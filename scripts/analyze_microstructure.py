#!/usr/bin/env python3
"""analyze_microstructure.py — mine the recorded order-book / trade-tape dataset.

The microstructure recorder has been writing bid/ask depth, depth-imbalance and
aggressor-side trade delta every ~12s for the 6 core pairs (the one data layer the
candle backtests never had). It has never been analysed. This is the analyser.

Two questions the candle backtests physically cannot answer:

  A. PREDICTIVENESS (all snapshots) — does order-flow at time T predict the forward
     mid-price move at +12s / +1m / +5m? This is the headline test of whether the
     RAW data holds any edge at all, uncontaminated by our (edgeless) entry logic.
     Reported as forward-return per imbalance quantile, correlation, and a
     non-overlapping "trade the imbalance" sim net of the cost floor.

  B. ENTRY QUALITY (joinable dev trades) — for the live trades that fall on a
     recorded pair inside the recorded window, did WINNERS enter WITH the prevailing
     order-flow and LOSERS against it? i.e. would a micro-flow entry filter have
     screened out the 0%-win stop-out bucket that is the entire bleed?

Pure stat/feature helpers carry no I/O; the async shell loads and the reporter prints
(this is an offline research-harness script — the print() ban in CLAUDE.md §3 targets
the live daemon, every backtest_*.py / algo_search.py reports to stdout the same way).

Run inside the container (DB_HOST=postgres resolves there):
    docker compose exec -T kestrel python3 scripts/analyze_microstructure.py
    docker compose exec -T kestrel python3 scripts/analyze_microstructure.py \
        --horizons 12,60,300 --cost-bps 4 --nq 5
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

FEATURES = ("depth_imb5", "depth_imb20", "trade_delta")
MICRO_PAIRS_SQL = "SELECT DISTINCT pair FROM microstructure ORDER BY pair"


# --------------------------------------------------------------------------- #
# Pure logic (no I/O) — numpy only.
# --------------------------------------------------------------------------- #
def forward_return_bps(ts: np.ndarray, mid: np.ndarray, horizon_ms: int) -> np.ndarray:
    """Forward mid return in bps from each snapshot to the first snapshot at >= ts+horizon.

    Returns NaN where no qualifying future snapshot exists. ts must be ascending.
    """
    target = ts + horizon_ms
    idx = np.searchsorted(ts, target, side="left")
    out = np.full(ts.shape, np.nan, dtype=np.float64)
    valid = idx < ts.shape[0]
    fwd_mid = np.where(valid, mid[np.clip(idx, 0, ts.shape[0] - 1)], np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (fwd_mid - mid) / mid * 1e4
    out[~valid] = np.nan
    return out


def _rank(x: np.ndarray) -> np.ndarray:
    """Average-rank of x (for Spearman). NaNs propagate to NaN ranks."""
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(1, x.shape[0] + 1, dtype=np.float64)
    return ranks


def correlations(feature: np.ndarray, fwd: np.ndarray) -> tuple[float, float, int]:
    """(pearson, spearman, n) over rows where both are finite."""
    m = np.isfinite(feature) & np.isfinite(fwd)
    n = int(m.sum())
    if n < 30:
        return float("nan"), float("nan"), n
    f, r = feature[m], fwd[m]
    pear = float(np.corrcoef(f, r)[0, 1])
    spear = float(np.corrcoef(_rank(f), _rank(r))[0, 1])
    return pear, spear, n


def quantile_table(feature: np.ndarray, fwd: np.ndarray, nq: int) -> list[tuple[float, float, int, float]]:
    """Per-quantile (by feature) rows of (lo, hi, n, mean_forward_bps)."""
    m = np.isfinite(feature) & np.isfinite(fwd)
    if m.sum() < nq * 10:
        return []
    f, r = feature[m], fwd[m]
    edges = np.quantile(f, np.linspace(0.0, 1.0, nq + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    rows: list[tuple[float, float, int, float]] = []
    for i in range(nq):
        sel = (f >= edges[i]) & (f < edges[i + 1])
        if i == nq - 1:
            sel = (f >= edges[i]) & (f <= edges[i + 1])
        if sel.sum() == 0:
            continue
        rows.append((float(edges[i]), float(edges[i + 1]), int(sel.sum()), float(r[sel].mean())))
    return rows


def flow_sim_bps(
    feature: np.ndarray,
    fwd: np.ndarray,
    ts: np.ndarray,
    horizon_ms: int,
    cost_bps: float,
    thr_q: float = 0.5,
) -> dict[str, float]:
    """Non-overlapping 'trade the imbalance' sim: go long when feature>=+thr, short when
    <=-thr; hold one horizon; net the round-trip cost. thr is the |feature| quantile thr_q.

    Non-overlap: greedily skip snapshots until ts advances >= horizon, so no two trades
    share a price window (otherwise overlapping samples fake-inflate the result)."""
    m = np.isfinite(feature) & np.isfinite(fwd)
    f, r, t = feature[m], fwd[m], ts[m]
    if f.shape[0] < 50:
        return {"n": 0.0}
    thr = float(np.quantile(np.abs(f), thr_q))
    nets: list[float] = []
    hits = 0
    last_t = -(10**18)
    for i in range(f.shape[0]):
        if t[i] - last_t < horizon_ms:
            continue
        if abs(f[i]) < thr:
            continue
        sig = 1.0 if f[i] > 0 else -1.0
        gross = sig * r[i]
        nets.append(gross - cost_bps)
        hits += int(gross > 0)
        last_t = t[i]
    if not nets:
        return {"n": 0.0}
    arr = np.array(nets)
    return {
        "n": float(arr.shape[0]),
        "thr": thr,
        "hit_rate": hits / arr.shape[0],
        "mean_net_bps": float(arr.mean()),
        "total_net_bps": float(arr.sum()),
    }


def signed_flow(direction: str, feature_val: float) -> float:
    """Flow signed by trade direction: + means order-flow AGREED with the trade."""
    return feature_val if direction == "long" else -feature_val


# --------------------------------------------------------------------------- #
# I/O shell.
# --------------------------------------------------------------------------- #
async def _load_pair(conn, pair: str) -> dict[str, np.ndarray]:
    rows = await conn.fetch(
        """
        SELECT ts,
               mid::float8           AS mid,
               spread_bps::float8    AS spread_bps,
               depth_imb5::float8    AS depth_imb5,
               depth_imb20::float8   AS depth_imb20,
               trade_delta::float8   AS trade_delta
        FROM microstructure
        WHERE pair = $1 AND mid IS NOT NULL
        ORDER BY ts
        """,
        pair,
    )
    cols = ("ts", "mid", "spread_bps", "depth_imb5", "depth_imb20", "trade_delta")
    return {c: np.array([r[c] for r in rows], dtype=np.float64) for c in cols}


async def _load_joinable_trades(conn, tol_ms: int) -> list[dict]:
    return [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT t.pair, t.direction, t.timeframe, t.pattern,
                   t.close_reason, t.pnl_net_usdt::float8 AS pnl,
                   m.depth_imb5::float8 AS depth_imb5,
                   m.depth_imb20::float8 AS depth_imb20,
                   m.trade_delta::float8 AS trade_delta,
                   m.spread_bps::float8 AS spread_bps
            FROM trades t
            JOIN LATERAL (
                SELECT * FROM microstructure m
                WHERE m.pair = t.pair AND abs(m.ts - t.entry_ts) < $1
                ORDER BY abs(m.ts - t.entry_ts)
                LIMIT 1
            ) m ON TRUE
            WHERE t.env = 'dev' AND t.pnl_net_usdt IS NOT NULL
            """,
            tol_ms,
        )
    ]


def _fmt_bps(x: float) -> str:
    return f"{x:+.2f}" if np.isfinite(x) else "   nan"


def _report_predictiveness(pair: str, data: dict, horizons: list[int], nq: int, cost_bps: float) -> None:
    ts, mid = data["ts"], data["mid"]
    if ts.shape[0] < 200:
        print(f"\n[{pair}] only {ts.shape[0]} snapshots — skipped")
        return
    dt = np.diff(ts)
    print(
        f"\n{'=' * 78}\n[{pair}]  {ts.shape[0]} snapshots  median dt={np.median(dt) / 1000:.1f}s  "
        f"spread~{np.nanmedian(data['spread_bps']):.2f}bps"
    )
    for h in horizons:
        fwd = forward_return_bps(ts, mid, h * 1000)
        print(f"\n  horizon +{h}s  (fwd-return bps)")
        for feat in FEATURES:
            pear, spear, n = correlations(data[feat], fwd)
            qt = quantile_table(data[feat], fwd, nq)
            spread = (qt[-1][3] - qt[0][3]) if qt else float("nan")
            cells = "  ".join(_fmt_bps(row[3]) for row in qt)
            sim = flow_sim_bps(data[feat], fwd, ts, h * 1000, cost_bps)
            simstr = (
                f"sim n={int(sim['n'])} hit={sim['hit_rate'] * 100:4.1f}% net={_fmt_bps(sim['mean_net_bps'])}bps/trade"
                if sim.get("n", 0)
                else "sim n=0"
            )
            print(
                f"    {feat:12s} pearson={pear:+.4f} spearman={spear:+.4f}  "
                f"Q[lo..hi]: {cells}  topΔbot={_fmt_bps(spread)}bps"
            )
            print(f"    {'':12s} {simstr}  (cost {cost_bps:.1f}bps round-trip)")


def _report_entry_quality(trades: list[dict], cost_note: str) -> None:
    print(f"\n{'#' * 78}\n# B. ENTRY-QUALITY STUDY — {len(trades)} dev trades with microstructure coverage\n{'#' * 78}")
    if not trades:
        print("  no joinable trades")
        return
    pnl = np.array([t["pnl"] for t in trades])
    win = pnl > 0
    print(f"  overall: win {win.mean() * 100:.1f}%  net ${pnl.sum():+.2f}  avg ${pnl.mean():+.4f}/trade")

    # winners vs losers: mean signed flow (flow agreed with the trade?)
    print("\n  mean order-flow at entry, signed by trade direction (+ = flow AGREED with trade):")
    print(f"    {'feature':12s} {'winners':>10s} {'losers':>10s} {'all':>10s}")
    for feat in FEATURES:
        sf = np.array([signed_flow(t["direction"], t[feat]) for t in trades])
        w = sf[win].mean() if win.any() else float("nan")
        lose = sf[~win].mean() if (~win).any() else float("nan")
        print(f"    {feat:12s} {w:>+10.4f} {lose:>+10.4f} {sf.mean():>+10.4f}")

    # flow-aligned vs flow-against entries: would a filter help?
    print("\n  if we only took entries where flow AGREED (signed feature > 0) vs AGAINST:")
    print(f"    {'feature':12s} {'aligned: n  win%   avg$':>30s}   {'against: n  win%   avg$':>30s}")
    for feat in FEATURES:
        sf = np.array([signed_flow(t["direction"], t[feat]) for t in trades])
        ali, agn = sf > 0, sf < 0

        def cell(sel: np.ndarray) -> str:
            if sel.sum() == 0:
                return f"{0:>4d}     -        -"
            return f"{int(sel.sum()):>4d}  {win[sel].mean() * 100:5.1f}%  ${pnl[sel].mean():+.4f}"

        print(f"    {feat:12s} {cell(ali):>30s}   {cell(agn):>30s}")
    print(f"\n  {cost_note}")


async def _run(args: argparse.Namespace) -> int:
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
        horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
        pairs = [r["pair"] for r in await conn.fetch(MICRO_PAIRS_SQL)]
        print(
            f"{'#' * 78}\n# A. PREDICTIVENESS — does order-flow at T predict the forward mid move?\n"
            f"#    {len(pairs)} pairs  horizons={horizons}s  quantiles={args.nq}  "
            f"cost={args.cost_bps}bps\n{'#' * 78}"
        )
        print(
            "#  read: topΔbot = mean fwd-return of the most bid/buy-heavy quantile minus the\n"
            "#  most ask/sell-heavy. A real edge => monotone quantiles AND topΔbot >> cost,\n"
            "#  AND the non-overlap sim net of cost stays positive across pairs+horizons."
        )
        for pair in pairs:
            data = await _load_pair(conn, pair)
            _report_predictiveness(pair, data, horizons, args.nq, args.cost_bps)

        trades = await _load_joinable_trades(conn, args.tol_ms)
        cost_note = (
            "a flow filter is only worth building if aligned-win% clears ~55% / avg$>0 "
            "AND beats against by a wide margin on > ~100 trades."
        )
        _report_entry_quality(trades, cost_note)
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyse the recorded microstructure dataset.")
    ap.add_argument("--horizons", default="12,60,300", help="forward-return horizons in seconds (csv)")
    ap.add_argument("--nq", type=int, default=5, help="number of imbalance quantile buckets")
    ap.add_argument("--cost-bps", type=float, default=4.0, help="round-trip cost floor in bps (maker~4)")
    ap.add_argument("--tol-ms", type=int, default=30000, help="max |snapshot-entry| ms for trade join")
    args = ap.parse_args()
    import asyncio

    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
