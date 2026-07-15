"""Fleet-wide concurrent-position cap (owner directive 2026-07-15).

ALL high-win bots watch the market, but only MAX_OPEN_POSITIONS_FLEET may hold
positions at once — many signal sources, bounded capital (prod $50 ⇒ 5 slots ×
$10 buckets). The gate lives in the daemon entry path (an I/O check on the DB's
authoritative open count + an in-process reservation that closes the
same-candle race); risk Rule 1 stays per-bot and untouched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import src.engine.daemon as daemon_mod
from src.engine.daemon import Daemon
from src.execution.interface import ExecutionInterface
from src.notify.telegram import TelegramNotifier
from tests.helpers.factories import make_app_config, make_params, make_signal


class _IdleExecution(ExecutionInterface):
    async def place_order(self, signal):  # pragma: no cover - gate tests never reach it
        raise NotImplementedError

    async def cancel_order(self, order_id, pair):  # pragma: no cover
        raise NotImplementedError

    async def get_position(self, pair):  # pragma: no cover
        raise NotImplementedError

    async def close_position(self, pair, reason):  # pragma: no cover
        raise NotImplementedError

    async def reconcile(self):
        return []


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(TelegramNotifier, "send", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def _clean_reservations():
    daemon_mod._fleet_slot_reservations.clear()
    yield
    daemon_mod._fleet_slot_reservations.clear()


@pytest.fixture
def _fake_db(monkeypatch):
    state = {"open_fleet": 0, "rejected_signals": [], "events": []}

    async def _count(env):
        return state["open_fleet"]

    async def _write_signal(signal, outcome, reason=None):
        state["rejected_signals"].append((signal, outcome, reason))
        return 1

    async def _write_event(*args, **kwargs):
        state["events"].append((args, kwargs))

    monkeypatch.setattr("src.engine.daemon.db.count_open_positions_fleet", _count)
    monkeypatch.setattr("src.engine.daemon.db.write_signal", _write_signal)
    monkeypatch.setattr("src.engine.daemon.db.write_event", _write_event)
    return state


def _make_daemon(cap: int, bot_id: str = "dev-BTCUSDT-1h-test-01") -> Daemon:
    cfg = make_app_config(max_open_positions_fleet=cap, bot_id=bot_id)
    return Daemon(cfg, make_params(), _IdleExecution(), TelegramNotifier(cfg))


class TestFleetSlotGate:
    async def test_cap_disabled_always_allows(self, _fake_db):
        daemon = _make_daemon(cap=0)
        _fake_db["open_fleet"] = 999
        assert await daemon._acquire_fleet_slot(make_signal(), 0) is True
        assert not daemon_mod._fleet_slot_reservations  # no reservation when disabled

    async def test_under_cap_reserves_and_allows(self, _fake_db):
        daemon = _make_daemon(cap=5)
        _fake_db["open_fleet"] = 3
        assert await daemon._acquire_fleet_slot(make_signal(), 0) is True
        assert daemon.cfg.bot_id in daemon_mod._fleet_slot_reservations

    async def test_at_cap_rejects_and_logs(self, _fake_db):
        daemon = _make_daemon(cap=5)
        _fake_db["open_fleet"] = 5
        assert await daemon._acquire_fleet_slot(make_signal(), 123) is False
        assert daemon.cfg.bot_id not in daemon_mod._fleet_slot_reservations
        assert _fake_db["rejected_signals"][0][2] == "fleet_position_limit"
        assert any("fleet_position_limit" in str(a) for a, _k in _fake_db["events"])

    async def test_reservations_count_toward_cap(self, _fake_db):
        # 4 open in DB + 1 reserved by another bot = 5 = at cap for the 6th.
        daemon = _make_daemon(cap=5)
        _fake_db["open_fleet"] = 4
        daemon_mod._fleet_slot_reservations.add("dev-ETHUSDT-1h-other-01")
        assert await daemon._acquire_fleet_slot(make_signal(), 0) is False

    async def test_release_frees_the_slot(self, _fake_db):
        daemon = _make_daemon(cap=1)
        _fake_db["open_fleet"] = 0
        assert await daemon._acquire_fleet_slot(make_signal(), 0) is True
        daemon._release_fleet_slot()
        # Same bot can re-acquire once released (e.g. after a failed order).
        assert await daemon._acquire_fleet_slot(make_signal(), 0) is True

    async def test_release_is_noop_without_reservation(self, _fake_db):
        daemon = _make_daemon(cap=5)
        daemon._release_fleet_slot()  # must not raise
