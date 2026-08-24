#!/usr/bin/env python3
"""Database janitor — enforces the §19 retention policy and reports DB growth.

Motivation (2026-08-24): the §15 `cleanup.sh` contract ("03:00 UTC daily:
DELETE unlinked candles >90d · signals >60d · events >30d · VACUUM ANALYZE")
was never actually scheduled — the host has no crontab, so retention had NEVER
run in the project's lifetime. candles held 145 days (1.6 GB) against a 90-day
policy and `microstructure` grew wholly unbounded (1.2 GB in 64 days, ~19 MB/day)
because it post-dates schema.py and no policy ever covered it.

This service is the missing scheduler. It runs the sweep itself rather than
shelling out to cleanup.sh so it can (a) batch the deletes — the one-shot
`DELETE ... WHERE id NOT IN (SELECT candle_id FROM trade_context)` in cleanup.sh
takes an hours-long lock on a 1.4M-row table — and (b) emit a structured size
report each pass, which is what makes DB growth visible in Grafana instead of
being discovered during an outage.

Retention is CONFIGURABLE per table via env, so widening capacity is a config
change, not a code change. The §19 defaults are the documented policy; the
`microstructure` default is deliberately generous (365d) — it is irreplaceable
research data (no historical L2 feed exists to re-fetch it) and Kestrel's whole
DB is ~3.7 GB of a 193 GB disk, so the bound exists to stop runaway growth, not
to reclaim space today. Set any *_RETENTION_DAYS to 0 to disable that table's
sweep entirely (same 0-means-off convention as PORTFOLIO_*_PCT).

Deletes run in bounded batches with a pause between them so the fleet's own
writes never queue behind the janitor. Safety rails: candles linked from
trade_context are NEVER deleted (§19 — they are labelled training data), and
trades/trade_context/pattern_memory are never touched at all.

Runs as its own aux compose service (like monitor_host.py / the microstructure
recorder). Same entrypoint-override gotcha: the image's docker-entrypoint.sh
hardcodes the daemon and ignores `command:`, so the service MUST set
`entrypoint:`.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

from src.config import AppConfig
from src.db import connection as db_conn
from src.db import writer as db
from src.notify.telegram import TelegramNotifier

_BOT_ID = "aux-db-janitor"
_SESSION = "db-janitor"

_SWEEP_HOUR_UTC = int(os.environ.get("JANITOR_SWEEP_HOUR_UTC", "3"))  # §15: 03:00 UTC daily
_REPORT_INTERVAL_S = int(os.environ.get("JANITOR_REPORT_INTERVAL_S", str(3600)))
_BATCH_ROWS = int(os.environ.get("JANITOR_BATCH_ROWS", "20000"))
_BATCH_PAUSE_S = float(os.environ.get("JANITOR_BATCH_PAUSE_S", "0.2"))
_DB_WARN_GB = float(os.environ.get("JANITOR_DB_WARN_GB", "20"))

_MS_PER_DAY = 86_400_000


@dataclass(frozen=True)
class SweepRule:
    """One retention rule. `days` <= 0 disables it.

    `label` names the rule in config/reporting and may differ from `table`, so one
    table can carry several windows (candles are swept per-timeframe). `filter_sql`
    narrows the rule to a subset of rows; `guard_sql` protects rows that must never
    be deleted regardless of age (§19).
    """

    label: str
    table: str
    days: int
    guard_sql: Optional[str] = None
    filter_sql: Optional[str] = None


def load_rules(env: dict[str, str]) -> list[SweepRule]:
    """Pure: build the retention rule set from environment config.

    §19 defaults; `microstructure` is bounded generously (see module docstring).

    NOT swept: trades / trade_context / pattern_memory are kept indefinitely (§19),
    and `heartbeats` is deliberately excluded — it holds one row per bot_id, so
    deleting stale rows would make a DEAD bot vanish from the liveness view instead
    of showing as stale. That is precisely the failure mode the fleet-liveness alarm
    in monitor_host.py depends on; heartbeat bloat is autovacuum's job, not ours.
    """
    # Training data — a candle referenced by trade_context is kept indefinitely.
    linked = "AND NOT EXISTS (SELECT 1 FROM trade_context tc WHERE tc.candle_id = t.id)"
    # Fast-timeframe candles get their OWN, much shorter window, because they are the
    # binding constraint on fleet size. Candles are stored PER bot_id
    # (UNIQUE(bot_id,pair,timeframe,ts)), so N bots on the same pair+timeframe store N
    # copies of every candle: at 1220 bytes/row a single 5m bot writes ~351 KB/day, and
    # a 204-bot 5m cohort held for the 90-day §19 window would alone be ~6.4 GB.
    # Nothing needs that depth. A bot warms up on ~120 candles (10 hours at 5m), and
    # the research harness (scripts/algo_search.py) fetches its OHLCV fresh from ccxt
    # rather than from this table — so fast-TF rows past a couple of weeks serve no
    # reader at all. Keeping 1h+ at the full 90 days preserves the slow-TF history that
    # bootstrapping genuinely depends on (720 candles ≈ 30 days at 1h).
    fast_tfs = env.get("FAST_TF_LIST", "1m,3m,5m,15m")
    fast_list = ", ".join(f"'{tf.strip()}'" for tf in fast_tfs.split(",") if tf.strip())
    return [
        SweepRule(
            "candles_fast",
            "candles",
            int(env.get("CANDLES_FAST_RETENTION_DAYS", "21")),
            linked,
            f"AND t.timeframe IN ({fast_list})",
        ),
        SweepRule(
            "candles",
            "candles",
            int(env.get("CANDLES_RETENTION_DAYS", "90")),
            linked,
            f"AND t.timeframe NOT IN ({fast_list})",
        ),
        SweepRule("signals", "signals", int(env.get("SIGNALS_RETENTION_DAYS", "60"))),
        SweepRule("events", "events", int(env.get("EVENTS_RETENTION_DAYS", "30"))),
        SweepRule(
            "microstructure",
            "microstructure",
            int(env.get("MICROSTRUCTURE_RETENTION_DAYS", "365")),
        ),
    ]


def cutoff_ms(now_ms: int, days: int) -> int:
    """Pure: the unix-ms timestamp `days` before `now_ms`."""
    return now_ms - days * _MS_PER_DAY


def seconds_until_hour(now_epoch: float, hour_utc: int) -> float:
    """Pure: seconds from `now_epoch` until the next occurrence of `hour_utc`:00 UTC."""
    day = 86400.0
    seconds_today = now_epoch % day
    target = hour_utc * 3600.0
    delta = target - seconds_today
    if delta <= 0:
        delta += day
    return delta


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = $1)",
            table,
        )
    )


async def sweep_table(conn, rule: SweepRule, now_ms: int) -> dict[str, int]:
    """I/O shell: delete expired rows for one rule in bounded batches.

    Batching keeps each statement's lock short so the fleet's candle-close write
    burst is never blocked behind a multi-million-row delete.
    """
    if rule.days <= 0 or not await _table_exists(conn, rule.table):
        return {"deleted": 0, "batches": 0}

    cutoff = cutoff_ms(now_ms, rule.days)
    predicate = f"{rule.guard_sql or ''} {rule.filter_sql or ''}"
    sql = (
        f"DELETE FROM {rule.table} WHERE ctid IN ("
        f"  SELECT t.ctid FROM {rule.table} t WHERE t.ts < $1 {predicate} LIMIT {_BATCH_ROWS}"
        f")"
    )

    deleted = 0
    batches = 0
    while True:
        status = await conn.execute(sql, cutoff)
        n = int(status.rsplit(" ", 1)[-1])
        deleted += n
        batches += 1
        if n < _BATCH_ROWS:
            break
        await asyncio.sleep(_BATCH_PAUSE_S)
    return {"deleted": deleted, "batches": batches}


async def table_sizes(conn) -> dict[str, int]:
    """I/O shell: total on-disk bytes per public table, largest first."""
    rows = await conn.fetch(
        "SELECT c.relname AS name, pg_total_relation_size(c.oid) AS bytes "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
        "ORDER BY pg_total_relation_size(c.oid) DESC"
    )
    return {r["name"]: int(r["bytes"]) for r in rows}


async def run_sweep(cfg: AppConfig, notifier: TelegramNotifier) -> None:
    """I/O shell: one full retention pass — delete, VACUUM ANALYZE, report."""
    rules = load_rules(dict(os.environ))
    now_ms = int(time.time() * 1000)
    results: dict[str, dict[str, int]] = {}
    vacuum_errors: dict[str, str] = {}

    async with db_conn.acquire() as conn:
        before = await table_sizes(conn)
        for rule in rules:
            results[rule.label] = await sweep_table(conn, rule, now_ms)

        # One VACUUM per physical TABLE, not per rule — candles carries two windows.
        vacuum_targets = {r.table for r in rules if results[r.label]["deleted"] > 0}
        for table in sorted(vacuum_targets):
            # VACUUM cannot run inside a transaction block; the pool hands out
            # plain autocommit connections, so this is safe here.
            #
            # Isolated per table because a VACUUM failure must never discard the
            # sweep: the DELETEs are already committed by this point, so letting
            # the exception escape would lose the whole report and leave the run
            # looking like it never happened. Seen for real on the first sweep —
            # VACUUM died on a 64MB /dev/shm (since raised to 512m in the compose
            # override) *after* every row had been deleted successfully.
            # Reclaiming the dead space is autovacuum's job if this fails.
            try:
                await conn.execute(f"VACUUM ANALYZE {table}")
            except Exception as exc:  # noqa: BLE001 — report it, never abort the sweep
                vacuum_errors[table] = type(exc).__name__

        after = await table_sizes(conn)

    freed = sum(before.get(t, 0) for t in after) - sum(after.values())
    total_deleted = sum(r["deleted"] for r in results.values())

    await db.write_event(
        _BOT_ID,
        _SESSION,
        cfg.env.value,
        "WARN" if vacuum_errors else "INFO",
        "system",
        "db_retention_sweep",
        {
            "deleted": {t: r["deleted"] for t, r in results.items()},
            "retention_days": {r.label: r.days for r in rules},
            "bytes_freed": freed,
            "total_deleted": total_deleted,
            "vacuum_errors": vacuum_errors,
        },
    )
    if total_deleted > 0:
        await notifier.send(
            f"db retention sweep: {total_deleted:,} rows removed, {freed / 1e6:.0f} MB reclaimed",
            "INFO",
        )


async def run_report(cfg: AppConfig, notifier: TelegramNotifier, last_warn: dict[str, float]) -> None:
    """I/O shell: emit a DB size snapshot; alert if the database outgrows its budget."""
    async with db_conn.acquire() as conn:
        sizes = await table_sizes(conn)
        db_bytes = int(await conn.fetchval("SELECT pg_database_size(current_database())"))

    await db.write_event(
        _BOT_ID,
        _SESSION,
        cfg.env.value,
        "INFO",
        "system",
        "db_size_report",
        {"db_bytes": db_bytes, "tables": sizes},
    )

    db_gb = db_bytes / 1e9
    now = time.time()
    if db_gb >= _DB_WARN_GB and now - last_warn.get("db", 0.0) >= 6 * 3600:
        last_warn["db"] = now
        await db.write_event(
            _BOT_ID,
            _SESSION,
            cfg.env.value,
            "WARN",
            "system",
            "db_size_pressure",
            {"db_gb": round(db_gb, 2), "warn_gb": _DB_WARN_GB},
        )
        await notifier.send(
            f"[WARN] kestrel DB {db_gb:.1f} GB (budget {_DB_WARN_GB} GB) — widen retention or raise the budget",
            "WARN",
        )


async def run() -> None:
    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    await db_conn.init_pool(cfg)
    notifier = TelegramNotifier(cfg)
    await notifier.start()

    last_warn: dict[str, float] = {}
    try:
        # Report immediately on boot so a restart always leaves a size datapoint,
        # then settle into: hourly report, daily sweep at _SWEEP_HOUR_UTC.
        await run_report(cfg, notifier, last_warn)
        next_sweep = time.time() + seconds_until_hour(time.time(), _SWEEP_HOUR_UTC)
        while True:
            await asyncio.sleep(_REPORT_INTERVAL_S)
            await run_report(cfg, notifier, last_warn)
            if time.time() >= next_sweep:
                await run_sweep(cfg, notifier)
                next_sweep = time.time() + seconds_until_hour(time.time(), _SWEEP_HOUR_UTC)
    finally:
        await notifier.stop()
        await db_conn.close_pool()


if __name__ == "__main__":
    asyncio.run(run())
