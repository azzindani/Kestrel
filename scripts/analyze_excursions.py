"""
MFE/MAE excursion miner — the Points Framework's S3 study
(docs/13-points-framework.md §5 S3, 2026-07-09).

For every closed live dev trade of the four 1h medium-exit leads, walk the
candles that CLOSED after entry (uncensored — candles keep recording after the
trade exits, so the path is independent of how the trade happened to close)
and compute, per candles-since-entry horizon k:

    MFE(k) = best  favorable excursion in bps of entry over candles 1..k
    MAE(k) = worst adverse   excursion in bps of entry over candles 1..k

Outputs per lead (and pooled):
  - the MFE/MAE median curves by k (does the favorable drift front-load?)
  - the e-ratio (median MFE / median |MAE|) by k
  - percentile tables at the hiwin horizon (k=4) and the medium horizon (k=6)
  - a derived empirical bracket: TP ~ p60(MFE@4), SL ~ p80(|MAE|@4),
    time-stop ~ the k where the median MFE curve stops growing —
    each reported in bps AND in entry-candle-ATR multiples.

Read-only research harness. Run inside the container (DB_HOST=postgres):
    docker compose exec -T kestrel python3 scripts/analyze_excursions.py
Options:
    --horizon 12       max candles after entry to walk (default 12)
    --min-n 20         skip leads with fewer closed trades than this
    --leads macd_cross,macd_rsi,cci_mom,sma_cross
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from typing import Any, Optional

sys.path.insert(0, "/app")

_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}

_DEFAULT_LEADS = ("macd_cross", "macd_rsi", "cci_mom", "sma_cross")


def _pct(sorted_vals: list[float], q: float) -> float:
    """Percentile on a pre-sorted list (nearest-rank, q in [0,1])."""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


async def _fetch(conn, leads: tuple[str, ...], horizon: int) -> dict[str, list[dict[str, Any]]]:
    """Return {lead: [trade rows with their post-entry candle path]}."""
    rows = await conn.fetch(
        """
        SELECT t.id, t.bot_id, t.pair, t.timeframe, t.direction,
               t.entry_ts, t.entry_price::float8 AS entry_price,
               split_part(t.bot_id, '-', 4) AS lead,
               c.ts AS c_ts, c.high::float8 AS high, c.low::float8 AS low,
               c.atr14::float8 AS atr14
        FROM trades t
        JOIN candles c
          ON c.bot_id = t.bot_id AND c.pair = t.pair AND c.timeframe = t.timeframe
         AND c.ts >= t.entry_ts - 1000
         AND c.ts <  t.entry_ts + $2::bigint
        WHERE t.env = 'dev' AND t.exit_ts IS NOT NULL
          AND t.timeframe = '1h'
          AND split_part(t.bot_id, '-', 4) = ANY($1::text[])
        ORDER BY t.id, c.ts
        """,
        list(leads),
        horizon * _TF_MS["1h"] + _TF_MS["1h"],
    )

    by_trade: dict[int, dict[str, Any]] = {}
    for r in rows:
        tr = by_trade.setdefault(
            int(r["id"]),
            {
                "lead": r["lead"],
                "direction": r["direction"],
                "entry_price": float(r["entry_price"]),
                "path": [],  # [(high, low, atr14)] in candle-close order after entry
            },
        )
        tr["path"].append((float(r["high"]), float(r["low"]), r["atr14"]))

    out: dict[str, list[dict[str, Any]]] = {lead: [] for lead in leads}
    for tr in by_trade.values():
        if tr["path"]:
            out[tr["lead"]].append(tr)
    return out


def _excursions(trade: dict[str, Any], horizon: int) -> tuple[list[float], list[float], Optional[float]]:
    """Cumulative (MFE_bps[k], MAE_bps[k]) for k=1..min(horizon, path len); entry ATR in bps."""
    entry = trade["entry_price"]
    long = trade["direction"] == "long"
    atr = trade["path"][0][2]
    atr_bps = (atr / entry * 10_000.0) if (atr and entry > 0) else None

    mfe, mae = [], []
    best, worst = 0.0, 0.0
    for high, low, _ in trade["path"][:horizon]:
        if long:
            fav = (high - entry) / entry * 10_000.0
            adv = (low - entry) / entry * 10_000.0
        else:
            fav = (entry - low) / entry * 10_000.0
            adv = (entry - high) / entry * 10_000.0
        best = max(best, fav)
        worst = min(worst, adv)
        mfe.append(best)
        mae.append(worst)
    return mfe, mae, atr_bps


def _curve_row(trades_exc: list[tuple[list[float], list[float]]], k: int) -> Optional[tuple[float, float, float, int]]:
    """(median MFE, median MAE, e-ratio, n) at horizon k across trades that reach k."""
    mfes = [m[0][k - 1] for m in trades_exc if len(m[0]) >= k]
    maes = [m[1][k - 1] for m in trades_exc if len(m[1]) >= k]
    if not mfes:
        return None
    med_mfe = statistics.median(mfes)
    med_mae = statistics.median(maes)
    e_ratio = med_mfe / abs(med_mae) if med_mae != 0 else float("inf")
    return med_mfe, med_mae, e_ratio, len(mfes)


def _report_lead(lead: str, trades: list[dict[str, Any]], horizon: int) -> None:
    exc = [_excursions(t, horizon) for t in trades]
    pairs = [(m, a) for m, a, _ in exc]
    atrs = sorted(a for _, _, a in exc if a is not None)
    med_atr = statistics.median(atrs) if atrs else 0.0

    print(f"\n--- {lead} (n={len(trades)} closed trades, median entry-ATR {med_atr:.0f} bps) ---")
    print(f"  {'k':>3s} {'medMFE':>8s} {'medMAE':>8s} {'e-ratio':>8s} {'n':>5s}")
    plateau_k = horizon
    prev_mfe = None
    for k in range(1, horizon + 1):
        row = _curve_row(pairs, k)
        if row is None:
            break
        med_mfe, med_mae, e_ratio, n = row
        marker = ""
        if prev_mfe is not None and med_mfe - prev_mfe < 1.0 and plateau_k == horizon:
            plateau_k = k - 1  # first k where the median MFE curve stops growing (>1 bp/candle)
            marker = "  <- MFE plateau"
        print(f"  {k:3d} {med_mfe:+8.1f} {med_mae:+8.1f} {e_ratio:8.2f} {n:5d}{marker}")
        prev_mfe = med_mfe

    for k_label, k in (("hiwin k=4", 4), ("medium k=6", 6)):
        mfes = sorted(m[k - 1] for m, _ in pairs if len(m) >= k)
        maes = sorted(abs(a[k - 1]) for _, a in pairs if len(a) >= k)
        if not mfes:
            continue
        print(
            f"  MFE@{k_label}: p40 {_pct(mfes, 0.40):+.1f} · p50 {_pct(mfes, 0.50):+.1f} · "
            f"p60 {_pct(mfes, 0.60):+.1f} · p70 {_pct(mfes, 0.70):+.1f} bps"
        )
        print(
            f"  |MAE|@{k_label}: p50 {_pct(maes, 0.50):.1f} · p70 {_pct(maes, 0.70):.1f} · "
            f"p80 {_pct(maes, 0.80):.1f} bps"
        )

    mfes4 = sorted(m[3] for m, _ in pairs if len(m) >= 4)
    maes4 = sorted(abs(a[3]) for _, a in pairs if len(a) >= 4)
    if mfes4 and med_atr > 0:
        tp_bps = _pct(mfes4, 0.60)
        sl_bps = _pct(maes4, 0.80)
        g = tp_bps / sl_bps if sl_bps else 0.0
        print(
            f"  EMPIRICAL BRACKET (docs/13 §5 S3): TP ~ {tp_bps:.0f} bps ({tp_bps / med_atr:.2f}x ATR) · "
            f"SL ~ {sl_bps:.0f} bps ({sl_bps / med_atr:.2f}x ATR) · g={g:.2f} "
            f"(no-tilt win ~{100.0 / (1.0 + g):.0f}%) · time-stop ~ k={plateau_k}"
        )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=12, help="max candles after entry to walk")
    ap.add_argument("--min-n", type=int, default=20, dest="min_n", help="skip leads with fewer trades")
    ap.add_argument("--leads", default=",".join(_DEFAULT_LEADS), help="comma list of lead labels (bot_id segment 4)")
    args = ap.parse_args()

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
        leads = tuple(x.strip() for x in args.leads.split(",") if x.strip())
        data = await _fetch(conn, leads, args.horizon)
    finally:
        await conn.close()

    print("=== MFE/MAE EXCURSION MINER (live dev 1h leads — gross bps of entry; uncensored paths) ===")
    print(f"horizon={args.horizon} candles · leads={','.join(leads)}")

    pooled: list[dict[str, Any]] = []
    for lead in leads:
        trades = data.get(lead, [])
        pooled.extend(trades)
        if len(trades) < args.min_n:
            print(f"\n--- {lead}: only {len(trades)} trades (< --min-n {args.min_n}) — skipped")
            continue
        _report_lead(lead, trades, args.horizon)

    if len(pooled) >= args.min_n:
        _report_lead("POOLED(all leads)", pooled, args.horizon)


if __name__ == "__main__":
    asyncio.run(main())
