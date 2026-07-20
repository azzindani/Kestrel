"""Unit tests for Daemon._reconcile()'s orphaned-position recovery.

Regression test for the 2026-07-20 incident: a host disk-full event killed the
kestrel/staging containers OUTSIDE stop.sh (no SIGTERM, no graceful close), so
the test_daemon_stop.py fix never ran. SimulationExecution holds positions in
memory only, so after any such crash its own reconcile() always comes back
empty — the DB rows stayed exit_ts=NULL forever, permanently jamming each
bot's bucket via count_active_positions (CLAUDE.md §11: DB is authoritative,
so an inconsistent row must be reconciled, not left silently open).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.engine.daemon import Daemon
from src.execution.interface import ExecutionInterface
from src.notify.telegram import TelegramNotifier
from tests.helpers.factories import make_app_config, make_params


class _FakeExecution(ExecutionInterface):
    def __init__(self, live_positions: list[dict[str, Any]]) -> None:
        self._live = live_positions

    async def place_order(self, signal):  # pragma: no cover
        raise NotImplementedError

    async def cancel_order(self, order_id, pair):  # pragma: no cover
        raise NotImplementedError

    async def get_position(self, pair):  # pragma: no cover
        raise NotImplementedError

    async def close_position(self, pair, reason):  # pragma: no cover
        raise NotImplementedError

    async def reconcile(self) -> list[dict[str, Any]]:
        return self._live


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(TelegramNotifier, "send", AsyncMock(return_value=None))


@pytest.fixture
def _fake_db(monkeypatch):
    state: dict[str, Any] = {"open_trades": [], "close_trade": [], "write_event": []}

    async def _get_open_trades(bot_id, env):
        return state["open_trades"]

    async def _close_trade(trade_id, close):
        state["close_trade"].append((trade_id, close))

    async def _write_event(*args, **kwargs):
        state["write_event"].append((args, kwargs))

    monkeypatch.setattr("src.engine.daemon.db.get_open_trades", _get_open_trades)
    monkeypatch.setattr("src.engine.daemon.db.close_trade", _close_trade)
    monkeypatch.setattr("src.engine.daemon.db.write_event", _write_event)
    return state


def _make_daemon(execution: ExecutionInterface, **cfg_overrides) -> Daemon:
    cfg = make_app_config(**cfg_overrides)
    notifier = TelegramNotifier(cfg)
    return Daemon(cfg, make_params(), execution, notifier)


class TestReconcileOrphanRecovery:
    async def test_db_open_trade_with_no_live_position_is_settled(self, _fake_db):
        _fake_db["open_trades"] = [
            {
                "id": 99,
                "pair": "BTC/USDT",
                "entry_ts": 1_700_000_000_000,
                "entry_price": 50000.0,
                "notional_usdt": 100.0,
                "bucket_balance_before": 10.0,
            }
        ]
        daemon = _make_daemon(_FakeExecution([]))

        await daemon._reconcile()

        assert len(_fake_db["close_trade"]) == 1
        trade_id, close = _fake_db["close_trade"][0]
        assert trade_id == 99
        assert close["close_reason"] == "orphaned_crash_recovery"
        assert close["exit_price"] == 50000.0
        assert close["pnl_gross_usdt"] == 0.0
        assert close["fee_exit_usdt"] > 0.0
        assert close["pnl_net_usdt"] == pytest.approx(-close["fee_exit_usdt"])
        assert close["bucket_balance_after"] == pytest.approx(10.0 - close["fee_exit_usdt"])

    async def test_live_position_matching_pair_is_not_orphaned(self, _fake_db):
        _fake_db["open_trades"] = [
            {
                "id": 1,
                "pair": "ETH/USDT",
                "entry_ts": 1_700_000_000_000,
                "entry_price": 3000.0,
                "notional_usdt": 60.0,
                "bucket_balance_before": 10.0,
            }
        ]
        daemon = _make_daemon(_FakeExecution([{"pair": "ETH/USDT"}]))

        await daemon._reconcile()

        assert _fake_db["close_trade"] == []

    async def test_no_open_trades_is_a_noop(self, _fake_db):
        daemon = _make_daemon(_FakeExecution([]))

        await daemon._reconcile()

        assert _fake_db["close_trade"] == []

    async def test_orphan_recovery_logs_critical_event(self, _fake_db):
        _fake_db["open_trades"] = [
            {
                "id": 5,
                "pair": "SOL/USDT",
                "entry_ts": 1_700_000_000_000,
                "entry_price": 100.0,
                "notional_usdt": 50.0,
                "bucket_balance_before": 10.0,
            }
        ]
        daemon = _make_daemon(_FakeExecution([]))

        await daemon._reconcile()

        critical = [
            (a, k)
            for a, k in _fake_db["write_event"]
            if len(a) >= 4 and a[3] == "CRITICAL" and a[5] == "orphaned_position_recovered"
        ]
        assert len(critical) == 1

    async def test_multiple_orphans_all_settled(self, _fake_db):
        _fake_db["open_trades"] = [
            {
                "id": 1,
                "pair": "BTC/USDT",
                "entry_ts": 1,
                "entry_price": 100.0,
                "notional_usdt": 10.0,
                "bucket_balance_before": 10.0,
            },
            {
                "id": 2,
                "pair": "ETH/USDT",
                "entry_ts": 2,
                "entry_price": 200.0,
                "notional_usdt": 10.0,
                "bucket_balance_before": 10.0,
            },
        ]
        daemon = _make_daemon(_FakeExecution([]))

        await daemon._reconcile()

        assert len(_fake_db["close_trade"]) == 2

    async def test_missing_notional_defaults_to_zero_fee(self, _fake_db):
        _fake_db["open_trades"] = [
            {
                "id": 3,
                "pair": "XRP/USDT",
                "entry_ts": 1,
                "entry_price": 1.0,
                "notional_usdt": None,
                "bucket_balance_before": 10.0,
            }
        ]
        daemon = _make_daemon(_FakeExecution([]))

        await daemon._reconcile()

        _, close = _fake_db["close_trade"][0]
        assert close["fee_exit_usdt"] == 0.0
