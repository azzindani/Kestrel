"""Unit tests for src/engine/scheduler.py.

Focus on the trade_context_post_task: per-bot logic + per-trade error
isolation. The other tasks (heartbeat, daily_summary, cleanup) are tight
asyncio wrappers around DB ops better validated via the live deployment
+ retention_cleanup_complete events, not unit mocks.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.engine.scheduler import trade_context_post_task

pytestmark = pytest.mark.asyncio


async def _run_one_tick(interval: float = 0.01) -> None:
    """Spin trade_context_post_task long enough to process one batch then stop."""
    task = asyncio.create_task(trade_context_post_task("dev", interval=interval))
    # Yield twice: once for the initial sleep, once for the iteration body.
    await asyncio.sleep(interval * 5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestTradeContextPostTask:
    @staticmethod
    def _once_then_empty(payload):
        """Mock helper: first call returns payload, subsequent calls return []."""
        calls = {"n": 0}

        async def _fn(*_, **__):
            calls["n"] += 1
            return payload if calls["n"] == 1 else []

        return _fn

    async def test_calls_link_post_for_each_pending_trade(self):
        pending = [
            {"id": 1, "bot_id": "b1", "pair": "BTC/USDT", "timeframe": "5m", "exit_ts": 1000},
            {"id": 2, "bot_id": "b2", "pair": "ETH/USDT", "timeframe": "5m", "exit_ts": 2000},
        ]
        with patch("src.engine.scheduler.db") as mock_db:
            mock_db.trades_pending_post_context = self._once_then_empty(pending)
            mock_db.link_post_context = AsyncMock(return_value=576)
            await _run_one_tick()

        assert mock_db.link_post_context.call_count == 2
        called_ids = {c.args[0] for c in mock_db.link_post_context.call_args_list}
        assert called_ids == {1, 2}

    async def test_no_pending_trades_is_a_noop(self):
        with patch("src.engine.scheduler.db") as mock_db:
            mock_db.trades_pending_post_context = AsyncMock(return_value=[])
            mock_db.link_post_context = AsyncMock()
            await _run_one_tick()
        assert mock_db.link_post_context.call_count == 0

    async def test_link_failure_for_one_trade_does_not_skip_others(self):
        """If link_post_context raises for trade #1, trade #2 still gets linked."""
        pending = [
            {"id": 1, "bot_id": "b1", "pair": "BTC/USDT", "timeframe": "5m", "exit_ts": 1000},
            {"id": 2, "bot_id": "b2", "pair": "ETH/USDT", "timeframe": "5m", "exit_ts": 2000},
        ]
        call_count = {"n": 0}

        async def fake_link(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated DB error on trade 1")
            return 0

        with patch("src.engine.scheduler.db") as mock_db:
            mock_db.trades_pending_post_context = self._once_then_empty(pending)
            mock_db.link_post_context = fake_link
            await _run_one_tick()

        # Both trades attempted despite the first failure.
        assert call_count["n"] == 2

    async def test_db_query_failure_does_not_kill_task(self):
        """trades_pending_post_context raising should not abort the loop."""
        with patch("src.engine.scheduler.db") as mock_db:
            mock_db.trades_pending_post_context = AsyncMock(side_effect=RuntimeError("DB connection lost"))
            mock_db.link_post_context = AsyncMock()
            # If the task crashed, this would propagate the RuntimeError.
            await _run_one_tick()
        assert mock_db.link_post_context.call_count == 0
