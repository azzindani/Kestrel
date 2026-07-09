"""Unit tests for Daemon.stop() — verifies the graceful-shutdown close path
actually persists the trade close to DB (CLAUDE.md §11 single-source-of-truth).

Regression test for the ghost-position bug (research-loop iter 54): stop() used
to call execution.close_position() directly and only log an event, never
writing exit_ts/close_reason/pnl to the trades row. Since SimulationExecution
never persists across restarts, that left the row exit_ts=NULL forever — a
permanent ghost that also jams the bot's bucket via count_active_positions.
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
    """Minimal execution stub: one open position, closes cleanly."""

    def __init__(self, open_positions: list[dict[str, Any]]) -> None:
        self._open = open_positions
        self.close_calls: list[tuple[str, str]] = []

    async def place_order(self, signal):  # pragma: no cover - unused here
        raise NotImplementedError

    async def cancel_order(self, order_id, pair):  # pragma: no cover
        raise NotImplementedError

    async def get_position(self, pair):  # pragma: no cover
        raise NotImplementedError

    async def close_position(self, pair: str, reason: str) -> dict[str, Any]:
        self.close_calls.append((pair, reason))
        return {
            "exit_price": 101.0,
            "pnl_gross_usdt": 1.0,
            "fee_exit_usdt": 0.01,
            "pnl_net_usdt": 0.9,
            "pnl_pct": 0.09,
            "ts": 1_700_000_100_000,
            "hold_candles": 3,
            "direction": "long",
            "entry_price": 100.0,
            "leverage": 20,
        }

    async def reconcile(self) -> list[dict[str, Any]]:
        return self._open


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # Never hit the real Telegram API from a unit test.
    monkeypatch.setattr(TelegramNotifier, "send", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def _fake_db(monkeypatch):
    calls: dict[str, list] = {"close_trade": [], "write_event": []}

    async def _fake_close_trade(trade_id, close):
        calls["close_trade"].append((trade_id, close))

    async def _fake_write_event(*args, **kwargs):
        calls["write_event"].append((args, kwargs))

    monkeypatch.setattr("src.engine.daemon.db.close_trade", _fake_close_trade)
    monkeypatch.setattr("src.engine.daemon.db.write_event", _fake_write_event)
    return calls


def _make_daemon(execution: ExecutionInterface) -> Daemon:
    cfg = make_app_config()
    params = make_params()
    notifier = TelegramNotifier(cfg)
    daemon = Daemon(cfg, params, execution, notifier)
    daemon._running = True
    return daemon


class TestStopClosesPositionsInDb:
    async def test_stop_writes_exit_to_trades_row(self, _fake_db):
        execution = _FakeExecution([{"pair": "BTC/USDT"}])
        daemon = _make_daemon(execution)
        daemon._open_trade_ids["BTC/USDT"] = (42, 1_700_000_000_000, 10.0)

        await daemon.stop()

        assert execution.close_calls == [("BTC/USDT", "manual")]
        assert len(_fake_db["close_trade"]) == 1
        trade_id, close = _fake_db["close_trade"][0]
        assert trade_id == 42
        assert close["exit_ts"] == 1_700_000_100_000
        assert close["close_reason"] == "manual"
        assert close["pnl_net_usdt"] == 0.9

    async def test_stop_pops_open_trade_id(self, _fake_db):
        execution = _FakeExecution([{"pair": "ETH/USDT"}])
        daemon = _make_daemon(execution)
        daemon._open_trade_ids["ETH/USDT"] = (7, 1_700_000_000_000, 10.0)

        await daemon.stop()

        assert "ETH/USDT" not in daemon._open_trade_ids

    async def test_stop_with_no_open_positions_writes_nothing(self, _fake_db):
        execution = _FakeExecution([])
        daemon = _make_daemon(execution)

        await daemon.stop()

        assert _fake_db["close_trade"] == []

    async def test_stop_survives_missing_open_trade_id(self, _fake_db):
        # reconcile() reports a position the daemon has no _open_trade_ids
        # entry for (e.g. a pre-existing ghost) — must not raise, just skip
        # the DB write (no trade_id to update).
        execution = _FakeExecution([{"pair": "SOL/USDT"}])
        daemon = _make_daemon(execution)

        await daemon.stop()

        assert execution.close_calls == [("SOL/USDT", "manual")]
        assert _fake_db["close_trade"] == []
