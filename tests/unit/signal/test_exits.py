"""Unit tests for signal/exits.py — indicator-based exit rules (iter 65).

Semantics under test mirror the cross-era-validated backtest wrapper
(scripts/algo_search.py sigexit presets): STATE-based reversal (not
cross-based), RSI-extreme profit-take only in sigexit_rsi mode, None when
the mode is off or indicators can't be computed yet.
"""

from __future__ import annotations

from src.config import Direction
from src.signal.exits import REASON_INDICATOR_TP, REASON_SIGNAL_EXIT, indicator_exit_reason
from tests.helpers.factories import make_candle, make_params


def _window(closes, rsi_last=None):
    candles = [make_candle(close=c, ts=i * 3_600_000, timeframe="1h", rsi14=50.0) for i, c in enumerate(closes)]
    if rsi_last is not None:
        candles[-1] = make_candle(close=closes[-1], ts=(len(closes) - 1) * 3_600_000, timeframe="1h", rsi14=rsi_last)
    return candles


def _params(mode: str):
    return make_params(indicator_exit_mode=mode)


# Accelerating trends: a LINEAR ramp makes the MACD line converge onto its
# signal line (both tend to the same constant), leaving the >-state razor-thin.
# Quadratic acceleration keeps MACD decisively above (below) its signal.
_UP = [100.0 + 0.02 * i * i for i in range(60)]
_DOWN = [200.0 - 0.02 * i * i for i in range(60)]


class TestModeOff:
    def test_off_mode_never_exits(self):
        assert indicator_exit_reason(_window(_DOWN), Direction.LONG, "sma_cross", _params("")) is None

    def test_empty_window_is_none(self):
        assert indicator_exit_reason([], Direction.LONG, "sma_cross", _params("sigexit")) is None


class TestSmaCross:
    def test_long_exits_when_sma_state_down(self):
        r = indicator_exit_reason(_window(_DOWN), Direction.LONG, "sma_cross", _params("sigexit"))
        assert r == REASON_SIGNAL_EXIT

    def test_long_holds_while_sma_state_up(self):
        assert indicator_exit_reason(_window(_UP), Direction.LONG, "sma_cross", _params("sigexit")) is None

    def test_short_mirror(self):
        assert (
            indicator_exit_reason(_window(_UP), Direction.SHORT, "sma_cross", _params("sigexit")) == REASON_SIGNAL_EXIT
        )
        assert indicator_exit_reason(_window(_DOWN), Direction.SHORT, "sma_cross", _params("sigexit")) is None


class TestMacdFamily:
    def test_long_macd_exits_on_down_state(self):
        r = indicator_exit_reason(_window(_DOWN), Direction.LONG, "macd_cross", _params("sigexit"))
        assert r == REASON_SIGNAL_EXIT

    def test_long_macd_holds_on_up_state(self):
        assert indicator_exit_reason(_window(_UP), Direction.LONG, "macd_rsi", _params("sigexit")) is None

    def test_too_short_window_is_none(self):
        # Below macd_slow + macd_signal + 1 candles the state is uncomputable → hold.
        assert indicator_exit_reason(_window(_UP[:20]), Direction.LONG, "macd_cross", _params("sigexit")) is None


class TestCciMom:
    def test_long_exits_when_cci_negative(self):
        r = indicator_exit_reason(_window(_DOWN), Direction.LONG, "cci_mom", _params("sigexit"))
        assert r == REASON_SIGNAL_EXIT

    def test_short_exits_when_cci_positive(self):
        r = indicator_exit_reason(_window(_UP), Direction.SHORT, "cci_mom", _params("sigexit"))
        assert r == REASON_SIGNAL_EXIT


class TestEnsemble:
    def test_long_exits_when_agreement_decays(self):
        # Steady fall: macd down, sma down, cci negative, rsi 50 (not >50) → aligned 0 ≤ 1.
        r = indicator_exit_reason(_window(_DOWN), Direction.LONG, "ensemble_3of4", _params("sigexit"))
        assert r == REASON_SIGNAL_EXIT

    def test_long_holds_while_majority_agrees(self):
        w = _window(_UP, rsi_last=60.0)  # all four states up
        assert indicator_exit_reason(w, Direction.LONG, "ensemble_3of4", _params("sigexit")) is None


class TestRsiProfitTake:
    def test_sigexit_rsi_long_takes_profit_at_70(self):
        w = _window(_UP, rsi_last=71.0)
        r = indicator_exit_reason(w, Direction.LONG, "sma_cross", _params("sigexit_rsi"))
        assert r == REASON_INDICATOR_TP

    def test_plain_sigexit_ignores_rsi_extreme(self):
        w = _window(_UP, rsi_last=71.0)
        assert indicator_exit_reason(w, Direction.LONG, "sma_cross", _params("sigexit")) is None

    def test_sigexit_rsi_short_takes_profit_at_30(self):
        w = _window(_DOWN, rsi_last=29.0)
        r = indicator_exit_reason(w, Direction.SHORT, "sma_cross", _params("sigexit_rsi"))
        assert r == REASON_INDICATOR_TP

    def test_reversal_still_fires_in_rsi_mode(self):
        w = _window(_DOWN, rsi_last=50.0)
        r = indicator_exit_reason(w, Direction.LONG, "sma_cross", _params("sigexit_rsi"))
        assert r == REASON_SIGNAL_EXIT


class TestUnknownPattern:
    def test_unknown_pattern_never_exits(self):
        assert indicator_exit_reason(_window(_DOWN), Direction.LONG, "wick_rejection", _params("sigexit")) is None


class TestStatePatternExitMapping:
    """State patterns (iter 66) must map to the same family exit rules."""

    def test_macd_state_maps_to_macd_rule(self):
        r = indicator_exit_reason(_window(_DOWN), Direction.LONG, "macd_state", _params("sigexit"))
        assert r == REASON_SIGNAL_EXIT

    def test_sma_state_maps_to_sma_rule(self):
        r = indicator_exit_reason(_window(_DOWN), Direction.LONG, "sma_state", _params("sigexit"))
        assert r == REASON_SIGNAL_EXIT

    def test_cci_state_maps_to_cci_rule(self):
        r = indicator_exit_reason(_window(_UP), Direction.SHORT, "cci_state", _params("sigexit"))
        assert r == REASON_SIGNAL_EXIT

    def test_ensemble_state_maps_to_ensemble_rule(self):
        r = indicator_exit_reason(_window(_DOWN), Direction.LONG, "ensemble_state", _params("sigexit"))
        assert r == REASON_SIGNAL_EXIT
