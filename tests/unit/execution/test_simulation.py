"""Unit tests for src/execution/simulation.py (Layer 3 boundary — tested without I/O)."""

from __future__ import annotations

import asyncio

import pytest

from src.config import Direction
from src.execution.interface import ExecutionError
from src.execution.simulation import SimulationExecution
from tests.helpers.factories import make_app_config, make_signal


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestSimulationInit:
    def test_init_positions_dict_is_empty(self):
        sim = SimulationExecution(make_app_config())
        assert sim._positions == {}

    def test_init_prices_dict_is_empty(self):
        sim = SimulationExecution(make_app_config())
        assert sim._prices == {}

    def test_init_both_dicts_present(self):
        sim = SimulationExecution(make_app_config())
        assert hasattr(sim, "_positions")
        assert hasattr(sim, "_prices")

    def test_init_cfg_stored(self):
        cfg = make_app_config()
        sim = SimulationExecution(cfg)
        assert sim.cfg is cfg


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------


class TestPlaceOrder:
    def test_place_order_long_returns_dict_with_direction(self):
        sim = SimulationExecution(make_app_config())
        order = _run(sim.place_order(make_signal(direction=Direction.LONG)))
        assert order["direction"] == "long"

    def test_place_order_long_adds_slippage_to_entry(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal(entry=83000.0, direction=Direction.LONG)
        order = _run(sim.place_order(sig))
        assert order["entry_price"] > 83000.0

    def test_place_order_short_subtracts_slippage_from_entry(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal(entry=83000.0, direction=Direction.SHORT)
        order = _run(sim.place_order(sig))
        assert order["entry_price"] < 83000.0

    def test_place_order_long_liquidation_below_entry(self):
        sim = SimulationExecution(make_app_config())
        order = _run(sim.place_order(make_signal(direction=Direction.LONG)))
        assert order["liquidation_price"] < order["entry_price"]

    def test_place_order_short_liquidation_above_entry(self):
        sim = SimulationExecution(make_app_config())
        order = _run(sim.place_order(make_signal(direction=Direction.SHORT)))
        assert order["liquidation_price"] > order["entry_price"]

    def test_place_order_fee_is_positive(self):
        sim = SimulationExecution(make_app_config())
        order = _run(sim.place_order(make_signal()))
        assert order["fee_usdt"] > 0.0

    def test_place_order_notional_equals_size_times_leverage(self):
        cfg = make_app_config(leverage=20)
        sim = SimulationExecution(cfg)
        sig = make_signal(size_usdt=10.0)
        order = _run(sim.place_order(sig))
        assert order["notional_usdt"] == pytest.approx(200.0, abs=0.01)

    def test_place_order_adds_position_to_state(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal()
        _run(sim.place_order(sig))
        assert sig.pair in sim._positions

    def test_place_order_duplicate_raises_execution_error(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal()
        _run(sim.place_order(sig))
        with pytest.raises(ExecutionError):
            _run(sim.place_order(sig))

    def test_place_order_returns_all_required_fields(self):
        sim = SimulationExecution(make_app_config())
        order = _run(sim.place_order(make_signal()))
        for key in (
            "order_id",
            "pair",
            "direction",
            "entry_price",
            "fee_usdt",
            "notional_usdt",
            "liquidation_price",
            "tp_price",
            "sl_price",
        ):
            assert key in order


# ---------------------------------------------------------------------------
# get_position
# ---------------------------------------------------------------------------


class TestGetPosition:
    def test_get_position_returns_none_when_empty(self):
        sim = SimulationExecution(make_app_config())
        result = _run(sim.get_position("BTCUSDT"))
        assert result is None

    def test_get_position_returns_position_after_order(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal()
        _run(sim.place_order(sig))
        result = _run(sim.get_position(sig.pair))
        assert result is not None
        assert result["pair"] == sig.pair


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------


class TestCancelOrder:
    def test_cancel_order_always_returns_false_simulation(self):
        sim = SimulationExecution(make_app_config())
        result = _run(sim.cancel_order("any-id", "BTCUSDT"))
        assert result is False


# ---------------------------------------------------------------------------
# close_position
# ---------------------------------------------------------------------------


class TestClosePosition:
    def test_close_position_nonexistent_raises_execution_error(self):
        sim = SimulationExecution(make_app_config())
        with pytest.raises(ExecutionError):
            _run(sim.close_position("BTCUSDT", "manual"))

    def test_close_position_removes_from_positions(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal()
        _run(sim.place_order(sig))
        sim.update_price(sig.pair, sig.tp_price + 1.0)
        _run(sim.close_position(sig.pair, "take_profit"))
        assert sig.pair not in sim._positions

    def test_close_position_long_at_tp_has_positive_pnl(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal(entry=83000.0, direction=Direction.LONG, tp_offset=336.0, sl_offset=210.0)
        _run(sim.place_order(sig))
        sim.update_price(sig.pair, sig.tp_price + 1.0)
        result = _run(sim.close_position(sig.pair, "take_profit"))
        assert result["pnl_net_usdt"] > 0.0

    def test_close_position_long_at_sl_has_negative_pnl(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal(entry=83000.0, direction=Direction.LONG, tp_offset=336.0, sl_offset=210.0)
        _run(sim.place_order(sig))
        sim.update_price(sig.pair, sig.sl_price - 1.0)
        result = _run(sim.close_position(sig.pair, "stop_loss"))
        assert result["pnl_net_usdt"] < 0.0

    def test_close_position_returns_required_fields(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal()
        _run(sim.place_order(sig))
        sim.update_price(sig.pair, sig.entry_price)
        result = _run(sim.close_position(sig.pair, "manual"))
        for key in ("exit_price", "pnl_gross_usdt", "fee_exit_usdt", "pnl_net_usdt", "pnl_pct"):
            assert key in result

    def test_close_position_uses_entry_price_fallback_without_update(self):
        """If update_price not called, entry price used as fallback."""
        sim = SimulationExecution(make_app_config())
        sig = make_signal()
        _run(sim.place_order(sig))
        # Don't call update_price
        result = _run(sim.close_position(sig.pair, "manual"))
        assert result is not None

    def test_close_position_short_at_tp_has_positive_pnl(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal(entry=83000.0, direction=Direction.SHORT, tp_offset=336.0, sl_offset=210.0)
        _run(sim.place_order(sig))
        sim.update_price(sig.pair, sig.tp_price - 1.0)
        result = _run(sim.close_position(sig.pair, "take_profit"))
        assert result["pnl_net_usdt"] > 0.0


# ---------------------------------------------------------------------------
# check_exits
# ---------------------------------------------------------------------------


class TestCheckExits:
    def test_check_exits_no_position_returns_none(self):
        sim = SimulationExecution(make_app_config())
        assert sim.check_exits("BTCUSDT") is None

    def test_check_exits_no_price_recorded_returns_none(self):
        sim = SimulationExecution(make_app_config())
        _run(sim.place_order(make_signal()))
        assert sim.check_exits("BTCUSDT") is None

    def test_check_exits_long_tp_hit_returns_take_profit(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal(direction=Direction.LONG)
        _run(sim.place_order(sig))
        sim.update_price(sig.pair, sig.tp_price + 1.0)
        assert sim.check_exits(sig.pair) == "take_profit"

    def test_check_exits_long_sl_hit_returns_stop_loss(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal(direction=Direction.LONG)
        _run(sim.place_order(sig))
        sim.update_price(sig.pair, sig.sl_price - 1.0)
        assert sim.check_exits(sig.pair) == "stop_loss"

    def test_check_exits_long_in_range_returns_none(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal(entry=83000.0, direction=Direction.LONG)
        _run(sim.place_order(sig))
        sim.update_price(sig.pair, sig.entry_price + 10.0)
        assert sim.check_exits(sig.pair) is None

    def test_check_exits_short_tp_hit_returns_take_profit(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal(direction=Direction.SHORT)
        _run(sim.place_order(sig))
        sim.update_price(sig.pair, sig.tp_price - 1.0)
        assert sim.check_exits(sig.pair) == "take_profit"

    def test_check_exits_short_sl_hit_returns_stop_loss(self):
        sim = SimulationExecution(make_app_config())
        sig = make_signal(direction=Direction.SHORT)
        _run(sim.place_order(sig))
        sim.update_price(sig.pair, sig.sl_price + 1.0)
        assert sim.check_exits(sig.pair) == "stop_loss"


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


class TestReconcile:
    def test_reconcile_empty_returns_empty_list(self):
        sim = SimulationExecution(make_app_config())
        result = _run(sim.reconcile())
        assert result == []

    def test_reconcile_with_open_position_returns_list_of_one(self):
        sim = SimulationExecution(make_app_config())
        _run(sim.place_order(make_signal()))
        result = _run(sim.reconcile())
        assert len(result) == 1
