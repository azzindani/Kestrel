#!/usr/bin/env python3
"""
Promote Phase-1 lab winners into the Phase-2 STAGING fleet (bots.staging.json).

The labs (ENV=dev, SimulationExecution) run many (strategy × pair) cells. This
script reads their realised performance from the trades table (env='dev'), ranks
the cells, and writes a small curated bots file for the quarantine phase — each
winner re-emitted with a `staging-` bot_id so its rows are isolated by env +
prefix (§6/§19). Staging then runs those bots against the BingX VST demo venue
(virtual money) via docker-compose.staging.yml.

Ranking (descending): net PnL, among cells with >= --min-trades closed trades
and positive net PnL (never promote a losing cell). Win rate / avg-R are shown.

If the lab has not yet produced enough closed trades (e.g. just deployed, or 4h
so trades are sparse), it falls back to the VALIDATED DEFAULT cells — the ones
that cleared the §30 out-of-sample bar in the 365d walk-forward (ETH on both
mom_adx and triple_mom; DOGE on triple_mom) — and says so on stderr.

This is a SELECTION tool: it never touches the venue and never trades. Promotion
to Phase 3 (real money) is human-gated by §18 and is NOT automated here.

Run (inside the labs container so DB_HOST=postgres resolves):
    docker compose exec -T kestrel python3 scripts/promote_to_staging.py --stdout > bots.staging.json
    docker compose exec -T kestrel python3 scripts/promote_to_staging.py            # dry-run table to stderr
    docker compose exec -T kestrel python3 scripts/promote_to_staging.py \
        --manual ETHUSDT:triple_mom,DOGEUSDT:triple_mom --stdout > bots.staging.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# Exit + risk profile for staging bots. KEEP IN SYNC with
# scripts/build_momentum_lab.py:_EXIT (the validated "medium" bracket: best
# confluence-momentum variant by expectancy on the 2026-06-14 4h/365d sweep).
_EXIT = {
    "tp_atr_multiplier": 2.0,
    "sl_atr_multiplier": 1.0,
    "max_hold_candles": 12,
    "trailing_enabled": True,
    "trail_activation_r": 1.0,
    "trail_distance_r": 1.0,
    "volume_ratio_min": 1.1,
    "adx_strong_min": 25.0,
    "max_loss_pct_per_trade": 0.02,
}

# Cells that cleared the §30 OOS bar in the walk-forward (see memory
# project_strategy_no_edge_on_real_data). Used only when the live lab lacks data.
_VALIDATED_DEFAULTS = [
    ("ETHUSDT", "triple_mom"),
    ("ETHUSDT", "mom_adx"),
    ("DOGEUSDT", "triple_mom"),
]

_TIMEFRAME = "4h"

# Leaderboard over the labs (env='dev') realised trades. bot_id layout is
# dev-{TOKEN}-4h-{strategy}-01 → split_part('-',2)=TOKEN, ('-',4)=strategy
# (mom_adx/triple_mom keep their underscore, so '-' split leaves them whole).
_LEADERBOARD_SQL = """
SELECT split_part(bot_id, '-', 2)                                AS token,
       split_part(bot_id, '-', 4)                                AS strategy,
       COUNT(*)                                                  AS n,
       AVG(CASE WHEN pnl_net_usdt > 0 THEN 1.0 ELSE 0.0 END)     AS win_rate,
       SUM(pnl_net_usdt)                                         AS net_pnl,
       AVG(pnl_net_usdt)                                         AS avg_pnl
FROM trades
WHERE env = 'dev' AND exit_ts IS NOT NULL AND pnl_net_usdt IS NOT NULL
GROUP BY 1, 2
HAVING COUNT(*) >= $1
ORDER BY SUM(pnl_net_usdt) DESC;
"""


def _token_to_pair(token: str) -> str:
    """ETHUSDT -> ETH/USDT (all lab pairs are USDT-quoted)."""
    if token.endswith("USDT"):
        return f"{token[:-4]}/USDT"
    if token.endswith("USD"):
        return f"{token[:-3]}/USD"
    raise ValueError(f"cannot derive pair from bot_id token {token!r}")


def _bot_entry(token: str, strategy: str) -> dict:
    return {
        "bot_id": f"staging-{token}-{_TIMEFRAME}-{strategy}-01",
        "pair": _token_to_pair(token),
        "timeframe_entry": _TIMEFRAME,
        "timeframe_regime": _TIMEFRAME,
        "max_active_buckets": 1,
        "strategy": strategy,
        "patterns": [strategy],
        "params": dict(_EXIT),
    }


def _parse_manual(spec: str) -> list[tuple[str, str]]:
    """'ETHUSDT:triple_mom,DOGEUSDT:mom_adx' -> [(token, strategy), ...]."""
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


async def _leaderboard(min_trades: int) -> list[dict]:
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
        rows = await conn.fetch(_LEADERBOARD_SQL, min_trades)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def _run(args: argparse.Namespace) -> int:
    if args.manual:
        cells = _parse_manual(args.manual)
        print(f"[promote] manual selection: {len(cells)} cell(s)", file=sys.stderr)
    else:
        try:
            board = await _leaderboard(args.min_trades)
        except ImportError as exc:
            print(f"missing runtime dependency: {exc}. Run inside the container.", file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 — surface DB/connection errors to the operator
            print(f"[promote] leaderboard query failed: {exc}", file=sys.stderr)
            return 2

        winners = [r for r in board if (r["net_pnl"] or 0) > 0][: args.top]
        if winners:
            print(
                f"[promote] labs leaderboard (>= {args.min_trades} trades, net>0), "
                f"top {len(winners)} of {len(board)} qualifying cells:",
                file=sys.stderr,
            )
            for r in winners:
                print(
                    f"  {r['token']:<10} {r['strategy']:<11} "
                    f"n={r['n']:<4} win={float(r['win_rate']):.0%} "
                    f"net=${float(r['net_pnl']):+.2f} avg=${float(r['avg_pnl']):+.3f}",
                    file=sys.stderr,
                )
            cells = [(r["token"], r["strategy"]) for r in winners]
        else:
            print(
                f"[promote] labs have no qualifying cells yet (need >= {args.min_trades} "
                "closed trades with net>0). Falling back to VALIDATED DEFAULTS "
                "(§30-OOS cells from the walk-forward).",
                file=sys.stderr,
            )
            cells = list(_VALIDATED_DEFAULTS)

    fleet = [_bot_entry(token, strategy) for token, strategy in cells]
    payload = json.dumps(fleet, indent=2)

    if args.stdout:
        print(payload)
    else:
        with open(args.out, "w") as f:
            f.write(payload + "\n")
        print(f"[promote] wrote {args.out}: {len(fleet)} staging bot(s)", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Promote lab winners into the staging fleet.")
    p.add_argument("--top", type=int, default=5, help="max cells to promote (default 5)")
    p.add_argument("--min-trades", type=int, default=10, help="min closed trades for a cell to qualify (default 10)")
    p.add_argument(
        "--manual",
        type=str,
        default=None,
        help="hand-pick cells, e.g. 'ETHUSDT:triple_mom,DOGEUSDT:mom_adx' (skips the leaderboard)",
    )
    p.add_argument("--out", type=str, default="bots.staging.json", help="output path (default bots.staging.json)")
    p.add_argument("--stdout", action="store_true", help="print JSON to stdout (logs go to stderr)")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
