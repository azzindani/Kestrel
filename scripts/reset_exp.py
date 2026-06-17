#!/usr/bin/env python3
"""
Reset ONLY the experimental cohort's evaluation slate — wipe trade/signal/event/
context rows for bots whose strategy label starts with "exp_", so a freshly
swapped-in cohort candidate is judged on a clean dataset.

Unlike reset_dev.py this is SURGICAL: the 120 baseline bots keep all their data,
candles are never touched, and the GLOBAL pattern_memory (shared with baseline)
is left alone. Used by the research loop when it rotates the experimental cohort.

Scope: env='dev' AND split_part(bot_id,'-',4) ~ '^exp_'  (the dashboard's
strategy segment). See scripts/build_exp_cohort.py for how cohort bots are named.

Safety:
    - Refuses to run unless ENV=dev.
    - Deletes in FK-safe order. candles / baseline / pattern_memory untouched.
    - Requires --yes to actually delete (dry-run prints counts otherwise).

Run:
    python3 scripts/reset_exp.py            # dry run: show cohort blast radius
    python3 scripts/reset_exp.py --yes      # wipe the cohort slate only
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# split_part(bot_id,'-',4) is the strategy segment; '^exp_' = cohort only.
_SCOPE = "env = 'dev' AND split_part(bot_id, '-', 4) ~ '^exp_'"
_TRADE_SCOPE = f"trade_id IN (SELECT id FROM trades WHERE {_SCOPE})"
_DELETE_STEPS = [
    ("trade_context", f"DELETE FROM trade_context WHERE {_TRADE_SCOPE}"),
    ("events", f"DELETE FROM events WHERE {_SCOPE}"),
    ("signals", f"DELETE FROM signals WHERE {_SCOPE}"),
    ("trades", f"DELETE FROM trades WHERE {_SCOPE}"),
    # pattern_memory is global/shared with baseline — intentionally NOT wiped here.
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
        print(f"refusing to run: ENV={env!r}, expected 'dev'. reset_exp.py never touches prod.", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    try:
        for table in ("trades", "signals", "events", "trade_context"):
            if table == "trade_context":
                n = await conn.fetchval(f"SELECT count(*) FROM trade_context WHERE {_TRADE_SCOPE}")
            else:
                n = await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {_SCOPE}")
            print(f"  {table:16s} {n:>10} cohort rows")

        if not apply:
            print("dry run — pass --yes to delete. candles / baseline / pattern_memory KEPT.")
            return 0

        async with conn.transaction():
            for name, sql in _DELETE_STEPS:
                status = await conn.execute(sql)
                print(f"  wiped {name}: {status}")
        print("cohort slate reset complete. baseline + candles kept.")
        return 0
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Reset the experimental-cohort slate (keeps baseline + candles).")
    ap.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_run(args.yes)))


if __name__ == "__main__":
    main()
