"""Unit tests for src/signal/memory.py"""
import pytest

from src.signal.memory import adjust_confidence, should_suppress, updated_memory


class TestAdjustConfidence:
    def test_no_memory_returns_raw(self):
        assert adjust_confidence(0.7, None) == pytest.approx(0.7)

    def test_insufficient_samples_returns_raw(self):
        mem = {"sample_count": 5, "win_rate": 0.3}
        assert adjust_confidence(0.7, mem, min_samples=10) == pytest.approx(0.7)

    def test_high_win_rate_boosts_low_confidence(self):
        mem = {"sample_count": 20, "win_rate": 0.9}
        result = adjust_confidence(0.55, mem)
        assert result > 0.55

    def test_low_win_rate_reduces_confidence(self):
        mem = {"sample_count": 20, "win_rate": 0.2}
        result = adjust_confidence(0.75, mem)
        assert result < 0.75

    def test_result_clamped_to_range(self):
        mem = {"sample_count": 20, "win_rate": 0.0}
        result = adjust_confidence(0.3, mem)
        assert result >= 0.30
        mem_high = {"sample_count": 20, "win_rate": 1.0}
        result_high = adjust_confidence(0.95, mem_high)
        assert result_high <= 0.95


class TestShouldSuppress:
    def test_no_memory_never_suppresses(self):
        assert should_suppress("p", "long", "asian", "TRENDING", None) is False

    def test_insufficient_samples_no_suppress(self):
        mem = {"sample_count": 5, "win_rate": 0.1}
        assert should_suppress("p", "long", "asian", "TRENDING", mem, min_samples=20) is False

    def test_low_win_rate_with_enough_samples_suppresses(self):
        mem = {"sample_count": 30, "win_rate": 0.2}
        assert should_suppress("p", "long", "asian", "TRENDING", mem, min_samples=20, min_win_rate=0.35) is True

    def test_acceptable_win_rate_no_suppress(self):
        mem = {"sample_count": 30, "win_rate": 0.5}
        assert should_suppress("p", "long", "asian", "TRENDING", mem) is False


class TestUpdatedMemory:
    def test_first_trade_win(self):
        result = updated_memory(None, won=True, pnl_pct=0.5, ts_ms=1000,
                                pattern="impulse_retracement", direction="long",
                                session="asian", regime="TRENDING")
        assert result["sample_count"] == 1
        assert result["win_count"] == 1
        assert result["win_rate"] == pytest.approx(1.0)

    def test_running_average(self):
        existing = {"sample_count": 4, "win_count": 2, "avg_pnl_pct": 0.3, "win_rate": 0.5}
        result = updated_memory(existing, won=False, pnl_pct=-0.1, ts_ms=2000,
                                pattern="p", direction="long", session="london", regime="RANGING")
        assert result["sample_count"] == 5
        assert result["win_count"] == 2
        assert result["win_rate"] == pytest.approx(0.4)
