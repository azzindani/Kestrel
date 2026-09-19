"""Unit tests for the iter-71 entries deployed to dev 2026-09-19.

vr_adaptive (variance-ratio regime switch), ker_trend (Kaufman efficiency ratio),
tsmom_z (volatility-scaled momentum) and turtle_soup (failed-breakout fade) — four
mechanisms the registry and the harness had never covered.

Locks down the arming checklist (a pattern missing any step is silently inert or
logs under a neighbour's name), each entry's direction logic, and that the two
threshold-crossing entries stay edge-triggered so a bot does not re-enter on every
candle of the same move.
"""

from __future__ import annotations

import math

import pytest

from src.config import Direction, PatternType
from src.signal.patterns import (
    SELF_DIRECTING_PATTERNS,
    _efficiency_ratio,
    detect_ker_trend,
    detect_tsmom_z,
    detect_turtle_soup,
    detect_vr_adaptive,
    registry,
)
from src.signal.regime import Regime, regime_permits_pattern
from tests.helpers.factories import make_candle, make_params

NEW = {
    "vr_adaptive": (detect_vr_adaptive, PatternType.VR_ADAPTIVE),
    "ker_trend": (detect_ker_trend, PatternType.KER_TREND),
    "tsmom_z": (detect_tsmom_z, PatternType.TSMOM_Z),
    "turtle_soup": (detect_turtle_soup, PatternType.TURTLE_SOUP),
}


def _params():
    return make_params(
        vr_lookback=96,
        vr_k=6,
        vr_band=0.25,
        vr_move_atr=1.0,
        ker_period=20,
        ker_min=0.5,
        tsmom_lookback=12,
        tsmom_vol_window=96,
        tsmom_z_min=2.0,
        soup_lookback=20,
    )


def _from_returns(returns, start=100.0, atr=None):
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * math.exp(r))
    candles = [make_candle(close=c, ts=i * 300_000) for i, c in enumerate(closes)]
    if atr is not None:
        last = candles[-1]
        candles[-1] = make_candle(close=last.close, ts=last.ts, atr14=atr)
    return candles


def _fires(detect, candles, params):
    """Indices (as prefix lengths) at which the pattern fires, walking the series."""
    return [n for n in range(2, len(candles) + 1) if detect(candles[:n], params) is not None]


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


class TestVrAdaptive:
    def test_trending_regime_follows_the_move(self):
        # Runs of 8 same-sign returns: positive autocorrelation -> VR well above 1.
        returns = [0.002 if (i // 8) % 2 == 0 else -0.002 for i in range(96)]
        returns[-6:] = [0.002] * 6
        got = detect_vr_adaptive(_from_returns(returns, atr=0.5), _params())
        assert got is not None and got.direction is Direction.LONG
        assert got.details["variant"] == "vr_adaptive_follow" and got.details["vr"] > 1.25

    def test_reverting_regime_fades_the_move(self):
        # Alternating returns: strong negative autocorrelation -> VR well below 1.
        returns = [0.004 if i % 2 == 0 else -0.004 for i in range(90)] + [0.003] * 6
        got = detect_vr_adaptive(_from_returns(returns, atr=0.5), _params())
        assert got is not None and got.direction is Direction.SHORT
        assert got.details["variant"] == "vr_adaptive_fade" and got.details["vr"] < 0.75

    def test_move_below_atr_threshold_is_ignored(self):
        returns = [0.002 if (i // 8) % 2 == 0 else -0.002 for i in range(96)]
        returns[-6:] = [0.002] * 6
        assert detect_vr_adaptive(_from_returns(returns, atr=50.0), _params()) is None

    def test_short_history_is_none(self):
        assert detect_vr_adaptive(_from_returns([0.001] * 20, atr=0.5), _params()) is None

    def test_missing_atr_is_none(self):
        returns = [0.002 if (i // 8) % 2 == 0 else -0.002 for i in range(96)]
        assert detect_vr_adaptive(_from_returns(returns), _params()) is None


class TestKerTrend:
    def test_efficiency_ratio_bounds(self):
        assert _efficiency_ratio([1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.0)
        assert _efficiency_ratio([1.0, 2.0, 1.0, 2.0, 1.0]) == pytest.approx(0.0)
        assert _efficiency_ratio([5.0, 5.0, 5.0]) is None

    # Edge-triggered: it fires on the candle the ratio CROSSES ker_min. As the rolling
    # window sheds old chop the ratio can dip back under for a candle and re-cross, so
    # a run may cross more than once — but never on two consecutive candles, and every
    # crossing points the run's way.
    @staticmethod
    def _assert_edge_fires(candles, direction):
        fires = _fires(detect_ker_trend, candles, _params())
        assert fires, "a clean run after chop must cross the threshold"
        assert all(b - a > 1 for a, b in zip(fires, fires[1:])), fires
        for n in fires:
            assert detect_ker_trend(candles[:n], _params()).direction is direction

    def test_clean_up_run_fires_long_on_crossings_only(self):
        chop = [0.003 if i % 2 == 0 else -0.003 for i in range(40)]
        self._assert_edge_fires(_from_returns(chop + [0.002] * 25), Direction.LONG)

    def test_clean_down_run_fires_short_on_crossings_only(self):
        chop = [0.003 if i % 2 == 0 else -0.003 for i in range(40)]
        self._assert_edge_fires(_from_returns(chop + [-0.002] * 25), Direction.SHORT)

    def test_sustained_clean_run_does_not_refire_every_candle(self):
        # Long straight run: once ER is above the threshold it stays there — one fire.
        chop = [0.003 if i % 2 == 0 else -0.003 for i in range(40)]
        candles = _from_returns(chop + [0.002] * 25)
        fires = _fires(detect_ker_trend, candles, _params())
        assert len(fires) < 25 // 2

    def test_pure_chop_never_fires(self):
        chop = [0.003 if i % 2 == 0 else -0.003 for i in range(60)]
        assert _fires(detect_ker_trend, _from_returns(chop), _params()) == []


class TestTsmomZ:
    def test_fires_once_on_an_outsized_move(self):
        noise = [0.001 if i % 2 == 0 else -0.001 for i in range(110)]
        candles = _from_returns(noise + [0.004] * 8)
        fires = _fires(detect_tsmom_z, candles, _params())
        assert len(fires) == 1
        got = detect_tsmom_z(candles[: fires[0]], _params())
        assert got is not None and got.direction is Direction.LONG and got.details["z"] >= 2.0

    def test_outsized_drop_is_short(self):
        noise = [0.001 if i % 2 == 0 else -0.001 for i in range(110)]
        candles = _from_returns(noise + [-0.004] * 8)
        fires = _fires(detect_tsmom_z, candles, _params())
        assert len(fires) == 1
        assert detect_tsmom_z(candles[: fires[0]], _params()).direction is Direction.SHORT

    def test_ordinary_noise_never_fires(self):
        noise = [0.001 if i % 2 == 0 else -0.001 for i in range(118)]
        assert _fires(detect_tsmom_z, _from_returns(noise), _params()) == []


class TestTurtleSoup:
    @staticmethod
    def _range(last):
        prior = [make_candle(close=100.0, high=101.0, low=99.0, ts=i * 300_000) for i in range(20)]
        return prior + [last]

    def test_failed_break_above_is_short(self):
        c = make_candle(close=100.5, high=102.0, low=100.0, open_=100.2, ts=20 * 300_000)
        got = detect_turtle_soup(self._range(c), _params())
        assert got is not None and got.direction is Direction.SHORT

    def test_failed_break_below_is_long(self):
        c = make_candle(close=99.5, high=100.0, low=98.0, open_=99.8, ts=20 * 300_000)
        got = detect_turtle_soup(self._range(c), _params())
        assert got is not None and got.direction is Direction.LONG

    def test_breakout_that_holds_is_not_faded(self):
        c = make_candle(close=101.5, high=102.0, low=100.5, open_=100.6, ts=20 * 300_000)
        assert detect_turtle_soup(self._range(c), _params()) is None

    def test_outside_candle_through_both_sides_is_ignored(self):
        c = make_candle(close=100.0, high=102.0, low=98.0, open_=100.0, ts=20 * 300_000)
        assert detect_turtle_soup(self._range(c), _params()) is None

    def test_short_history_is_none(self):
        assert detect_turtle_soup([make_candle(close=100.0)] * 5, _params()) is None
