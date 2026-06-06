#!/usr/bin/env python3
"""
Reset the DEV evaluation slate — wipe trade/signal/event/context/memory rows so a
newly-deployed algorithm is judged on a clean dataset. KEEPS candles (training data
and the indicator buffer bootstrap) and heartbeats.

Standard procedure after deploying a new strategy (see memory
`feedback_reset_after_new_algorithm`): stop the daemon → run this → restart.

Safety:
    - Refuses to run unless ENV=dev (never touches prod data).
    - Deletes in FK-safe order. candles are never touched.
    - Requires --yes to actually delete (dry-run prints counts otherwise).

Run:
    python3 scripts/reset_dev.py            # dry run: show what would be deleted
    python3 scripts/reset_dev.py --yes      # actually wipe the dev slate
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Order matters: children before parents (FKs), then global learned state.
_DEV_SCOPED = ("events", "signals")  # have an env column + trade_id FK
_DELETE_STEPS = [
    # trade_context has no env column — scope via its parent trades.
    ("trade_context", "DELETE FROM trade_context WHERE trade_id IN (SELECT id FROM trades WHERE env = 'dev')"),
    ("events", "DELETE FROM events WHERE env = 'dev'"),
    ("signals", "DELETE FROM signals WHERE env = 'dev'"),
    ("trades", "DELETE FROM trades WHERE env = 'dev'"),
    # pattern_memory is global learned state (no env) — wiped for a clean slate.
    ("pattern_memory", "DELETE FROM pattern_memory"),
]


async def _run(apply: bool) -> int:
    try:
        import asyncpg
        from dotenv import load_dotenv
    except ImportError as exc:
        print(f"missing runtime dependency: {exc}. Run on the VPS / installed env.", file=sys.stderr)
        return 2

    load_dotenv()
    env = (os.getenv("ENV") or "").lower()
    if env != "dev":
        print(f"refusing to run: ENV={env!r}, expected 'dev'. reset_dev.py never touches prod.", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    try:
        # Show pre-counts (dev scope) so the operator sees the blast radius.
        for table in ("trades", "signals", "events", "trade_context", "pattern_memory"):
            if table == "pattern_memory":
                n = await conn.fetchval("SELECT count(*) FROM pattern_memory")
            elif table == "trade_context":
                n = await conn.fetchval(
                    "SELECT count(*) FROM trade_context WHERE trade_id IN (SELECT id FROM trades WHERE env='dev')"
                )
            else:
                n = await conn.fetchval(f"SELECT count(*) FROM {table} WHERE env='dev'")
            print(f"  {table:16s} {n:>10} rows")

        if not apply:
            print("dry run — pass --yes to delete. candles are KEPT.")
            return 0

        async with conn.transaction():
            for name, sql in _DELETE_STEPS:
                status = await conn.execute(sql)
                print(f"  wiped {name}: {status}")
        print("dev slate reset complete. candles kept. restart the daemon for a clean run.")
        return 0
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Reset the dev evaluation slate (keeps candles).")
    ap.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_run(args.yes)))


if __name__ == "__main__":
    main()
