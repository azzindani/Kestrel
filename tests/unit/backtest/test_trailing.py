"""Trailing-close parity tests for src/backtest/runner.py.

The backtest exit path must match execution/simulation.py: trailing replaces the
fixed TP, the stop ratchets with the candle's favourable extreme, and a single
candle can never both arm and trip the stop (no intra-candle look-ahead).
"""

from __future__ import annotations

from src.backtest.runner import _advance_trail_bt, _check_exit, _simulate_close
from tests.helpers.factories import make_candle


def _long_trade(**over):
    trade = {
        "direction": "long",
        "entry_price": 100.0,
        "sl_price": 98.0,
        "tp_price": 103.2,
        "liquidation_price": 95.5,
        "notional_usdt": 200.0,
        "size_usdt": 10.0,
        "fee_entry_usdt": 0.08,
        "entry_ts": 0,
        "timeframe": "5m",
        "trailing_enabled": True,
        "peak_price": 100.0,
        "trail_stop": None,
        "trail_activation_dist": 2.0,
        "trail_distance_dist": 1.0,
    }
    trade.update(over)
    return trade


def _short_trade(**over):
    return _long_trade(
        direction="short",
        sl_price=102.0,
        tp_price=96.8,
        liquidation_price=104.5,
        **over,
    )


def _c(high, low, close=None):
    close = close if close is not None else (high + low) / 2
    return make_candle(close=close, high=high, low=low)


class TestLongTrailingBacktest:
    def test_arms_then_exits_on_pullback(self):
        t = _long_trade()
        assert _check_exit(t, _c(101.5, 100.0)) is None  # not armed
        assert _check_exit(t, _c(103.0, 101.0)) is None  # arms; stop = 102.0
        assert t["trail_stop"] == 102.0
        assert _check_exit(t, _c(103.5, 101.8)) == "trailing_stop"

    def test_no_intra_candle_lookahead_on_arming_candle(self):
        # One candle whose high arms the trail (stop→109) and whose low (100)
        # is below that stop must NOT trip on the same candle.
        t = _long_trade()
        assert _check_exit(t, _c(110.0, 100.0)) is None
        assert t["trail_stop"] == 109.0
        # Next candle dipping to 108 trips it.
        assert _check_exit(t, _c(108.5, 108.0)) == "trailing_stop"

    def test_fixed_tp_ignored_when_trailing(self):
        t = _long_trade()
        # High punches well past the fixed tp (103.2) — no take_profit.
        assert _check_exit(t, _c(106.0, 104.0)) is None
        assert t["trail_stop"] == 105.0  # 106 − 1.0

    def test_unarmed_stop_loss_still_fires(self):
        t = _long_trade()
        assert _check_exit(t, _c(100.5, 100.1)) is None
        assert _check_exit(t, _c(99.0, 97.5)) == "stop_loss"

    def test_simulate_close_uses_trail_stop_level(self):
        t = _long_trade()
        _check_exit(t, _c(103.0, 101.0))  # arm, stop = 102.0
        res = _simulate_close(t, _c(102.5, 101.0), "trailing_stop")
        # Exit fills at the trail-stop level (102.0) minus exit slippage.
        assert res["exit_price"] < 102.0
        assert res["exit_price"] > 101.8
        assert res["pnl_net_usdt"] > 0.0  # locked a gain above entry


class TestShortTrailingBacktest:
    def test_arms_then_exits_on_bounce(self):
        t = _short_trade()
        assert _check_exit(t, _c(99.0, 97.0)) is None  # arms; stop = 98.0
        assert t["trail_stop"] == 98.0
        assert _check_exit(t, _c(99.5, 98.5)) == "trailing_stop"


class TestAdvanceTrailHelper:
    def test_trail_only_tightens(self):
        t = _long_trade(peak_price=104.0, trail_stop=103.0)
        _advance_trail_bt(t, high=103.0, low=102.0)  # lower high → no loosen
        assert t["trail_stop"] == 103.0
