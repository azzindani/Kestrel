"""Unit tests for src/signal/memory.py (Tier 1)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.signal.memory import adjust_confidence, should_suppress, updated_memory

# ---------------------------------------------------------------------------
# adjust_confidence
# ---------------------------------------------------------------------------


class TestAdjustConfidence:
    def test_adjust_confidence_no_memory_returns_raw(self):
        assert adjust_confidence(0.7, None) == pytest.approx(0.7)

    def test_adjust_confidence_below_min_samples_returns_raw(self):
        memory = {"sample_count": 5, "win_rate": 0.8}
        assert adjust_confidence(0.7, memory, min_samples=10) == pytest.approx(0.7)

    def test_adjust_confidence_high_win_rate_boosts_confidence(self):
        memory = {"sample_count": 20, "win_rate": 0.9}
        result = adjust_confidence(0.6, memory, min_samples=10)
        assert result > 0.6

    def test_adjust_confidence_low_win_rate_reduces_confidence(self):
        memory = {"sample_count": 20, "win_rate": 0.2}
        result = adjust_confidence(0.7, memory, min_samples=10)
        assert result < 0.7

    def test_adjust_confidence_result_clamped_to_lower_bound(self):
        memory = {"sample_count": 20, "win_rate": 0.0}
        result = adjust_confidence(0.1, memory, min_samples=10)
        assert result >= 0.30

    def test_adjust_confidence_result_clamped_to_upper_bound(self):
        memory = {"sample_count": 20, "win_rate": 1.0}
        result = adjust_confidence(0.99, memory, min_samples=10)
        assert result <= 0.95

    def test_adjust_confidence_missing_win_rate_returns_raw(self):
        memory = {"sample_count": 20}  # no win_rate key
        assert adjust_confidence(0.65, memory, min_samples=10) == pytest.approx(0.65)

    def test_adjust_confidence_blend_formula_correct(self):
        memory = {"sample_count": 20, "win_rate": 0.6}
        raw = 0.7
        expected = round(raw * 0.70 + 0.6 * 0.30, 3)
        result = adjust_confidence(raw, memory, min_samples=10)
        assert result == pytest.approx(expected, abs=1e-3)

    @given(
        raw=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        win_rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        samples=st.integers(min_value=10, max_value=1000),
    )
    @settings(max_examples=200)
    def test_adjust_confidence_result_always_in_bounds(self, raw: float, win_rate: float, samples: int):
        memory = {"sample_count": samples, "win_rate": win_rate}
        result = adjust_confidence(raw, memory, min_samples=10)
        assert 0.30 <= result <= 0.95


# ---------------------------------------------------------------------------
# should_suppress
# ---------------------------------------------------------------------------


class TestShouldSuppress:
    def test_should_suppress_no_memory_returns_false(self):
        assert should_suppress("impulse_retracement", "long", "asian", "TRENDING", None) is False

    def test_should_suppress_below_min_samples_returns_false(self):
        memory = {"sample_count": 10, "win_rate": 0.1}
        assert should_suppress("p", "long", "s", "r", memory, min_samples=20) is False

    def test_should_suppress_poor_win_rate_above_threshold_returns_true(self):
        memory = {"sample_count": 30, "win_rate": 0.2}
        assert should_suppress("p", "long", "s", "r", memory, min_samples=20, min_win_rate=0.35) is True

    def test_should_suppress_adequate_win_rate_returns_false(self):
        memory = {"sample_count": 30, "win_rate": 0.6}
        assert should_suppress("p", "long", "s", "r", memory, min_samples=20) is False

    def test_should_suppress_exactly_at_min_win_rate_returns_false(self):
        memory = {"sample_count": 30, "win_rate": 0.35}
        assert should_suppress("p", "long", "s", "r", memory, min_samples=20, min_win_rate=0.35) is False

    def test_should_suppress_missing_win_rate_returns_false(self):
        memory = {"sample_count": 30}
        assert should_suppress("p", "long", "s", "r", memory, min_samples=20) is False


# ---------------------------------------------------------------------------
# updated_memory
# ---------------------------------------------------------------------------


class TestUpdatedMemory:
    def test_updated_memory_first_trade_win_increments_win_count(self):
        result = updated_memory(
            None, won=True, pnl_pct=0.5, ts_ms=1000, pattern="p", direction="long", session="asian", regime="TRENDING"
        )
        assert result["win_count"] == 1
        assert result["sample_count"] == 1

    def test_updated_memory_first_trade_loss_win_count_zero(self):
        result = updated_memory(
            None, won=False, pnl_pct=-0.3, ts_ms=1000, pattern="p", direction="long", session="asian", regime="TRENDING"
        )
        assert result["win_count"] == 0

    def test_updated_memory_win_rate_correct_after_2_wins_1_loss(self):
        m1 = updated_memory(
            None, won=True, pnl_pct=0.5, ts_ms=1000, pattern="p", direction="long", session="asian", regime="TRENDING"
        )
        m2 = updated_memory(
            m1, won=True, pnl_pct=0.5, ts_ms=2000, pattern="p", direction="long", session="asian", regime="TRENDING"
        )
        m3 = updated_memory(
            m2, won=False, pnl_pct=-0.3, ts_ms=3000, pattern="p", direction="long", session="asian", regime="TRENDING"
        )
        assert m3["win_rate"] == pytest.approx(2 / 3, abs=1e-4)

    def test_updated_memory_avg_pnl_is_running_average(self):
        m1 = updated_memory(
            None, won=True, pnl_pct=1.0, ts_ms=1000, pattern="p", direction="long", session="asian", regime="TRENDING"
        )
        m2 = updated_memory(
            m1, won=False, pnl_pct=-1.0, ts_ms=2000, pattern="p", direction="long", session="asian", regime="TRENDING"
        )
        assert m2["avg_pnl_pct"] == pytest.approx(0.0, abs=1e-4)

    def test_updated_memory_contains_all_required_fields(self):
        result = updated_memory(
            None, won=True, pnl_pct=0.5, ts_ms=1000, pattern="p", direction="long", session="asian", regime="TRENDING"
        )
        for key in (
            "pattern",
            "direction",
            "session",
            "regime",
            "sample_count",
            "win_count",
            "win_rate",
            "avg_pnl_pct",
            "last_updated",
        ):
            assert key in result

    def test_updated_memory_last_updated_matches_ts(self):
        ts = 99999
        result = updated_memory(
            None, won=True, pnl_pct=0.0, ts_ms=ts, pattern="p", direction="long", session="asian", regime="TRENDING"
        )
        assert result["last_updated"] == ts

    @given(
        pnl_pcts=st.lists(
            st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False), min_size=1, max_size=20
        ),
        wins=st.lists(st.booleans(), min_size=1, max_size=20),
    )
    @settings(max_examples=100)
    def test_updated_memory_win_rate_always_in_unit_interval(self, pnl_pcts: list[float], wins: list[bool]):
        n = min(len(pnl_pcts), len(wins))
        m = None
        for i in range(n):
            m = updated_memory(
                m,
                won=wins[i],
                pnl_pct=pnl_pcts[i],
                ts_ms=i,
                pattern="p",
                direction="long",
                session="asian",
                regime="TRENDING",
            )
        if m is not None:
            assert 0.0 <= m["win_rate"] <= 1.0
