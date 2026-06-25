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

# strategy segment of bot_id (dev-BTCUSDT-1h-macd_rsi-01 -> macd_rsi); used to SCOPE a
# surgical reset to only the cohort(s) whose config changed this deploy. An ADDITIVE deploy
# (brand-new bot_ids) needs no reset at all — those rows don't exist yet — so the loop should
# reset only when an EXISTING bot_id's config changed, and then only THAT strategy. A full wipe
# (no --strategy) stays available for a deliberate whole-program restart only.
_STRAT_SEG = "split_part(bot_id, '-', 4)"


def _build_steps(strategies: list[str] | None) -> list[tuple[str, str, tuple]]:
    """Return (table, sql, args) delete steps. Children before parents (FK-safe).

    When `strategies` is given the wipe is SURGICAL — only those cohorts' dev rows, and the
    GLOBAL pattern_memory is left intact (it is shared across cohorts; wiping it on a surgical
    reset would corrupt the cohorts that did NOT change). A None scope is the full dev wipe.
    """
    if strategies:
        # trade_context has no bot_id/env — scope it through its parent trades.
        tctx = (
            "DELETE FROM trade_context WHERE trade_id IN "
            f"(SELECT id FROM trades WHERE env='dev' AND {_STRAT_SEG} = ANY($1::text[]))"
        )
        row = f"env='dev' AND {_STRAT_SEG} = ANY($1::text[])"
        return [
            ("trade_context", tctx, (strategies,)),
            ("events", f"DELETE FROM events WHERE {row}", (strategies,)),
            ("signals", f"DELETE FROM signals WHERE {row}", (strategies,)),
            ("trades", f"DELETE FROM trades WHERE {row}", (strategies,)),
            # pattern_memory intentionally NOT wiped on a scoped reset (global/shared).
        ]
    return [
        ("trade_context", "DELETE FROM trade_context WHERE trade_id IN (SELECT id FROM trades WHERE env='dev')", ()),
        ("events", "DELETE FROM events WHERE env='dev'", ()),
        ("signals", "DELETE FROM signals WHERE env='dev'", ()),
        ("trades", "DELETE FROM trades WHERE env='dev'", ()),
        # full wipe only: pattern_memory is global learned state — cleared for a true clean slate.
        ("pattern_memory", "DELETE FROM pattern_memory", ()),
    ]


async def _run(apply: bool, strategies: list[str] | None) -> int:
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
    steps = _build_steps(strategies)
    scope_txt = f"strategy(s) {strategies}" if strategies else "ALL env='dev'"
    try:
        print(f"reset scope: {scope_txt}  (candles + microstructure ALWAYS kept)")
        # Show pre-counts within the SAME scope so the operator sees the real blast radius.
        for table, _, _ in steps:
            if table == "trade_context":
                if strategies:
                    n = await conn.fetchval(
                        "SELECT count(*) FROM trade_context WHERE trade_id IN "
                        f"(SELECT id FROM trades WHERE env='dev' AND {_STRAT_SEG} = ANY($1::text[]))",
                        strategies,
                    )
                else:
                    n = await conn.fetchval(
                        "SELECT count(*) FROM trade_context WHERE trade_id IN (SELECT id FROM trades WHERE env='dev')"
                    )
            elif table == "pattern_memory":
                n = await conn.fetchval("SELECT count(*) FROM pattern_memory")
            elif strategies:
                n = await conn.fetchval(
                    f"SELECT count(*) FROM {table} WHERE env='dev' AND {_STRAT_SEG} = ANY($1::text[])", strategies
                )
            else:
                n = await conn.fetchval(f"SELECT count(*) FROM {table} WHERE env='dev'")
            print(f"  {table:16s} {n:>10} rows")

        if not apply:
            print("dry run — pass --yes to delete. candles are KEPT.")
            return 0

        async with conn.transaction():
            for name, sql, sql_args in steps:
                status = await conn.execute(sql, *sql_args)
                print(f"  wiped {name}: {status}")
        print("dev slate reset complete. candles kept. restart the daemon for a clean run.")
        return 0
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Reset the dev evaluation slate (keeps candles).")
    ap.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="SURGICAL scope: comma-separated strategy label(s) (the 4th bot_id segment, e.g. "
        "'cci_mom' or 'macd_cross,macd_rsi'). Wipes ONLY those cohorts' dev rows and KEEPS the "
        "global pattern_memory. Omit for a full dev wipe (deliberate whole-program restart only).",
    )
    args = ap.parse_args()
    strategies = [s.strip() for s in args.strategy.split(",") if s.strip()] if args.strategy else None
    raise SystemExit(asyncio.run(_run(args.yes, strategies)))


if __name__ == "__main__":
    main()
