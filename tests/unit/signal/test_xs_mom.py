"""Unit tests for xs_mom — the multi-week cross-pair momentum cohort deployed to dev 2026-10-03.

The daemon ranks XS_MOM_UNIVERSE once a day and attaches the pair's group to the decision
candle; the pure pattern reads it, and the group-exit rule closes a position whose pair
has left its group. Locks down the arming checklist, the group arithmetic the backtest
measured (scripts/backtest_xs_momentum.bot_trades — checked here against its percentile
rank on random cross-sections), the once-a-day decision candle, and the exit rule.
"""

from __future__ import annotations

import json
import pathlib
import random
import sys

import numpy as np
import pytest

from src.config import XS_MOM_UNIVERSE, Direction, PatternType
from src.signal.detector import compute_exit_prices
from src.signal.exits import REASON_SIGNAL_EXIT, indicator_exit_reason
from src.signal.patterns import (
    SELF_DIRECTING_PATTERNS,
    cross_section_group,
    detect_xs_mom,
    registry,
    xs_mom_decision_candle,
)
from src.signal.regime import Regime, regime_permits_pattern
from tests.helpers.factories import make_candle, make_params

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCRIPTS = _ROOT / "scripts"
_H = 3_600_000


def _snap(returns: dict[str, float]) -> dict[str, tuple[float, float]]:
    """(close_now, close_then) with close_then = 100, so each pair's return is `returns[pair]`."""
    return {p: (100.0 * (1.0 + r), 100.0) for p, r in returns.items()}


def _ladder(n: int) -> dict[str, tuple[float, float]]:
    """n pairs P01..Pn ordered from weakest (P01) to strongest return."""
    return _snap({f"P{i:02d}": 0.01 * i for i in range(1, n + 1)})


class TestArmingChecklist:
    def test_registered(self):
        assert registry.get("xs_mom") is detect_xs_mom

    def test_self_directing(self):
        assert "xs_mom" in SELF_DIRECTING_PATTERNS

    def test_permitted_in_all_non_quiet_regimes(self):
        for regime in (Regime.TRENDING, Regime.VOLATILE, Regime.RANGING):
            assert regime_permits_pattern(regime, "xs_mom"), regime

    def test_blocked_in_quiet(self):
        assert not regime_permits_pattern(Regime.QUIET, "xs_mom")

    def test_has_own_pattern_type(self):
        assert PatternType.XS_MOM.value == "xs_mom"

    def test_universe_is_the_backtested_scalp_pairs(self):
        sys.path.insert(0, str(_SCRIPTS))
        from build_momentum_lab import SCALP_PAIRS

        assert list(XS_MOM_UNIVERSE) == list(SCALP_PAIRS)
        assert len(set(XS_MOM_UNIVERSE)) == 34

    def test_params_carry_the_full_contract(self):
        contract = json.loads((_ROOT / "params.json").read_text())
        for key in ("xs_mom_lookback", "xs_mom_groups", "xs_mom_eval_hour"):
            spec = contract[key]
            assert {"value", "type", "range", "description", "impact"} <= set(spec), key
            lo, hi = spec["range"]
            assert lo <= spec["value"] <= hi, key
            assert getattr(make_params(), key) == spec["value"], key


class TestCrossSectionGroup:
    def test_thirds_of_34_are_11_laggards_and_12_leaders(self):
        closes = _ladder(34)
        groups = {p: cross_section_group(p, closes, 3, min_pairs=28) for p in closes}
        assert [p for p, g in groups.items() if g == -1] == [f"P{i:02d}" for i in range(1, 12)]
        assert [p for p, g in groups.items() if g == 1] == [f"P{i:02d}" for i in range(23, 35)]
        assert sum(1 for g in groups.values() if g == 0) == 11

    def test_matches_the_backtest_percentile_rank(self):
        """Same groups as the backtest's bot replay, for every universe size and divisor."""
        sys.path.insert(0, str(_SCRIPTS))
        from backtest_xs_momentum import group_sides

        rng = random.Random(75)
        for groups in (2, 3, 4, 5):
            for n in range(10, 41):
                rets = {f"P{i:02d}": rng.uniform(-0.5, 0.5) for i in range(n)}
                want = group_sides(np.array(list(rets.values())), 1.0 / groups)
                got = [cross_section_group(p, _snap(rets), groups, min_pairs=10) for p in rets]
                assert got == [int(w) for w in want], (groups, n)

    def test_below_quorum_is_none(self):
        closes = _ladder(20)
        assert cross_section_group("P20", closes, 3, min_pairs=28) is None

    def test_pair_without_a_return_is_none(self):
        closes = _ladder(34)
        assert cross_section_group("MISSING", closes, 3, min_pairs=28) is None

    def test_non_positive_closes_are_not_ranked(self):
        closes = _ladder(30)
        closes["BAD"] = (0.0, 100.0)
        assert cross_section_group("BAD", closes, 3, min_pairs=28) is None
        assert cross_section_group("P30", closes, 3, min_pairs=28) == 1  # still 30 ranked pairs

    def test_fewer_than_two_groups_is_none(self):
        assert cross_section_group("P01", _ladder(34), 1, min_pairs=28) is None

    def test_halves_split_everyone(self):
        closes = _ladder(10)
        groups = [cross_section_group(p, closes, 2, min_pairs=10) for p in closes]
        assert groups == [-1] * 5 + [1] * 5


class TestDecisionCandle:
    def test_only_the_candle_opening_at_the_eval_hour(self):
        day = 20_000 * 24 * _H
        assert xs_mom_decision_candle(day + 12 * _H, 12)
        for hour in (0, 4, 8, 16, 20):
            assert not xs_mom_decision_candle(day + hour * _H, 12), hour

    def test_once_per_day_on_4h_candles(self):
        day = 20_000 * 24 * _H
        opens = [day + i * 4 * _H for i in range(6 * 7)]
        assert sum(xs_mom_decision_candle(t, 12) for t in opens) == 7


class TestPattern:
    def test_leader_is_long(self):
        res = detect_xs_mom([make_candle(xs_group=1)], make_params())
        assert res is not None and res.direction is Direction.LONG and res.pattern is PatternType.XS_MOM

    def test_laggard_is_short(self):
        res = detect_xs_mom([make_candle(xs_group=-1)], make_params())
        assert res is not None and res.direction is Direction.SHORT

    @pytest.mark.parametrize("group", [0, None])
    def test_flat_outside_the_groups_and_off_the_decision_candle(self, group):
        assert detect_xs_mom([make_candle(xs_group=group)], make_params()) is None

    def test_empty_window_is_none(self):
        assert detect_xs_mom([], make_params()) is None

    def test_default_candle_has_no_group(self):
        assert make_candle().xs_group is None


class TestGroupExit:
    P = make_params(indicator_exit_mode="sigexit")

    def _exit(self, group, direction, params=None):
        return indicator_exit_reason([make_candle(xs_group=group)], direction, "xs_mom", params or self.P)

    def test_long_holds_while_a_leader(self):
        assert self._exit(1, Direction.LONG) is None

    @pytest.mark.parametrize("group", [0, -1])
    def test_long_exits_when_no_longer_a_leader(self, group):
        assert self._exit(group, Direction.LONG) == REASON_SIGNAL_EXIT

    def test_short_holds_while_a_laggard(self):
        assert self._exit(-1, Direction.SHORT) is None

    @pytest.mark.parametrize("group", [0, 1])
    def test_short_exits_when_no_longer_a_laggard(self, group):
        assert self._exit(group, Direction.SHORT) == REASON_SIGNAL_EXIT

    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    def test_holds_off_the_decision_candle(self, direction):
        assert self._exit(None, direction) is None

    def test_mode_off_never_exits(self):
        assert self._exit(0, Direction.LONG, make_params(indicator_exit_mode="")) is None


class TestCohortBrackets:
    """The cohort's pct brackets: a 0.25 stop is clamped to 0.7 / leverage."""

    P = make_params(tp_sl_pct_enabled=True, tp_pct=1.0, sl_pct=0.25)

    def test_stop_sits_23_percent_away_at_3x(self):
        tp, sl = compute_exit_prices(100.0, Direction.LONG, atr=None, params=self.P, leverage=3)
        assert sl == pytest.approx(100.0 * (1 - 0.7 / 3))
        assert tp == pytest.approx(200.0)

    def test_short_mirror(self):
        tp, sl = compute_exit_prices(100.0, Direction.SHORT, atr=None, params=self.P, leverage=3)
        assert sl == pytest.approx(100.0 * (1 + 0.7 / 3))

    def test_the_same_params_stay_tight_at_the_fleet_leverage(self):
        _, sl = compute_exit_prices(100.0, Direction.LONG, atr=None, params=self.P, leverage=20)
        assert sl == pytest.approx(100.0 * (1 - 0.7 / 20))
