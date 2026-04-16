"""
Layer 1 — scheduled task definitions.

All tasks are pure async functions that accept explicit dependencies
(no global state). The daemon wires them up as asyncio tasks.
"""
from __future__ import annotations

import asyncio
import os
import time

from src.config import AppConfig
from src.db import writer as db


async def heartbeat_task(cfg: AppConfig, session_id: str, interval: float = 30.0) -> None:
    """Write a heartbeat record every `interval` seconds (CLAUDE.md §16)."""
    while True:
        await asyncio.sleep(interval)
        ts = int(time.time() * 1000)
        pid = os.getpid()
        await db.write_heartbeat(cfg.bot_id, ts, pid, "running")


async def daily_summary_task(
    cfg: AppConfig,
    session_id: str,
    notify_fn,
) -> None:
    """Compute and send daily summary at 00:00 UTC (CLAUDE.md §27)."""
    while True:
        now = time.time()
        # Seconds until next UTC midnight
        midnight = (int(now) // 86400 + 1) * 86400
        await asyncio.sleep(midnight - now)

        since_ts = midnight * 1000 - 86_400_000  # previous day start

        # Fetch session stats
        net_pnl = await db.get_session_pnl(cfg.bot_id, cfg.env.value, since_ts)
        active = await db.count_active_positions(cfg.bot_id, cfg.env.value)

        await db.write_event(
            cfg.bot_id, session_id, cfg.env.value,
            "INFO", "system",
            "daily_summary",
            {"net_pnl_usdt": net_pnl, "active_positions": active},
        )

        if notify_fn:
            await notify_fn.daily_summary({
                "total_trades": 0,  # pulled from DB by caller if needed
                "win_rate": 0.0,
                "net_pnl_usdt": net_pnl,
                "bucket_states": f"{active} active",
            })


async def cleanup_task(cfg: AppConfig, session_id: str) -> None:
    """Run retention cleanup at 03:00 UTC daily (CLAUDE.md §15)."""
    while True:
        now = time.time()
        # Next 03:00 UTC
        day_start = (int(now) // 86400) * 86400
        target = day_start + 3 * 3600
        if target <= now:
            target += 86400
        await asyncio.sleep(target - now)

        ts_90d = int((time.time() - 90 * 86400) * 1000)
        ts_60d = int((time.time() - 60 * 86400) * 1000)
        ts_30d = int((time.time() - 30 * 86400) * 1000)

        from src.db.connection import acquire
        async with acquire() as conn:
            # Candles not in trade_context older than 90d
            await conn.execute(
                """
                DELETE FROM candles
                WHERE ts < $1
                  AND id NOT IN (SELECT candle_id FROM trade_context)
                """,
                ts_90d,
            )
            # Signals older than 60d
            await conn.execute("DELETE FROM signals WHERE ts < $1", ts_60d)
            # Events older than 30d
            await conn.execute("DELETE FROM events WHERE ts < $1", ts_30d)
            # VACUUM ANALYZE for performance
            await conn.execute("VACUUM ANALYZE candles")
            await conn.execute("VACUUM ANALYZE signals")
            await conn.execute("VACUUM ANALYZE events")

        await db.write_event(
            cfg.bot_id, session_id, cfg.env.value,
            "INFO", "system", "retention_cleanup_complete",
            {"ts_90d": ts_90d, "ts_60d": ts_60d, "ts_30d": ts_30d},
        )
