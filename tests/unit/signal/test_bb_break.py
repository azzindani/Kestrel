"""Unit tests for bb_break — the high-win-rate entry registered 2026-08-24.

Selected off a 480-backtest 5m sweep as the highest-win cell of 24 (71.8% @
hiwin33, n=2544) to give the owner's lab/staging "high win rate" tiers a live
pattern that actually produces one. It delivers the WIN RATE, not an edge —
still net-negative, nothing prod-eligible.

Two things matter here. First the four-step arming checklist (§9 + the
2026-06-27 mislabel): a pattern missing any one of @register /
SELF_DIRECTING_PATTERNS / regime_permits_pattern / its own PatternType member
fires never or logs under a neighbour's name — that combination silently cost
the MACD cohorts seven iterations. Second, the entry must stay STATE-based and
match scripts/algo_search.py's `_bb_break` exactly; an edge-triggered variant
would be a different, unvalidated signal with none of the measured win rate.
"""

from __future__ import annotations

from src.config import Direction, PatternType
from src.signal.patterns import SELF_DIRECTING_PATTERNS, detect_bb_break, registry
from src.signal.regime import Regime, regime_permits_pattern
from tests.helpers.factories import make_candle, make_params


def _candle(close, upper=110.0, lower=90.0):
    return make_candle(close=close, bb_upper=upper, bb_lower=lower)


class TestArmingChecklist:
    """All four steps — miss one and the pattern is silently inert."""

    def test_registered(self):
        assert registry.get("bb_break") is detect_bb_break

    def test_self_directing(self):
        # The band side supplies the direction, so it must bypass the trend gate.
        assert "bb_break" in SELF_DIRECTING_PATTERNS

    def test_permitted_in_all_non_quiet_regimes(self):
        for regime in (Regime.TRENDING, Regime.VOLATILE, Regime.RANGING):
            assert regime_permits_pattern(regime, "bb_break"), regime

    def test_blocked_in_quiet(self):
        assert not regime_permits_pattern(Regime.QUIET, "bb_break")

    def test_has_own_pattern_type(self):
        # Borrowing a neighbour's label pools unrelated patterns in pattern_memory.
        assert PatternType.BB_BREAK.value == "bb_break"


class TestDirection:
    def test_close_above_upper_is_long(self):
        got = detect_bb_break([_candle(111.0)], make_params())
        assert got is not None and got.direction is Direction.LONG

    def test_close_below_lower_is_short(self):
        got = detect_bb_break([_candle(89.0)], make_params())
        assert got is not None and got.direction is Direction.SHORT

    def test_inside_band_does_not_fire(self):
        assert detect_bb_break([_candle(100.0)], make_params()) is None

    def test_exactly_on_upper_does_not_fire(self):
        # Strict `>` matches algo_search; touching the band is not a break.
        assert detect_bb_break([_candle(110.0)], make_params()) is None

    def test_exactly_on_lower_does_not_fire(self):
        assert detect_bb_break([_candle(90.0)], make_params()) is None

    def test_labels_its_own_pattern_type(self):
        got = detect_bb_break([_candle(111.0)], make_params())
        assert got is not None and got.pattern is PatternType.BB_BREAK


class TestStateNotEdge:
    """The validated form fires WHILE price sits outside the band."""

    def test_fires_on_consecutive_candles_outside_band(self):
        params = make_params()
        window = [_candle(111.0), _candle(112.0), _candle(113.0)]
        # Every suffix ending outside the band must fire, not just the first break.
        for i in range(1, len(window) + 1):
            got = detect_bb_break(window[:i], params)
            assert got is not None and got.direction is Direction.LONG, i


class TestAbsentIndicators:
    """Explicit absence — bands are None until the BB window warms up."""

    def test_no_candles(self):
        assert detect_bb_break([], make_params()) is None

    def test_upper_none(self):
        c = make_candle(close=111.0, bb_upper=None, bb_lower=90.0)
        assert detect_bb_break([c], make_params()) is None

    def test_lower_none(self):
        c = make_candle(close=89.0, bb_upper=110.0, bb_lower=None)
        assert detect_bb_break([c], make_params()) is None
