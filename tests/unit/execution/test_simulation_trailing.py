"""Trailing-close tests for src/execution/simulation.py.

Trailing replaces the fixed take-profit when enabled: the stop arms once the
position is trail_activation_r×R in profit (R = initial stop distance), then
holds trail_distance_r×R below the running peak, exiting on reversal.
"""

from __future__ import annotations

import asyncio

from src.config import Direction
from src.execution.simulation import SimulationExecution
from tests.helpers.factories import make_app_config, make_params, make_signal


def _run(coro):
    return asyncio.run(coro)


def _sim(**param_overrides) -> SimulationExecution:
    # Long max_hold so the timeout branch never masks a trailing exit.
    params = make_params(
        trailing_enabled=True,
        trail_activation_r=1.0,
        trail_distance_r=0.5,
        max_hold_candles=50,
        **param_overrides,
    )
    return SimulationExecution(make_app_config(), params)


def _open_long(sim, entry=100.0, sl_offset=2.0, tp_offset=3.2):
    sig = make_signal(entry=entry, direction=Direction.LONG, sl_offset=sl_offset, tp_offset=tp_offset)
    _run(sim.place_order(sig))
    return sig


def _open_short(sim, entry=100.0, sl_offset=2.0, tp_offset=3.2):
    sig = make_signal(entry=entry, direction=Direction.SHORT, sl_offset=sl_offset, tp_offset=tp_offset)
    _run(sim.place_order(sig))
    return sig


def _step(sim, pair, price):
    sim.update_price(pair, price)
    return sim.check_exits(pair)


class TestPositionCarriesTrailingState:
    def test_place_order_stores_trailing_geometry(self):
        sim = _sim()
        sig = _open_long(sim)
        pos = sim._positions[sig.pair]
        assert pos["trailing_enabled"] is True
        assert pos["trail_stop"] is None
        # R = |fill − sl|; activation/distance are R-multiples of it.
        r_unit = abs(pos["entry_price"] - pos["sl_price"])
        assert pos["trail_activation_dist"] == round(1.0 * r_unit, 8)
        assert pos["trail_distance_dist"] == round(0.5 * r_unit, 8)

    def test_trailing_disabled_by_default(self):
        sim = SimulationExecution(make_app_config(), make_params())
        sig = _open_long(sim)
        assert sim._positions[sig.pair]["trailing_enabled"] is False


class TestLongTrailing:
    def test_arms_then_exits_on_pullback(self):
        sim = _sim()
        sig = _open_long(sim)  # entry≈100.05, sl=98, R≈2.05
        assert _step(sim, sig.pair, 102.0) is None  # not yet armed
        assert _step(sim, sig.pair, 103.0) is None  # arms here; stop ≈ 101.975
        assert _step(sim, sig.pair, 101.0) == "trailing_stop"

    def test_does_not_take_fixed_tp_when_trailing(self):
        sim = _sim()
        sig = _open_long(sim, tp_offset=3.2)  # fixed tp ≈ 103.2
        # Price blows past the old TP and keeps running — no take_profit.
        assert _step(sim, sig.pair, 104.0) is None
        assert _step(sim, sig.pair, 106.0) is None
        pos = sim._positions[sig.pair]
        assert pos["trail_stop"] is not None and pos["trail_stop"] > 103.2

    def test_trail_ratchets_up_and_locks_extended_gain(self):
        sim = _sim()
        sig = _open_long(sim)
        _step(sim, sig.pair, 104.0)
        stop_a = sim._positions[sig.pair]["trail_stop"]
        _step(sim, sig.pair, 107.0)
        stop_b = sim._positions[sig.pair]["trail_stop"]
        assert stop_b > stop_a  # trail only tightens upward
        # A reversal into the trail closes far above entry.
        assert _step(sim, sig.pair, stop_b - 0.01) == "trailing_stop"

    def test_unarmed_position_still_stops_out(self):
        sim = _sim()
        sig = _open_long(sim)  # sl = 98
        assert _step(sim, sig.pair, 100.5) is None  # never reaches activation
        assert _step(sim, sig.pair, 97.5) == "stop_loss"


class TestShortTrailing:
    def test_arms_then_exits_on_bounce(self):
        sim = _sim()
        sig = _open_short(sim)  # entry≈99.95, sl=102, R≈2.05
        assert _step(sim, sig.pair, 97.0) is None  # arms; stop ≈ 98.025
        assert _step(sim, sig.pair, 99.0) == "trailing_stop"

    def test_unarmed_short_still_stops_out(self):
        sim = _sim()
        sig = _open_short(sim)  # sl = 102
        assert _step(sim, sig.pair, 99.5) is None
        assert _step(sim, sig.pair, 102.5) == "stop_loss"
