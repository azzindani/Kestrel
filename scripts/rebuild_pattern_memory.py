#!/usr/bin/env python3
"""Pattern-memory rebuilder — connects the learning loop that was only half-wired.

Kestrel has always specified an adaptive loop (§11: "pattern memory read at eval /
write after close"). The READ half is live: detector.py consults pattern_memory on
every evaluation and feeds it to should_suppress / adjust_confidence. The WRITE half
was never connected — `upsert_pattern_memory` had zero production callers and
`updated_memory` was called only by its own unit tests, so the table sat empty and
those two functions were silent no-ops for the project's entire life.

WHY A REBUILDER RATHER THAN A CLOSE-TRADE HOOK. The obvious fix is to upsert from
_close_position, but `upsert_pattern_memory` writes caller-computed totals
(`SET sample_count = EXCLUDED.sample_count`) rather than incrementing atomically,
and the key (pattern, direction, session, regime) is SHARED across the whole fleet.
With 644 bots closing concurrently, two closes on the same key both read 10, both
write 11, and a trade is silently lost. Recomputing the aggregate from `trades` —
which §11 already makes the authoritative record — is correct by construction, has
exactly one writer, and is idempotent: re-running it can never double-count.

WHAT FEEDS IT vs WHAT ACTS ON IT. pattern_memory has no env column (§19), so its
rows are global. Every tier's trades therefore feed the statistics, which is what
makes them robust — dev is by far the largest contributor. Only the curated tiers
ACT on them; see MEMORY_ACTIVE_ENVS in src/signal/memory.py, which excludes dev so
it keeps measuring the raw strategy. That split is exactly the owner's tier model:
dev collects, lab and staging use.

The session key comes from src.config.get_trading_session, the SAME function the
detector uses to build its lookup key. Deriving it independently here would be the
classic way to rebuild an inert loop — keys that never match anything.

Runs as an aux compose service (like db_janitor / monitor_host). Same
entrypoint-override gotcha: the image's docker-entrypoint.sh hardcodes the daemon
and ignores `command:`, so the service MUST set `entrypoint:`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from dotenv import load_dotenv

from src.config import AppConfig, get_trading_session
from src.db import connection as db_conn
from src.db import writer as db

_BOT_ID = "aux-pattern-memory"
_SESSION = "pattern-memory"

_INTERVAL_S = int(os.environ.get("PATTERN_MEMORY_INTERVAL_S", str(3600)))
# Trades closed by fleet maintenance or crash recovery are not strategy outcomes and
# must never shape what the detector believes about a pattern.
_EXCLUDED_REASONS = ("orphaned_crash_recovery", "manual")


@dataclass(frozen=True)
class MemoryRow:
    """One aggregated (pattern, direction, session, regime) cell."""

    pattern: str
    direction: str
    session: str
    regime: str
    sample_count: int
    win_count: int
    win_rate: float
    avg_pnl_pct: float


def aggregate(trades: list[dict[str, Any]]) -> list[MemoryRow]:
    """Pure: fold closed trades into per-key performance statistics.

    `regime` falls back to 'UNKNOWN' to match the detector, which uses
    `candles[-1].regime or "UNKNOWN"` when building its lookup key — a different
    fallback here would silently miss every unknown-regime row.
    """
    buckets: dict[tuple[str, str, str, str], list[tuple[bool, float]]] = {}
    for t in trades:
        pattern = t.get("pattern")
        direction = t.get("direction")
        entry_ts = t.get("entry_ts")
        pnl_pct = t.get("pnl_pct")
        if not pattern or not direction or entry_ts is None or pnl_pct is None:
            continue  # explicit absence: an incomplete row teaches nothing
        key = (
            str(pattern),
            str(direction),
            get_trading_session(int(entry_ts)).value,
            str(t.get("regime") or "UNKNOWN"),
        )
        buckets.setdefault(key, []).append((float(pnl_pct) > 0.0, float(pnl_pct)))

    rows: list[MemoryRow] = []
    for (pattern, direction, session, regime), items in sorted(buckets.items()):
        wins = sum(1 for won, _ in items if won)
        rows.append(
            MemoryRow(
                pattern=pattern,
                direction=direction,
                session=session,
                regime=regime,
                sample_count=len(items),
                win_count=wins,
                win_rate=round(wins / len(items), 4),
                avg_pnl_pct=round(sum(p for _, p in items) / len(items), 6),
            )
        )
    return rows


async def load_closed_trades(env_filter: Optional[str]) -> list[dict[str, Any]]:
    """I/O shell: closed trades with the regime recorded on their firing signal.

    regime lives on `signals`, not `trades` (§19), so it is recovered by the
    signals.trade_id link. A trade whose signal row is gone still counts, under
    the same 'UNKNOWN' regime the detector falls back to.
    """
    sql = """
        SELECT t.pattern, t.direction, t.entry_ts, t.pnl_pct, s.regime
        FROM trades t
        LEFT JOIN signals s ON s.trade_id = t.id
        WHERE t.exit_ts IS NOT NULL
          AND t.pnl_pct IS NOT NULL
          AND t.close_reason <> ALL($1::text[])
    """
    args: list[Any] = [list(_EXCLUDED_REASONS)]
    if env_filter:
        sql += " AND t.env = $2"
        args.append(env_filter)
    async with db_conn.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def rebuild(cfg: AppConfig, env_filter: Optional[str]) -> dict[str, int]:
    """I/O shell: recompute every memory cell and upsert it. Idempotent."""
    trades = await load_closed_trades(env_filter)
    rows = aggregate(trades)
    now_ms = int(time.time() * 1000)
    for row in rows:
        await db.upsert_pattern_memory(
            row.pattern,
            row.direction,
            row.session,
            row.regime,
            row.sample_count,
            row.win_count,
            row.win_rate,
            row.avg_pnl_pct,
            now_ms,
        )

    # Cells at or past should_suppress()'s thresholds are the ones that will change
    # live behaviour in the acting tiers, so surface that count rather than leaving
    # it to be discovered by a drop in trade rate.
    actionable = sum(1 for r in rows if r.sample_count >= 20 and r.win_rate < 0.35)
    stats = {"trades": len(trades), "cells": len(rows), "suppressing_cells": actionable}
    await db.write_event(_BOT_ID, _SESSION, cfg.env.value, "INFO", "system", "pattern_memory_rebuilt", stats)
    return stats


async def run(args: argparse.Namespace) -> None:
    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    await db_conn.init_pool(cfg)
    try:
        while True:
            await rebuild(cfg, args.env)
            if args.once:
                return
            await asyncio.sleep(_INTERVAL_S)
    finally:
        await db_conn.close_pool()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="rebuild once and exit")
    ap.add_argument(
        "--env",
        default=None,
        help="restrict the SOURCE trades to one env (default: all tiers feed the stats)",
    )
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
