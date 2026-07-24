"""Arming-checklist tests for sma_cross_gated (iter 67).

The gated twin must be: registered, NOT self-directing (that is its entire
point — the detector's trend filter must gate it), permitted in every
non-QUIET regime, mapped into the sigexit SMA family, and stamped with its
OWN PatternType member (the 06-27..07-15 mislabel lesson).
"""

from __future__ import annotations

from src.config import Direction, PatternType
from src.signal.exits import _SMA_FAMILY
from src.signal.patterns import SELF_DIRECTING_PATTERNS, detect_sma_cross, registry
from src.signal.regime import Regime, regime_permits_pattern
from tests.helpers.factories import make_candle, make_params


def _window(closes: list[float]) -> list:
    return [make_candle(close=c, ts=i * 3_600_000, timeframe="1h") for i, c in enumerate(closes)]


def _cross_up_closes() -> list[float]:
    """Closes where SMA9 crosses up through SMA21 on the final candle
    (30-bar downtrend, then a 5-bar +3/bar recovery: SMA9 93.94->95.22
    crosses SMA21 94.23->94.61 exactly on the last close)."""
    return [100.0 - 0.3 * i for i in range(30)] + [92.0 + 3.0 * i for i in range(5)]


class TestArmingChecklist:
    def test_registered(self):
        assert "sma_cross_gated" in registry

    def test_not_self_directing(self):
        assert "sma_cross_gated" not in SELF_DIRECTING_PATTERNS

    def test_regime_permitted_all_non_quiet(self):
        for regime in (Regime.TRENDING, Regime.VOLATILE, Regime.RANGING):
            assert regime_permits_pattern(regime, "sma_cross_gated"), regime
        assert not regime_permits_pattern(Regime.QUIET, "sma_cross_gated")

    def test_in_sigexit_sma_family(self):
        assert "sma_cross_gated" in _SMA_FAMILY

    def test_own_pattern_type(self):
        assert PatternType.SMA_CROSS_GATED.value == "sma_cross_gated"


class TestDetection:
    def test_mirrors_sma_cross_with_own_identity(self):
        p = make_params()
        candles = _window(_cross_up_closes())
        base = detect_sma_cross(candles, p)
        gated = registry["sma_cross_gated"](candles, p)
        assert base is not None and gated is not None
        assert gated.direction is base.direction is Direction.LONG
        assert gated.pattern is PatternType.SMA_CROSS_GATED
        assert gated.details["variant"] == "sma_cross_gated"
        assert gated.confidence == base.confidence

    def test_none_when_no_cross(self):
        p = make_params()
        candles = _window([100.0 + 0.5 * i for i in range(40)])
        assert registry["sma_cross_gated"](candles, p) is None
