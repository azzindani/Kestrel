"""Perp funding model in SimulationExecution (CLAUDE.md §13/§29 v2.7).

Conservative always-charged: cost = notional × rate/100 × hold_hours/8.
Off by default (rate 0.0) so dev's raw-strategy forward test is unchanged;
staging sets FUNDING_RATE_8H_PCT to model the perp venue.
"""

from __future__ import annotations

import pytest

from src.execution.simulation import SimulationExecution, _funding_cost
from tests.helpers.factories import make_app_config, make_params, make_signal


class TestFundingCost:
    def test_zero_rate_charges_nothing(self):
        cfg = make_app_config(funding_rate_8h_pct=0.0)
        assert _funding_cost(cfg, notional=200.0, candles_held=6) == 0.0

    def test_one_hour_candles_charge_pro_rata(self):
        # 0.01%/8h on $200 notional held 4×1h candles = 200 × 0.0001 × 4/8
        cfg = make_app_config(funding_rate_8h_pct=0.01, timeframe_entry="1h")
        assert _funding_cost(cfg, notional=200.0, candles_held=4) == pytest.approx(0.01)

    def test_five_minute_candles_use_timeframe_hours(self):
        # 6×5m = 0.5h → 200 × 0.0001 × 0.5/8
        cfg = make_app_config(funding_rate_8h_pct=0.01, timeframe_entry="5m")
        assert _funding_cost(cfg, notional=200.0, candles_held=6) == pytest.approx(0.00125)


class TestFundingInClose:
    async def test_close_deducts_funding_and_records_it_in_exit_fee(self):
        cfg = make_app_config(funding_rate_8h_pct=0.01, timeframe_entry="1h", maker_execution=True)
        sim = SimulationExecution(cfg, make_params())
        sig = make_signal(entry=100.0, tp_offset=0.6, sl_offset=1.2)
        await sim.place_order(sig)
        pos = sim._positions[sig.pair]
        pos["candles_held"] = 4
        sim.update_price(sig.pair, sig.tp_price)

        result = await sim.close_position(sig.pair, "take_profit")

        notional = pos["notional_usdt"] if sig.pair in sim._positions else result["notional_usdt"]
        expected_funding = notional * 0.0001 * (4 / 8.0)
        maker_exit_fee = notional * 0.0002
        assert result["fee_exit_usdt"] == pytest.approx(maker_exit_fee + expected_funding, rel=1e-6)

    async def test_zero_rate_close_matches_legacy_fees(self):
        cfg = make_app_config(funding_rate_8h_pct=0.0, maker_execution=True)
        sim = SimulationExecution(cfg, make_params())
        sig = make_signal(entry=100.0, tp_offset=0.6, sl_offset=1.2)
        await sim.place_order(sig)
        notional = sim._positions[sig.pair]["notional_usdt"]
        sim._positions[sig.pair]["candles_held"] = 4
        sim.update_price(sig.pair, sig.tp_price)

        result = await sim.close_position(sig.pair, "take_profit")

        assert result["fee_exit_usdt"] == pytest.approx(notional * 0.0002, rel=1e-6)
