"""Unit tests for src/engine/portfolio_guard.py (pure decision + aggregation logic)."""

from __future__ import annotations

from src.engine.portfolio_guard import PortfolioGuard
from src.notify.telegram import TelegramNotifier
from tests.helpers.factories import make_app_config


class _FakeBot:
    """Minimal stand-in for a Daemon: a fixed (equity, unrealised) snapshot."""

    def __init__(self, equity: float, unrealised: float) -> None:
        self._snap = (equity, unrealised)
        self.closed_with: str | None = None

    def portfolio_snapshot(self) -> tuple[float, float]:
        return self._snap

    async def force_close_all(self, reason: str) -> None:
        self.closed_with = reason


def _guard(tp: float, dd: float) -> PortfolioGuard:
    cfg = make_app_config()
    return PortfolioGuard(cfg, "dev-global", TelegramNotifier(cfg), tp, dd)


class TestEnabled:
    def test_disabled_when_both_zero(self):
        assert _guard(0.0, 0.0).enabled is False

    def test_enabled_with_tp_only(self):
        assert _guard(0.10, 0.0).enabled is True

    def test_enabled_with_dd_only(self):
        assert _guard(0.0, 0.10).enabled is True


class TestAggregate:
    def test_sums_equity_and_unrealised(self):
        g = _guard(0.10, 0.10)
        g.attach([_FakeBot(10.0, 1.0), _FakeBot(12.0, -0.5), _FakeBot(8.0, 2.0)])
        equity, unrealised = g._aggregate()
        assert equity == 30.0
        assert unrealised == 2.5

    def test_empty_is_zero(self):
        assert _guard(0.10, 0.10)._aggregate() == (0.0, 0.0)


class TestDecide:
    def test_profit_lock_triggers_at_threshold(self):
        # +10% of 100 equity = +10 unrealised → fire profit-lock.
        assert _guard(0.10, 0.10)._decide(100.0, 10.0) == "portfolio_take_profit"

    def test_drawdown_stop_triggers_at_threshold(self):
        assert _guard(0.10, 0.10)._decide(100.0, -10.0) == "portfolio_drawdown_stop"

    def test_no_trigger_inside_band(self):
        assert _guard(0.10, 0.10)._decide(100.0, 5.0) is None
        assert _guard(0.10, 0.10)._decide(100.0, -5.0) is None

    def test_tp_side_off_does_not_fire_on_gain(self):
        assert _guard(0.0, 0.10)._decide(100.0, 50.0) is None

    def test_dd_side_off_does_not_fire_on_loss(self):
        assert _guard(0.10, 0.0)._decide(100.0, -50.0) is None

    def test_zero_equity_never_fires(self):
        assert _guard(0.10, 0.10)._decide(0.0, 5.0) is None
