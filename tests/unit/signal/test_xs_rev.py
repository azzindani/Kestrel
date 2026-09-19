"""Unit tests for xs_rev — the cross-sectional reversal entry deployed to dev 2026-09-19.

The first entry that ranks pairs against each other. The daemon reads the universe's
closes and calls the pure cross_section_entry; the pattern reads the result off the
latest candle. Locks down the arming checklist, the group-ENTRY semantics that the
backtest measured (scripts/algo_search._build_xs_map — checked point-for-point against
this function on 3,980 synthetic (pair, candle) points before deploy), and the fade
direction.
"""

from __future__ import annotations

import dataclasses

from src.config import XS_UNIVERSE, Direction, PatternType
from src.signal.patterns import SELF_DIRECTING_PATTERNS, cross_section_entry, detect_xs_rev, registry
from src.signal.regime import Regime, regime_permits_pattern
from tests.helpers.factories import make_candle, make_params

PAIRS = ["A", "B", "C", "D", "E", "F", "G"]


def _snap(returns: dict[str, float]) -> dict[str, tuple[float, float]]:
    """(close_now, close_then) with close_then = 100 so log-return order = `returns` order."""
    return {p: (100.0 * (1.0 + r), 100.0) for p, r in returns.items()}


def _ranked(order: list[str]) -> dict[str, tuple[float, float]]:
    """Cross-section where `order` runs from biggest laggard to biggest leader."""
    return _snap({p: 0.001 * i for i, p in enumerate(order)})


class TestArmingChecklist:
    def test_registered(self):
        assert registry.get("xs_rev") is detect_xs_rev

    def test_self_directing(self):
        assert "xs_rev" in SELF_DIRECTING_PATTERNS

    def test_permitted_in_all_non_quiet_regimes(self):
        for regime in (Regime.TRENDING, Regime.VOLATILE, Regime.RANGING):
            assert regime_permits_pattern(regime, "xs_rev"), regime

    def test_blocked_in_quiet(self):
        assert not regime_permits_pattern(Regime.QUIET, "xs_rev")

    def test_has_own_pattern_type(self):
        assert PatternType.XS_REV.value == "xs_rev"

    def test_universe_is_the_backtested_ten(self):
        assert len(XS_UNIVERSE) == 10 and len(set(XS_UNIVERSE)) == 10


class TestCrossSectionEntry:
    def test_entering_the_leader_group_is_plus_one(self):
        prev = _ranked(["G", "A", "B", "C", "D", "E", "F"])  # F, E lead; G lags
        now = _ranked(["A", "B", "C", "D", "E", "F", "G"])  # G jumps to the top
        assert cross_section_entry("G", now, prev, k=2) == 1

    def test_entering_the_laggard_group_is_minus_one(self):
        prev = _ranked(["B", "C", "A", "D", "E", "F", "G"])
        now = _ranked(["A", "B", "C", "D", "E", "F", "G"])  # A drops into the bottom 2
        assert cross_section_entry("A", now, prev, k=2) == -1

    def test_staying_in_the_same_group_is_not_an_entry(self):
        same = _ranked(["A", "B", "C", "D", "E", "F", "G"])
        assert cross_section_entry("G", same, same, k=2) == 0
        assert cross_section_entry("A", same, same, k=2) == 0

    def test_flipping_from_leader_to_laggard_is_an_entry(self):
        prev = _ranked(["B", "C", "D", "E", "F", "G", "A"])  # A leads
        now = _ranked(["A", "B", "C", "D", "E", "F", "G"])  # A lags
        assert cross_section_entry("A", now, prev, k=2) == -1

    def test_middle_of_the_pack_is_zero(self):
        now = _ranked(["A", "B", "C", "D", "E", "F", "G"])
        assert cross_section_entry("D", now, {}, k=2) == 0

    def test_too_few_pairs_is_none(self):
        # 2k+1 = 5 ranked pairs are needed; four is no cross-section at all.
        now = _ranked(["A", "B", "C", "D"])
        assert cross_section_entry("A", now, {}, k=2) is None

    def test_unusable_previous_section_counts_every_member_as_entry(self):
        now = _ranked(["A", "B", "C", "D", "E", "F", "G"])
        prev = _ranked(["A", "B"])  # too small -> treated as no groups
        assert cross_section_entry("G", now, prev, k=2) == 1
        assert cross_section_entry("A", now, prev, k=2) == -1

    def test_non_positive_closes_are_ignored(self):
        now = _ranked(["A", "B", "C", "D", "E", "F", "G"])
        now["H"] = (0.0, 100.0)
        assert cross_section_entry("H", now, {}, k=2) == 0


class TestPattern:
    @staticmethod
    def _with(entry):
        candle = dataclasses.replace(make_candle(close=100.0), xs_entry=entry)
        return [make_candle(close=100.0)] * 3 + [candle]

    def test_new_leader_is_faded_short(self):
        got = detect_xs_rev(self._with(1), make_params())
        assert got is not None and got.direction is Direction.SHORT and got.pattern is PatternType.XS_REV

    def test_new_laggard_is_bought(self):
        got = detect_xs_rev(self._with(-1), make_params())
        assert got is not None and got.direction is Direction.LONG

    def test_no_entry_or_no_cross_section_stays_flat(self):
        assert detect_xs_rev(self._with(0), make_params()) is None
        assert detect_xs_rev(self._with(None), make_params()) is None
        assert detect_xs_rev([], make_params()) is None
