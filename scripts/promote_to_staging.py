#!/usr/bin/env python3
"""
Promote Phase-1 lab best-performers into the Phase-2 STAGING fleet (bots.staging.json).

The labs (ENV=dev) run many (pair × timeframe × strategy) cells. This script reads
their realised performance from the trades table (env='dev'), ranks the cells by
EXPECTANCY (avg net PnL per trade — the right metric; win rate alone is gameable),
and writes a small curated fleet for the quarantine phase. Each winner is a CLONE
of its exact dev bot config (same timeframe + exit params + patterns) re-emitted
with a `staging-` bot_id, so a promoted bot keeps the bracket it was measured with
— never a hardcoded one. Rows isolate by env='staging' + the staging- prefix (§19).

Ranking (descending expectancy): among cells with >= --min-trades closed trades AND
positive net PnL (never promote a losing cell). When the lab has no positive cell
yet (e.g. just reset, or the only +EV signals are slow 1h crosses that haven't
fired), it falls back to the LOCKBOX-VALIDATED LEADS — the signals shown +EV in
BOTH the recent year and the untouched prior-year lockbox (macd_cross, macd_rsi;
RESEARCH_LOOP iter 18/22) — cloned from the dev fleet, and says so on stderr.

This is a SELECTION tool: it never touches a venue and never trades. Promotion to
Phase 3 (real money) is human-gated by §18 and is NOT automated here.

Run (inside the labs container so DB_HOST=postgres resolves):
    docker compose exec -T kestrel python3 scripts/promote_to_staging.py --stdout > bots.staging.json
    docker compose exec -T kestrel python3 scripts/promote_to_staging.py            # dry-run table to stderr
    docker compose exec -T kestrel python3 scripts/promote_to_staging.py \
        --manual ETHUSDT:macd_rsi,DOGEUSDT:macd_cross --stdout > bots.staging.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# Lockbox-validated leads — the only signals +EV in BOTH recent + prior-year lockbox
# (RESEARCH_LOOP iter 18/22). Used as the staging seed when the live lab has no
# positive cell yet. Cloned from the dev fleet (so their exit brackets come along).
_LOCKBOX_LEADS = ["macd_cross", "macd_rsi"]

# Rank by expectancy (avg net PnL/trade) among cells with enough trades AND net>0.
# bot_id layout is dev-{TOKEN}-{tf}-{strategy}-01; strategies keep their underscores
# (mom_adx, macd_cross) so split_part('-',4) leaves them whole. Use the timeframe
# column directly rather than parsing it out of the id.
_LEADERBOARD_SQL = """
SELECT split_part(bot_id, '-', 2)                            AS token,
       timeframe                                             AS tf,
       split_part(bot_id, '-', 4)                            AS strategy,
       COUNT(*)                                              AS n,
       AVG(CASE WHEN pnl_net_usdt > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
       SUM(pnl_net_usdt)                                     AS net_pnl,
       AVG(pnl_net_usdt)                                     AS avg_pnl
FROM trades
WHERE env = 'dev' AND exit_ts IS NOT NULL AND pnl_net_usdt IS NOT NULL
GROUP BY 1, 2, 3
HAVING COUNT(*) >= $1
   AND SUM(pnl_net_usdt) > 0
   AND AVG(CASE WHEN pnl_net_usdt > 0 THEN 1.0 ELSE 0.0 END) >= $2
ORDER BY AVG(pnl_net_usdt) DESC;
"""


def _parse_bot_id(bot_id: str) -> tuple[str, str, str]:
    """dev-BTCUSDT-1h-macd_rsi-01 -> ('BTCUSDT', '1h', 'macd_rsi')."""
    parts = bot_id.split("-")
    if len(parts) < 5:
        raise ValueError(f"unexpected bot_id layout: {bot_id!r}")
    token, tf = parts[1], parts[2]
    strategy = "-".join(parts[3:-1])  # tolerate (none today) hyphenated strategies
    return token, tf, strategy


def _load_dev_fleet(path: str) -> tuple[list[dict], dict]:
    """Read the dev fleet and index it by (token, tf, strategy) and (token, strategy)."""
    with open(path) as f:
        bots = json.load(f)
    idx: dict[tuple, dict] = {}
    for b in bots:
        try:
            token, tf, strategy = _parse_bot_id(b["bot_id"])
        except (KeyError, ValueError):
            continue
        idx[(token, tf, strategy)] = b
        idx.setdefault((token, strategy), b)  # tf-agnostic fallback for --manual
    return bots, idx


def _stage_clone(dev_bot: dict) -> dict:
    """Clone a dev bot config into a staging bot (same tf/params/patterns, staging- id)."""
    b = dict(dev_bot)
    b["bot_id"] = "staging-" + dev_bot["bot_id"].split("-", 1)[1]
    return b


def _parse_manual(spec: str) -> list[tuple[str, str]]:
    """'ETHUSDT:macd_rsi,DOGEUSDT:macd_cross' -> [(token, strategy), ...]."""
    cells: list[tuple[str, str]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"manual cell {part!r} must be TOKEN:strategy")
        token, strategy = part.split(":", 1)
        cells.append((token.strip().upper(), strategy.strip()))
    return cells


def _lockbox_seed(bots: list[dict]) -> list[dict]:
    """Clone every dev bot whose strategy is a lockbox-validated lead."""
    seed = []
    for b in bots:
        try:
            _, _, strategy = _parse_bot_id(b["bot_id"])
        except (KeyError, ValueError):
            continue
        if strategy in _LOCKBOX_LEADS:
            seed.append(_stage_clone(b))
    return seed


async def _leaderboard(min_trades: int, min_win_frac: float) -> list[dict]:
    import asyncpg
    from dotenv import load_dotenv

    load_dotenv()
    conn = await asyncpg.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    try:
        rows = await conn.fetch(_LEADERBOARD_SQL, min_trades, min_win_frac)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


def _resolve(idx: dict, token: str, tf: str | None, strategy: str) -> dict | None:
    if tf is not None and (token, tf, strategy) in idx:
        return idx[(token, tf, strategy)]
    return idx.get((token, strategy))


async def _run(args: argparse.Namespace) -> int:
    try:
        bots, idx = _load_dev_fleet(args.bots)
    except OSError as exc:
        print(f"[promote] cannot read dev fleet {args.bots!r}: {exc}", file=sys.stderr)
        return 2

    fleet: list[dict] = []
    if args.manual:
        for token, strategy in _parse_manual(args.manual):
            dev = _resolve(idx, token, None, strategy)
            if dev is None:
                print(f"[promote] manual cell {token}:{strategy} not in dev fleet — skipped", file=sys.stderr)
                continue
            fleet.append(_stage_clone(dev))
        print(f"[promote] manual selection: {len(fleet)} cell(s)", file=sys.stderr)
    else:
        try:
            board = await _leaderboard(args.min_trades, args.min_win / 100.0)
        except ImportError as exc:
            print(f"missing runtime dependency: {exc}. Run inside the container.", file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 — surface DB/connection errors to the operator
            print(f"[promote] leaderboard query failed: {exc}", file=sys.stderr)
            return 2

        winners = board[: args.top]
        if winners:
            print(
                f"[promote] labs leaderboard (>= {args.min_trades} trades, net>0, "
                f"win>= {args.min_win:.0f}%), top {len(winners)} of {len(board)} "
                "qualifying cells by expectancy:",
                file=sys.stderr,
            )
            for r in winners:
                dev = _resolve(idx, r["token"], r["tf"], r["strategy"])
                tag = "" if dev is not None else "  (NOT IN dev fleet — skipped)"
                print(
                    f"  {r['token']:<10} {r['tf']:<3} {r['strategy']:<13} "
                    f"n={r['n']:<4} win={float(r['win_rate']):.0%} "
                    f"net=${float(r['net_pnl']):+.2f} avg=${float(r['avg_pnl']):+.4f}{tag}",
                    file=sys.stderr,
                )
                if dev is not None:
                    fleet.append(_stage_clone(dev))
        if not fleet:
            print(
                f"[promote] no qualifying lab cells (need >= {args.min_trades} closed "
                f"trades, net>0, win>= {args.min_win:.0f}%). Falling back to "
                f"LOCKBOX-VALIDATED LEADS ({', '.join(_LOCKBOX_LEADS)}) cloned from the "
                "dev fleet as the seed until a live cell qualifies.",
                file=sys.stderr,
            )
            fleet = _lockbox_seed(bots)

    payload = json.dumps(fleet, indent=2)
    if args.stdout:
        print(payload)
    else:
        with open(args.out, "w") as f:
            f.write(payload + "\n")
        print(f"[promote] wrote {args.out}: {len(fleet)} staging bot(s)", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Promote lab best-performers into the staging fleet.")
    p.add_argument("--top", type=int, default=8, help="max cells to promote (default 8)")
    p.add_argument("--min-trades", type=int, default=10, help="min closed trades for a cell to qualify (default 10)")
    p.add_argument(
        "--min-win",
        type=float,
        default=0.0,
        help="min win-rate %% for a cell to qualify, ON TOP of net>0 (default 0 = expectancy only; "
        "the loop passes 50 so staging only holds bots that BOTH win >50%% AND make money)",
    )
    p.add_argument("--bots", type=str, default="bots.json", help="dev fleet to clone winners from (default bots.json)")
    p.add_argument(
        "--manual",
        type=str,
        default=None,
        help="hand-pick cells, e.g. 'ETHUSDT:macd_rsi,DOGEUSDT:macd_cross' (skips the leaderboard)",
    )
    p.add_argument("--out", type=str, default="bots.staging.json", help="output path (default bots.staging.json)")
    p.add_argument("--stdout", action="store_true", help="print JSON to stdout (logs go to stderr)")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
