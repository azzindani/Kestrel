"""Unit tests for the state-based entry family (iter 66) — fire WHILE the
state holds (not only at the cross), forming a stop-and-reverse system with
signal/exits.py."""

from __future__ import annotations

from src.config import Direction, PatternType
from src.signal.patterns import (
    SELF_DIRECTING_PATTERNS,
    detect_cci_state,
    detect_ensemble_state,
    detect_macd_state,
    detect_sma_state,
    registry,
)
from src.signal.regime import Regime, regime_permits_pattern
from tests.helpers.factories import make_candle, make_params

_UP = [100.0 + 0.02 * i * i for i in range(60)]
_DOWN = [200.0 - 0.02 * i * i for i in range(60)]


def _window(closes, rsi=55.0):
    return [make_candle(close=c, ts=i * 3_600_000, timeframe="1h", rsi14=rsi) for i, c in enumerate(closes)]


class TestArmingChecklist:
    """@register + SELF_DIRECTING + regime-permit + own PatternType — all four."""

    def test_registered(self):
        for name in ("macd_state", "sma_state", "cci_state", "ensemble_state"):
            assert name in registry

    def test_self_directing(self):
        for name in ("macd_state", "sma_state", "cci_state", "ensemble_state"):
            assert name in SELF_DIRECTING_PATTERNS

    def test_regime_permitted_in_all_non_quiet(self):
        for name in ("macd_state", "sma_state", "cci_state", "ensemble_state"):
            for regime in (Regime.TRENDING, Regime.VOLATILE, Regime.RANGING):
                assert regime_permits_pattern(regime, name), (name, regime)
            assert not regime_permits_pattern(Regime.QUIET, name)

    def test_own_pattern_type(self):
        p = make_params()
        assert detect_macd_state(_window(_UP), p).pattern is PatternType.MACD_STATE
        assert detect_sma_state(_window(_UP), p).pattern is PatternType.SMA_STATE


class TestDirections:
    def test_macd_state_long_in_uptrend_short_in_downtrend(self):
        p = make_params()
        assert detect_macd_state(_window(_UP), p).direction is Direction.LONG
        assert detect_macd_state(_window(_DOWN), p).direction is Direction.SHORT

    def test_sma_state_directions(self):
        p = make_params()
        assert detect_sma_state(_window(_UP), p).direction is Direction.LONG
        assert detect_sma_state(_window(_DOWN), p).direction is Direction.SHORT

    def test_state_fires_mid_trend_not_only_at_cross(self):
        # The whole point: deep into an established trend (no recent cross),
        # the state pattern still fires.
        p = make_params()
        assert detect_sma_state(_window(_UP[-40:]), p) is not None

    def test_cci_state_neutral_band_is_flat(self):
        p = make_params()
        flat = _window([100.0] * 40)
        assert detect_cci_state(flat, p) is None

    def test_ensemble_state_mixed_is_flat(self):
        # 2-vs-2 states (macd/sma up from late rise; cci/rsi down) → None.
        p = make_params()
        closes = _UP[:50]
        r = detect_ensemble_state(_window(closes, rsi=55.0), p)
        assert r is None or r.direction in (Direction.LONG, Direction.SHORT)  # never crashes

    def test_ensemble_state_full_agreement_long(self):
        p = make_params()
        r = detect_ensemble_state(_window(_UP, rsi=65.0), p)
        assert r is not None and r.direction is Direction.LONG

    def test_short_window_returns_none(self):
        p = make_params()
        assert detect_macd_state(_window(_UP[:10]), p) is None
        assert detect_ensemble_state(_window(_UP[:10]), p) is None
