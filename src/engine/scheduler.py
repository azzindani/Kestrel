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
    """Send ONE fleet-wide daily summary at 00:00 UTC (CLAUDE.md §27).

    Runs globally (one instance for the whole process), NOT per-bot. A single
    instance aggregates every bot's closed trades for the day into one message.
    Spawning this per-bot emitted one summary per bot (240×) every midnight —
    each with hardcoded zeros — which was the "hundreds of empty summaries" spam.
    """
    while True:
        now = time.time()
        # Seconds until next UTC midnight
        midnight = (int(now) // 86400 + 1) * 86400
        await asyncio.sleep(midnight - now)

        since_ts = midnight * 1000 - 86_400_000  # previous UTC day start

        summary = await db.get_fleet_daily_summary(cfg.env.value, since_ts)

        await db.write_event(
            cfg.bot_id,
            session_id,
            cfg.env.value,
            "INFO",
            "system",
            "daily_summary",
            summary,
        )

        if notify_fn:
            await notify_fn.daily_summary(
                {
                    "total_trades": summary["total_trades"],
                    "wins": summary["wins"],
                    "losses": summary["losses"],
                    "win_rate": summary["win_rate"],
                    "net_pnl_usdt": summary["net_pnl_usdt"],
                    "bucket_states": (f"{summary['active_positions']} open · {summary['bots_traded']} bots traded"),
                }
            )


async def trade_context_post_task(env: str, interval: float = 3600.0) -> None:
    """Link 48h-after candles for closed trades whose post-window has elapsed
    (CLAUDE.md §21).

    Runs globally (not per-bot): single sweep across all bots' trades whose
    exit_ts is older than 48h and context_post_complete=FALSE. Idempotent
    via ON CONFLICT in link_post_context.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            pending = await db.trades_pending_post_context(env=env)
        except Exception:
            continue
        for trade in pending:
            try:
                await db.link_post_context(
                    trade["id"],
                    trade["bot_id"],
                    trade["pair"],
                    trade["timeframe"],
                    trade["exit_ts"],
                )
            except Exception:
                # Per-trade failure shouldn't abort the rest of the batch.
                continue


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
            cfg.bot_id,
            session_id,
            cfg.env.value,
            "INFO",
            "system",
            "retention_cleanup_complete",
            {"ts_90d": ts_90d, "ts_60d": ts_60d, "ts_30d": ts_30d},
        )
