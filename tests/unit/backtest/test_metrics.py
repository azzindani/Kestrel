"""Unit tests for src/backtest/metrics.py (Tier 1)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.backtest.metrics import compare_metrics, compute_metrics

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trade(pnl: float, close_reason: str = "take_profit", hold: int = 2) -> dict:
    return {
        "pnl_net_usdt": pnl,
        "pnl_pct": pnl / 10.0 * 100.0,
        "close_reason": close_reason,
        "entry_ts": 1000,
        "exit_ts": 2000,
        "size_usdt": 10.0,
        "hold_candles": hold,
    }


# ---------------------------------------------------------------------------
# compute_metrics — contract tests
# ---------------------------------------------------------------------------


class TestComputeMetricsContract:
    def test_compute_metrics_empty_returns_zero_metrics(self):
        result = compute_metrics([])
        assert result["total_trades"] == 0
        assert result["win_rate"] == 0.0

    def test_compute_metrics_returns_all_required_keys(self):
        result = compute_metrics([_trade(0.5)])
        required = {
            "total_trades",
            "win_count",
            "loss_count",
            "win_rate",
            "total_pnl_usdt",
            "avg_pnl_usdt",
            "avg_win_usdt",
            "avg_loss_usdt",
            "profit_factor",
            "sharpe_ratio",
            "max_drawdown_usdt",
            "max_drawdown_pct",
            "avg_hold_candles",
            "close_reasons",
        }
        assert required.issubset(result.keys())

    def test_compute_metrics_single_win_returns_100_pct_win_rate(self):
        result = compute_metrics([_trade(1.0)])
        assert result["win_rate"] == pytest.approx(1.0)

    def test_compute_metrics_single_loss_returns_0_pct_win_rate(self):
        result = compute_metrics([_trade(-1.0)])
        assert result["win_rate"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_metrics — correctness
# ---------------------------------------------------------------------------


class TestComputeMetricsCorrectness:
    def test_compute_metrics_two_wins_one_loss_correct_win_rate(self):
        trades = [_trade(1.0), _trade(0.5), _trade(-0.3, "stop_loss")]
        result = compute_metrics(trades)
        assert result["win_rate"] == pytest.approx(2 / 3, abs=1e-4)

    def test_compute_metrics_total_pnl_is_sum_of_all_pnls(self):
        trades = [_trade(1.0), _trade(-0.5), _trade(0.8)]
        result = compute_metrics(trades)
        assert result["total_pnl_usdt"] == pytest.approx(1.3, abs=1e-4)

    def test_compute_metrics_avg_win_correct(self):
        trades = [_trade(1.0), _trade(2.0), _trade(-1.0, "stop_loss")]
        result = compute_metrics(trades)
        assert result["avg_win_usdt"] == pytest.approx(1.5, abs=1e-4)

    def test_compute_metrics_avg_loss_correct(self):
        trades = [_trade(1.0), _trade(-1.0, "stop_loss"), _trade(-3.0, "stop_loss")]
        result = compute_metrics(trades)
        assert result["avg_loss_usdt"] == pytest.approx(-2.0, abs=1e-4)

    def test_compute_metrics_profit_factor_infinite_when_no_losses(self):
        trades = [_trade(1.0), _trade(0.5)]
        result = compute_metrics(trades)
        assert result["profit_factor"] is None  # stored as None for inf

    def test_compute_metrics_sharpe_zero_when_constant_pnl(self):
        # Constant PnL → std=0 → Sharpe=0
        trades = [_trade(1.0)] * 10
        result = compute_metrics(trades)
        assert result["sharpe_ratio"] == pytest.approx(0.0)

    def test_compute_metrics_sharpe_positive_when_consistent_positive(self):
        trades = [_trade(float(i) * 0.1 + 0.5) for i in range(20)]
        result = compute_metrics(trades)
        assert result["sharpe_ratio"] > 0.0

    def test_compute_metrics_max_drawdown_zero_when_monotone_gains(self):
        trades = [_trade(1.0)] * 5
        result = compute_metrics(trades)
        assert result["max_drawdown_usdt"] == pytest.approx(0.0, abs=1e-6)
        assert result["max_drawdown_pct"] == pytest.approx(0.0, abs=1e-6)

    def test_compute_metrics_max_drawdown_correct_for_known_series(self):
        # PnL series: +5, -3, +2, -4, +1
        # Cumulative: 5, 2, 4, 0, 1
        # Peak: 5, drawdown: 5-0=5 (at index 3)
        trades = [_trade(5), _trade(-3), _trade(2), _trade(-4), _trade(1)]
        result = compute_metrics(trades)
        assert result["max_drawdown_usdt"] == pytest.approx(5.0, abs=1e-4)

    def test_compute_metrics_close_reasons_counted_correctly(self):
        trades = [
            _trade(1.0, "take_profit"),
            _trade(-0.5, "stop_loss"),
            _trade(-0.2, "stop_loss"),
            _trade(-0.1, "timeout"),
        ]
        result = compute_metrics(trades)
        assert result["close_reasons"]["take_profit"] == 1
        assert result["close_reasons"]["stop_loss"] == 2
        assert result["close_reasons"]["timeout"] == 1

    def test_compute_metrics_avg_hold_candles_correct(self):
        trades = [_trade(1.0, hold=2), _trade(1.0, hold=4)]
        result = compute_metrics(trades)
        assert result["avg_hold_candles"] == pytest.approx(3.0)

    @given(
        pnls=st.lists(
            st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=50,
        )
    )
    @settings(max_examples=200)
    def test_compute_metrics_win_rate_always_in_unit_interval(self, pnls: list[float]):
        trades = [_trade(p) for p in pnls]
        result = compute_metrics(trades)
        assert 0.0 <= result["win_rate"] <= 1.0

    @given(
        pnls=st.lists(
            st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
            min_size=2,
            max_size=50,
        )
    )
    @settings(max_examples=100)
    def test_compute_metrics_sharpe_is_finite(self, pnls: list[float]):
        trades = [_trade(p) for p in pnls]
        result = compute_metrics(trades)
        assert not math.isnan(result["sharpe_ratio"])
        assert not math.isinf(result["sharpe_ratio"])

    @given(
        pnls=st.lists(
            st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=50,
        )
    )
    @settings(max_examples=100)
    def test_compute_metrics_max_drawdown_always_non_negative(self, pnls: list[float]):
        trades = [_trade(p) for p in pnls]
        result = compute_metrics(trades)
        assert result["max_drawdown_usdt"] >= 0.0
        assert result["max_drawdown_pct"] >= 0.0


# ---------------------------------------------------------------------------
# compare_metrics
# ---------------------------------------------------------------------------


class TestCompareMetrics:
    def _baseline(self) -> dict:
        return {
            "win_rate": 0.6,
            "total_pnl_usdt": 10.0,
            "sharpe_ratio": 1.5,
            "max_drawdown_pct": 5.0,
            "profit_factor": 2.0,
        }

    def test_compare_metrics_identical_returns_accept(self):
        result = compare_metrics(self._baseline(), self._baseline())
        assert result["verdict"] == "ACCEPT"

    def test_compare_metrics_improved_candidate_returns_accept(self):
        candidate = {**self._baseline(), "win_rate": 0.65, "total_pnl_usdt": 12.0}
        result = compare_metrics(self._baseline(), candidate)
        assert result["verdict"] == "ACCEPT"

    def test_compare_metrics_regressed_win_rate_returns_reject(self):
        # 20% drop in win_rate: 0.6 → 0.48 → delta = -0.2 < -0.05 threshold
        candidate = {**self._baseline(), "win_rate": 0.48}
        result = compare_metrics(self._baseline(), candidate)
        assert result["verdict"] == "REJECT"
        assert result["win_rate"] == "regress"

    def test_compare_metrics_improved_drawdown_returns_improve(self):
        # lower drawdown is better
        candidate = {**self._baseline(), "max_drawdown_pct": 3.0}
        result = compare_metrics(self._baseline(), candidate)
        assert result["max_drawdown_pct"] == "improve"

    def test_compare_metrics_missing_value_treated_as_hold(self):
        baseline = {**self._baseline(), "profit_factor": None}
        candidate = {**self._baseline(), "profit_factor": None}
        result = compare_metrics(baseline, candidate)
        assert result["profit_factor"] == "hold"

    def test_compare_metrics_zero_baseline_treated_as_hold(self):
        baseline = {**self._baseline(), "win_rate": 0.0}
        candidate = {**self._baseline(), "win_rate": 0.5}
        result = compare_metrics(baseline, candidate)
        assert result["win_rate"] == "hold"
