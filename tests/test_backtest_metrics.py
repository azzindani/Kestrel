"""Unit tests for src/backtest/metrics.py"""
import pytest

from src.backtest.metrics import compute_metrics, compare_metrics


def _trade(pnl: float, reason: str = "take_profit", hold: int = 3) -> dict:
    return {
        "pnl_net_usdt": pnl,
        "pnl_pct": pnl / 10.0 * 100.0,
        "close_reason": reason,
        "entry_ts": 0,
        "exit_ts": hold * 300_000,
        "size_usdt": 10.0,
        "hold_candles": hold,
    }


class TestComputeMetrics:
    def test_empty_trades(self):
        m = compute_metrics([])
        assert m["total_trades"] == 0
        assert m["win_rate"] == 0.0

    def test_all_wins(self):
        trades = [_trade(1.0) for _ in range(10)]
        m = compute_metrics(trades)
        assert m["total_trades"] == 10
        assert m["win_rate"] == pytest.approx(1.0)
        assert m["total_pnl_usdt"] == pytest.approx(10.0)

    def test_mixed_wins_losses(self):
        trades = [_trade(1.0)] * 6 + [_trade(-0.5)] * 4
        m = compute_metrics(trades)
        assert m["win_rate"] == pytest.approx(0.6)
        assert m["total_pnl_usdt"] == pytest.approx(4.0)
        assert m["avg_win_usdt"] > 0
        assert m["avg_loss_usdt"] < 0

    def test_max_drawdown_positive(self):
        # Sequence: +1, +1, -3, +1 → peak=2, drawdown=3
        trades = [_trade(1.0), _trade(1.0), _trade(-3.0), _trade(1.0)]
        m = compute_metrics(trades)
        assert m["max_drawdown_usdt"] == pytest.approx(3.0)

    def test_close_reason_breakdown(self):
        trades = [
            _trade(1.0, "take_profit"),
            _trade(-0.5, "stop_loss"),
            _trade(0.1, "timeout"),
        ]
        m = compute_metrics(trades)
        assert m["close_reasons"]["take_profit"] == 1
        assert m["close_reasons"]["stop_loss"] == 1
        assert m["close_reasons"]["timeout"] == 1


class TestCompareMetrics:
    def test_accept_when_all_improve(self):
        baseline = {
            "win_rate": 0.5, "total_pnl_usdt": 10.0, "sharpe_ratio": 1.0,
            "max_drawdown_pct": 20.0, "profit_factor": 1.5,
        }
        candidate = {
            "win_rate": 0.6, "total_pnl_usdt": 15.0, "sharpe_ratio": 1.5,
            "max_drawdown_pct": 15.0, "profit_factor": 2.0,
        }
        result = compare_metrics(baseline, candidate)
        assert result["verdict"] == "ACCEPT"

    def test_reject_when_win_rate_regresses(self):
        baseline = {
            "win_rate": 0.6, "total_pnl_usdt": 10.0, "sharpe_ratio": 1.0,
            "max_drawdown_pct": 20.0, "profit_factor": 1.5,
        }
        candidate = {
            "win_rate": 0.4, "total_pnl_usdt": 10.0, "sharpe_ratio": 1.0,
            "max_drawdown_pct": 20.0, "profit_factor": 1.5,
        }
        result = compare_metrics(baseline, candidate)
        assert result["verdict"] == "REJECT"
        assert result["win_rate"] == "regress"
