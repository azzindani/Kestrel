"""Unit tests for the iter-73 entries deployed to dev 2026-09-19.

engulf_rev (engulfing reversal at a local extreme), obv_div (volume breaks out before
price) and fvg_retest (first retest of a fair-value gap). Locks down the arming
checklist and each entry's geometry, including the "first retest only" rule that
keeps fvg_retest from re-trading a gap price has already visited.
"""

from __future__ import annotations

import pytest

from src.config import Direction, PatternType
from src.signal.patterns import (
    SELF_DIRECTING_PATTERNS,
    _obv,
    detect_engulf_rev,
    detect_fvg_retest,
    detect_obv_div,
    registry,
)
from src.signal.regime import Regime, regime_permits_pattern
from tests.helpers.factories import make_candle, make_params

NEW = {
    "engulf_rev": (detect_engulf_rev, PatternType.ENGULF_REV),
    "obv_div": (detect_obv_div, PatternType.OBV_DIV),
    "fvg_retest": (detect_fvg_retest, PatternType.FVG_RETEST),
}


def _params():
    return make_params(engulf_run=3, obv_lookback=20, fvg_max_age=12, fvg_min_atr=0.3)


def _c(o, h, lo, c, i, v=100.0, atr=None):
    extra = {"atr14": atr} if atr is not None else {}
    return make_candle(open_=o, high=h, low=lo, close=c, volume=v, ts=i * 300_000, **extra)


@pytest.mark.parametrize("name", sorted(NEW))
class TestArmingChecklist:
    def test_registered(self, name):
        assert registry.get(name) is NEW[name][0]

    def test_self_directing(self, name):
        assert name in SELF_DIRECTING_PATTERNS

    def test_permitted_in_all_non_quiet_regimes(self, name):
        for regime in (Regime.TRENDING, Regime.VOLATILE, Regime.RANGING):
            assert regime_permits_pattern(regime, name), regime

    def test_blocked_in_quiet(self, name):
        assert not regime_permits_pattern(Regime.QUIET, name)

    def test_has_own_pattern_type(self, name):
        assert NEW[name][1].value == name


class TestEngulfRev:
    @staticmethod
    def _rising_then(last):
        run = [_c(100 + i, 101 + i, 99.5 + i, 100.8 + i, i) for i in range(4)]  # up candles
        prev = _c(104.0, 105.2, 103.9, 105.0, 4)  # up candle, highest close
        return run + [prev, last]

    def test_bearish_engulfing_at_local_high_is_short(self):
        cur = _c(105.1, 105.3, 103.5, 103.8, 5)  # opens above prev close, closes below prev open
        got = detect_engulf_rev(self._rising_then(cur), _params())
        assert got is not None and got.direction is Direction.SHORT

    def test_bullish_engulfing_at_local_low_is_long(self):
        run = [_c(110 - i, 110.5 - i, 109 - i, 109.2 - i, i) for i in range(4)]  # down candles
        prev = _c(106.0, 106.1, 104.8, 105.0, 4)  # down candle, lowest close
        cur = _c(104.9, 106.6, 104.7, 106.4, 5)
        got = detect_engulf_rev(run + [prev, cur], _params())
        assert got is not None and got.direction is Direction.LONG

    def test_engulfing_away_from_the_extreme_is_ignored(self):
        run = [_c(110, 111, 109, 110.5, i) for i in range(4)]  # closes above prev's close
        prev = _c(104.0, 105.2, 103.9, 105.0, 4)
        cur = _c(105.1, 105.3, 103.5, 103.8, 5)
        assert detect_engulf_rev(run + [prev, cur], _params()) is None

    def test_partial_engulf_is_ignored(self):
        cur = _c(105.1, 105.3, 104.2, 104.5, 5)  # closes above prev open 104.0
        assert detect_engulf_rev(self._rising_then(cur), _params()) is None


class TestObvDiv:
    def test_obv_signs_volume_by_close_change(self):
        cs = [_c(10, 11, 9, 10, 0, v=5), _c(10, 11, 9, 11, 1, v=7), _c(11, 12, 10, 10, 2, v=3)]
        assert _obv(cs) == [0.0, 7.0, 4.0]

    def test_volume_breakout_before_price_is_long(self):
        # Price peaks early at 110 then ranges lower; up-candles carry heavy volume, down
        # candles light, so OBV climbs to a new high while close stays under 110.
        cs = [_c(100, 101, 99, 100, i) for i in range(3)]
        cs.append(_c(100, 110.5, 100, 110, 3, v=10))  # the price high, inside the 20-candle window
        for i in range(4, 24):
            up = i % 2 == 1  # ends on an up candle carrying heavy volume
            close = 104.0 if up else 103.0
            cs.append(_c(103.5, 104.5, 102.5, close, i, v=50 if up else 10))
        got = detect_obv_div(cs, _params())
        assert got is not None and got.direction is Direction.LONG

    def test_price_at_its_own_high_is_not_a_divergence(self):
        cs = [_c(100 + i, 101 + i, 99 + i, 100.5 + i, i, v=100) for i in range(23)]  # both make highs
        assert detect_obv_div(cs, _params()) is None


class TestFvgRetest:
    @staticmethod
    def _bullish_gap(tail):
        base = [_c(100, 100.5, 99.5, 100, i) for i in range(12)]
        c0 = _c(100, 100.6, 99.8, 100.4, 12)  # high 100.6
        c1 = _c(100.4, 102.8, 100.3, 102.6, 13)  # impulse
        c2 = _c(102.6, 103.4, 101.6, 103.2, 14)  # low 101.6 > 100.6 -> gap [100.6, 101.6]
        return base + [c0, c1, c2] + tail

    def test_first_retest_that_holds_is_long(self):
        cs = self._bullish_gap([_c(103.2, 103.5, 102.2, 102.5, 15), _c(102.5, 102.6, 101.2, 101.9, 16, atr=1.0)])
        got = detect_fvg_retest(cs, _params())
        assert got is not None and got.direction is Direction.LONG and got.details["zone"] == [100.6, 101.6]

    def test_close_through_the_gap_is_not_a_hold(self):
        cs = self._bullish_gap([_c(103.2, 103.5, 102.2, 102.5, 15), _c(102.5, 102.6, 100.2, 100.4, 16, atr=1.0)])
        assert detect_fvg_retest(cs, _params()) is None

    def test_second_retest_is_ignored(self):
        cs = self._bullish_gap(
            [
                _c(103.2, 103.5, 101.3, 102.5, 15),  # first touch of the zone
                _c(102.5, 102.9, 102.0, 102.7, 16),
                _c(102.7, 102.8, 101.2, 101.9, 17, atr=1.0),  # second touch
            ]
        )
        assert detect_fvg_retest(cs, _params()) is None

    def test_hairline_gap_is_ignored(self):
        cs = self._bullish_gap(
            [_c(103.2, 103.5, 102.2, 102.5, 15), _c(102.5, 102.6, 101.2, 101.9, 16, atr=10.0)]
        )  # gap 1.0 < 0.3 x ATR 10
        assert detect_fvg_retest(cs, _params()) is None

    def test_bearish_gap_retest_is_short(self):
        base = [_c(110, 110.5, 109.5, 110, i) for i in range(12)]
        c0 = _c(110, 110.2, 109.4, 109.6, 12)  # low 109.4
        c1 = _c(109.6, 109.7, 107.2, 107.4, 13)
        c2 = _c(107.4, 108.4, 106.6, 106.8, 14)  # high 108.4 < 109.4 -> gap [108.4, 109.4]
        tail = [_c(106.8, 107.8, 106.5, 107.5, 15), _c(107.5, 108.8, 107.4, 108.1, 16, atr=1.0)]
        got = detect_fvg_retest(base + [c0, c1, c2] + tail, _params())
        assert got is not None and got.direction is Direction.SHORT

    def test_missing_atr_is_none(self):
        cs = self._bullish_gap([_c(103.2, 103.5, 102.2, 102.5, 15), _c(102.5, 102.6, 101.2, 101.9, 16)])
        assert detect_fvg_retest(cs, _params()) is None
