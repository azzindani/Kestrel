#!/usr/bin/env python3
"""
Backtest grid sweep — the FAST, rigorous version of the live HP-tuning lab.

The live lab (bots.json, build_lab.py) accumulates the same TP×SL×hold grid one
real candle at a time, so a rankable answer is days away. This script runs the
*identical* grid over months of real history in minutes, walk-forward
(train 60% / test 40%, CLAUDE.md §30), with the same fee+slippage model the live
runner applies — so its leaderboard is directly comparable to the live dashboard
"Strategy Leaderboard" (both keyed on the same strategy token, e.g. t16s10h8).

Per cell it computes the full strategy-evaluation stat suite (see _ext_metrics):
sample size, win rate, expectancy ($ and R), profit factor, payoff, Sharpe,
Sortino, max drawdown, Calmar, streaks, close-reason mix (TP/SL/timeout/liq),
%-clearing-cost, time-in-market, avg MAE/MFE, and IS→OOS degradation.

Outputs (under reports/):
    backtest_grid_<tag>.md    — human leaderboard + full per-cell stats + verdict
    backtest_grid_<tag>.csv   — every cell, machine-readable
    backtest_grid_<tag>.json  — per-trade detail (incl MAE/MFE/R) for the top cells

Run (one-off container, repo mounted, no DB needed):
  docker run --rm --entrypoint python --env-file .env -e EXCHANGE=gate \
    -v /root/Kestrel:/app -w /app kestrel-kestrel:latest -u \
    scripts/backtest_grid.py --days 150
  # smoke: scripts/backtest_grid.py --days 30 --pairs BTC/USDT,ETH/USDT
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import math
import os
import sys
import time
from typing import Any, Sequence

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

from dotenv import load_dotenv

import backtest_real as bt  # robust multi-exchange fetch_ohlcv
import build_lab as lab  # PAIRS / TP_GRID / SL_GRID / HOLD_GRID / _tok — single source of truth
from src.backtest.metrics import compute_metrics
from src.backtest.runner import run_backtest
from src.config import AppConfig, load_params
from src.data.candle_builder import CandleBuilder

# ~0.18% round trip (CLAUDE.md §17): the edge a winner must clear before any profit.
_COST_PCT = (0.0004 + 0.0005) * 2 * 100.0  # taker + slippage, both sides, in %


# ---------------------------------------------------------------------------
# Candle building (per-pair — backtest_real bakes cfg.pair, we iterate pairs)
# ---------------------------------------------------------------------------
def build_candles_for(pair: str, tf: str, cfg: AppConfig, params, rows: list[list]) -> list:
    """Drive raw OHLCV rows for `pair` through the production CandleBuilder so the
    indicator path is byte-identical to live. volume_ratio is unit-invariant, so
    no base→quote conversion is needed for a self-consistent offline series."""
    builder = CandleBuilder(cfg.bot_id, pair, tf, params)
    built: list = []
    builder.set_emitter(built.append)
    for r in rows:
        builder.process_ohlcv([int(r[0]), r[1], r[2], r[3], r[4], r[5]], is_closed=True)
    return built


# ---------------------------------------------------------------------------
# Per-trade enrichment: MAE / MFE / realized-R / planned-R/R
# ---------------------------------------------------------------------------
def _enrich_trades(trades: list[dict], ts_index: dict[int, int], candles: Sequence) -> None:
    """Attach path-dependent stats to each trade in place.

    realized_R   = price move (signed) / planned risk distance |entry-sl|
    planned_RR   = |tp-entry| / |entry-sl|
    mfe_pct/mae_pct = max favourable / adverse excursion during the hold (% of entry)
    """
    for t in trades:
        entry = float(t["entry_price"])
        sl = float(t["sl_price"])
        tp = float(t["tp_price"])
        exit_p = float(t["exit_price"])
        is_long = t["direction"] == "long"
        risk = abs(entry - sl) or 1e-12

        move = (exit_p - entry) if is_long else (entry - exit_p)
        t["realized_R"] = move / risk
        t["planned_RR"] = abs(tp - entry) / risk

        i0 = ts_index.get(int(t["entry_ts"]))
        i1 = ts_index.get(int(t["exit_ts"]))
        if i0 is None or i1 is None or i1 < i0:
            t["mfe_pct"] = 0.0
            t["mae_pct"] = 0.0
            continue
        hi = max(c.high for c in candles[i0 : i1 + 1])
        lo = min(c.low for c in candles[i0 : i1 + 1])
        if is_long:
            t["mfe_pct"] = (hi - entry) / entry * 100.0
            t["mae_pct"] = (lo - entry) / entry * 100.0
        else:
            t["mfe_pct"] = (entry - lo) / entry * 100.0
            t["mae_pct"] = (entry - hi) / entry * 100.0


# ---------------------------------------------------------------------------
# Extended metrics — everything useful for judging a strategy cell
# ---------------------------------------------------------------------------
def _ext_metrics(trades: list[dict], n_candles: int) -> dict[str, Any]:
    base = compute_metrics(trades)
    if not trades:
        return {
            **base,
            "expectancy_R": 0.0,
            "sortino": 0.0,
            "calmar": 0.0,
            "median_pnl_usdt": 0.0,
            "max_consec_losses": 0,
            "max_consec_wins": 0,
            "tp_rate": 0.0,
            "sl_rate": 0.0,
            "timeout_rate": 0.0,
            "liq_rate": 0.0,
            "pct_clearing_cost": 0.0,
            "time_in_market_pct": 0.0,
            "avg_planned_RR": 0.0,
            "avg_mfe_pct": 0.0,
            "avg_mae_pct": 0.0,
            "avg_hold_minutes": 0.0,
        }

    n = len(trades)
    pnls = [float(t["pnl_net_usdt"]) for t in trades]
    pcts = [float(t["pnl_pct"]) for t in trades]
    Rs = [float(t["realized_R"]) for t in trades]
    reasons = base["close_reasons"]

    # streaks (chronological)
    chrono = sorted(trades, key=lambda t: int(t["entry_ts"]))
    mc_l = mc_w = cur_l = cur_w = 0
    for t in chrono:
        if float(t["pnl_net_usdt"]) > 0:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        mc_l = max(mc_l, cur_l)
        mc_w = max(mc_w, cur_w)

    downside = [p for p in pcts if p < 0]
    dstd = math.sqrt(sum(p * p for p in downside) / len(downside)) if downside else 0.0
    mean_pct = sum(pcts) / n
    sortino = (mean_pct / dstd) * math.sqrt(105_120) if dstd else 0.0
    calmar = (base["total_pnl_usdt"] / base["max_drawdown_usdt"]) if base["max_drawdown_usdt"] else 0.0
    hold_min = [int(t["hold_candles"]) * 5 for t in trades]  # 5m candles

    return {
        **base,
        "median_pnl_usdt": round(sorted(pnls)[n // 2], 4),
        "expectancy_R": round(sum(Rs) / n, 4),
        "avg_planned_RR": round(sum(float(t["planned_RR"]) for t in trades) / n, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3),
        "max_consec_losses": mc_l,
        "max_consec_wins": mc_w,
        "tp_rate": round(reasons.get("take_profit", 0) / n, 4),
        "sl_rate": round(reasons.get("stop_loss", 0) / n, 4),
        "timeout_rate": round(reasons.get("timeout", 0) / n, 4),
        "liq_rate": round(reasons.get("liquidated", 0) / n, 4),
        "pct_clearing_cost": round(sum(1 for p in pcts if p > 0) / n, 4),
        "time_in_market_pct": round(sum(int(t["hold_candles"]) for t in trades) / n_candles * 100.0, 2)
        if n_candles
        else 0.0,
        "avg_mfe_pct": round(sum(float(t["mfe_pct"]) for t in trades) / n, 4),
        "avg_mae_pct": round(sum(float(t["mae_pct"]) for t in trades) / n, 4),
        "avg_hold_minutes": round(sum(hold_min) / n, 1),
    }


def _rr(m: dict) -> float:
    return abs(m["avg_win_usdt"] / m["avg_loss_usdt"]) if m["avg_loss_usdt"] else 0.0


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--pairs", default=None, help="comma list to override PAIRS (e.g. BTC/USDT,ETH/USDT)")
    ap.add_argument("--top", type=int, default=3, help="per-trade JSON for the top-N cells")
    args = ap.parse_args()

    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    base = load_params("params.json")
    pairs = [p.strip() for p in args.pairs.split(",")] if args.pairs else lab.PAIRS
    cells = [(tp, sl, h) for tp in lab.TP_GRID for sl in lab.SL_GRID for h in lab.HOLD_GRID]
    tag = time.strftime("%Y%m%d-%H%M%S", time.gmtime())

    print(f"=== Kestrel backtest GRID sweep ({args.tf}, {args.days}d, {cfg.leverage}x) ===", flush=True)
    print(
        f"pairs={len(pairs)}  cells={len(cells)} (TP×SL×hold = "
        f"{len(lab.TP_GRID)}×{len(lab.SL_GRID)}×{len(lab.HOLD_GRID)})  "
        f"backtests={len(pairs) * len(cells)}  cost/trade≈{_COST_PCT:.2f}%",
        flush=True,
    )

    # rows[cell_token] accumulates per-cell pooled OOS/IS/full trade lists across pairs
    pooled: dict[str, dict[str, list]] = {
        lab._tok(tp, sl, h): {"oos": [], "ins": [], "full": [], "n_oos_candles": 0} for (tp, sl, h) in cells
    }
    per_pair_best: list[dict] = []
    skipped: list[str] = []

    for pi, pair in enumerate(pairs, 1):
        try:
            ex_used, raw = bt.fetch_ohlcv(pair, args.tf, args.days)
        except Exception as exc:  # noqa: BLE001 — survey loop, report and continue
            print(
                f"[{pi}/{len(pairs)}] {pair}: FETCH FAILED ({type(exc).__name__}: {str(exc)[:80]}) — skipped",
                flush=True,
            )
            skipped.append(pair)
            continue

        candles = build_candles_for(pair, args.tf, cfg, base, raw)
        ts_index = {int(c.ts): i for i, c in enumerate(candles)}
        split_ts = candles[int(len(candles) * 0.60)].ts
        n_oos_candles = sum(1 for c in candles if c.ts >= split_ts)
        print(f"[{pi}/{len(pairs)}] {pair}: src={ex_used} candles={len(candles)} (OOS={n_oos_candles})", flush=True)

        pair_rows: list[dict] = []
        for tp, sl, h in cells:
            tok = lab._tok(tp, sl, h)
            p = dataclasses.replace(base, tp_atr_multiplier=tp, sl_atr_multiplier=sl, max_hold_candles=h)
            trades = run_backtest(candles, p, cfg, bot_id=f"bt-{pair}-{tok}")["trades"]
            _enrich_trades(trades, ts_index, candles)
            oos = [t for t in trades if t["entry_ts"] >= split_ts]
            ins = [t for t in trades if t["entry_ts"] < split_ts]
            pooled[tok]["oos"].extend(oos)
            pooled[tok]["ins"].extend(ins)
            pooled[tok]["full"].extend(trades)
            pooled[tok]["n_oos_candles"] += n_oos_candles
            mo = _ext_metrics(oos, n_oos_candles)
            pair_rows.append(
                {
                    "pair": pair,
                    "cell": tok,
                    "tp": tp,
                    "sl": sl,
                    "hold": h,
                    "oos_n": mo["total_trades"],
                    "oos_win": mo["win_rate"],
                    "oos_net": mo["total_pnl_usdt"],
                    "oos_avg": mo["avg_pnl_usdt"],
                    "oos_expR": mo["expectancy_R"],
                    "oos_rr": _rr(mo),
                }
            )
        # zero-trade cells (planned R/R < risk floor 1.2 → every signal rejected) sort last
        pair_rows.sort(key=lambda r: (r["oos_n"] == 0, -r["oos_avg"], -r["oos_n"]))
        per_pair_best.append(pair_rows[0])
        b = pair_rows[0]
        print(
            f"        best cell {b['cell']}: OOS n={b['oos_n']} win={b['oos_win'] * 100:.1f}% "
            f"avg=${b['oos_avg']:.4f} net=${b['oos_net']:.3f} expR={b['oos_expR']:.3f}",
            flush=True,
        )

    # ---- pooled per-cell leaderboard ----
    table: list[dict] = []
    for tok, d in pooled.items():
        mo = _ext_metrics(d["oos"], d["n_oos_candles"])
        mi = _ext_metrics(d["ins"], d["n_oos_candles"])
        table.append(
            {"cell": tok, "oos": mo, "ins": mi, "degrade": round((mo["avg_pnl_usdt"] - mi["avg_pnl_usdt"]), 5)}
        )
    # zero-trade cells (planned R/R < risk floor 1.2 → every signal rejected) sort last
    table.sort(key=lambda r: (r["oos"]["total_trades"] == 0, -r["oos"]["avg_pnl_usdt"], -r["oos"]["total_trades"]))

    _print_leaderboard(table)
    passing = [
        r
        for r in table
        if r["oos"]["total_pnl_usdt"] > 0
        and r["oos"]["win_rate"] > 0.55
        and _rr(r["oos"]) >= 1.2
        and r["oos"]["total_trades"] >= 30
    ]
    _print_verdict(table, passing, skipped)

    _write_reports(tag, args, cfg, pairs, cells, table, per_pair_best, pooled, passing, skipped)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _print_leaderboard(table: list[dict]) -> None:
    print("\n=== POOLED CELL LEADERBOARD (all pairs, ranked by OOS avg $/trade) ===", flush=True)
    print(
        f"  {'cell':12s} {'n':>5s} {'win%':>6s} {'avg$':>9s} {'net$':>9s} {'R/R':>5s} "
        f"{'expR':>6s} {'PF':>5s} {'shrp':>6s} {'srt':>6s} {'maxDD$':>8s} "
        f"{'tp%':>5s} {'sl%':>5s} {'to%':>5s} {'IS→OOS':>8s}",
        flush=True,
    )
    for r in table:
        m = r["oos"]
        pf = m["profit_factor"] if m["profit_factor"] is not None else float("inf")
        print(
            f"  {r['cell']:12s} {m['total_trades']:5d} {m['win_rate'] * 100:6.1f} "
            f"{m['avg_pnl_usdt']:9.4f} {m['total_pnl_usdt']:9.3f} {_rr(m):5.2f} "
            f"{m['expectancy_R']:6.2f} {pf:5.2f} {m['sharpe_ratio']:6.2f} {m['sortino']:6.2f} "
            f"{m['max_drawdown_usdt']:8.3f} {m['tp_rate'] * 100:5.1f} {m['sl_rate'] * 100:5.1f} "
            f"{m['timeout_rate'] * 100:5.1f} {r['degrade']:+8.4f}",
            flush=True,
        )


def _print_verdict(table: list[dict], passing: list[dict], skipped: list[str]) -> None:
    best = table[0]["oos"] if table else _ext_metrics([], 0)
    doa = [r["cell"] for r in table if r["oos"]["total_trades"] == 0]
    print("\n=== VERDICT (out-of-sample, CLAUDE.md §30: win>55% · R/R≥1.2 · net>0 · n≥30) ===", flush=True)
    print(f"  cells clearing the bar: {len(passing)} / {len(table)}", flush=True)
    if doa:
        print(
            f"  DEAD cells (planned R/R = tp/sl < 1.2 risk floor → 0 trades, never fire): "
            f"{len(doa)} — {', '.join(doa)}",
            flush=True,
        )
    if passing:
        for r in passing[:5]:
            m = r["oos"]
            print(
                f"    >>> {r['cell']}: win={m['win_rate'] * 100:.1f}% R/R={_rr(m):.2f} "
                f"net=${m['total_pnl_usdt']:.3f} n={m['total_trades']} — VALIDATES, investigate",
                flush=True,
            )
    else:
        print(
            f"  best cell {table[0]['cell'] if table else '—'}: avg=${best['avg_pnl_usdt']:.4f}/trade "
            f"win={best['win_rate'] * 100:.1f}% n={best['total_trades']} — "
            f"NO cell clears the bar (confirms no edge in this grid on real data)",
            flush=True,
        )
    if skipped:
        print(
            f"  NOTE: {len(skipped)} pair(s) skipped (fetch failed): {', '.join(skipped)} — leaderboard excludes them.",
            flush=True,
        )


def _flat(cell: str, tag: str, m: dict, sample: str) -> dict:
    """One flat CSV row for a (cell, sample) — sample in {'oos','ins'}."""
    return {
        "cell": cell,
        "sample": sample,
        "n": m["total_trades"],
        "win_rate": m["win_rate"],
        "net_usdt": m["total_pnl_usdt"],
        "avg_usdt": m["avg_pnl_usdt"],
        "median_usdt": m["median_pnl_usdt"],
        "expectancy_R": m["expectancy_R"],
        "avg_planned_RR": m["avg_planned_RR"],
        "rr": round(_rr(m), 3),
        "profit_factor": m["profit_factor"],
        "sharpe": m["sharpe_ratio"],
        "sortino": m["sortino"],
        "calmar": m["calmar"],
        "max_dd_usdt": m["max_drawdown_usdt"],
        "max_dd_pct": m["max_drawdown_pct"],
        "max_consec_losses": m["max_consec_losses"],
        "tp_rate": m["tp_rate"],
        "sl_rate": m["sl_rate"],
        "timeout_rate": m["timeout_rate"],
        "liq_rate": m["liq_rate"],
        "pct_clearing_cost": m["pct_clearing_cost"],
        "time_in_market_pct": m["time_in_market_pct"],
        "avg_hold_candles": m["avg_hold_candles"],
        "avg_hold_minutes": m["avg_hold_minutes"],
        "avg_mfe_pct": m["avg_mfe_pct"],
        "avg_mae_pct": m["avg_mae_pct"],
    }


def _write_reports(tag, args, cfg, pairs, cells, table, per_pair_best, pooled, passing, skipped) -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, f"backtest_grid_{tag}")

    # CSV — every cell, OOS and IS rows
    with open(f"{stem}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(_flat("x", tag, _ext_metrics([], 0), "oos").keys()))
        w.writeheader()
        for r in table:
            w.writerow(_flat(r["cell"], tag, r["oos"], "oos"))
            w.writerow(_flat(r["cell"], tag, r["ins"], "ins"))

    # JSON — per-trade detail for top-N cells (incl MAE/MFE/R)
    keep = {
        "pair",
        "direction",
        "pattern",
        "entry_ts",
        "exit_ts",
        "hold_candles",
        "entry_price",
        "exit_price",
        "tp_price",
        "sl_price",
        "close_reason",
        "pnl_net_usdt",
        "pnl_pct",
        "realized_R",
        "planned_RR",
        "mfe_pct",
        "mae_pct",
    }
    top_json = {}
    for r in table[: args.top]:
        ts = sorted(pooled[r["cell"]]["full"], key=lambda t: int(t["entry_ts"]))
        top_json[r["cell"]] = [{k: t[k] for k in keep if k in t} for t in ts]
    with open(f"{stem}.json", "w") as f:
        json.dump({"tag": tag, "days": args.days, "pairs": pairs, "trades_by_cell": top_json}, f, indent=2)

    # Markdown leaderboard
    lines = [
        f"# Kestrel backtest grid sweep — {tag} UTC",
        "",
        f"- **window:** {args.days}d {args.tf} · **leverage:** {cfg.leverage}x · "
        f"**bucket:** ${cfg.bucket_size_usdt} · cost/trade ≈ {_COST_PCT:.2f}%",
        f"- **pairs:** {', '.join(pairs)}"
        + (f" · **skipped (fetch failed):** {', '.join(skipped)}" if skipped else ""),
        f"- **grid:** {len(cells)} cells (TP×SL×hold) × {len(pairs)} pairs · walk-forward 60/40",
        f"- **cells clearing §30 OOS bar:** {len(passing)} / {len(table)}",
        "",
        "## Pooled cell leaderboard (out-of-sample, ranked by avg $/trade)",
        "",
        "| cell | n | win% | avg $ | net $ | R/R | expR | PF | Sharpe | Sortino | maxDD$ | tp% | sl% | to% | IS→OOS |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in table:
        m = r["oos"]
        pf = f"{m['profit_factor']:.2f}" if m["profit_factor"] is not None else "∞"
        lines.append(
            f"| {r['cell']} | {m['total_trades']} | {m['win_rate'] * 100:.1f} | "
            f"{m['avg_pnl_usdt']:.4f} | {m['total_pnl_usdt']:.3f} | {_rr(m):.2f} | "
            f"{m['expectancy_R']:.2f} | {pf} | {m['sharpe_ratio']:.2f} | {m['sortino']:.2f} | "
            f"{m['max_drawdown_usdt']:.3f} | {m['tp_rate'] * 100:.0f} | {m['sl_rate'] * 100:.0f} | "
            f"{m['timeout_rate'] * 100:.0f} | {r['degrade']:+.4f} |"
        )
    lines += [
        "",
        "## Best cell per pair (out-of-sample)",
        "",
        "| pair | best cell | n | win% | avg $ | net $ | expR | R/R |",
        "|---|---|--:|--:|--:|--:|--:|--:|",
    ]
    for b in per_pair_best:
        lines.append(
            f"| {b['pair']} | {b['cell']} | {b['oos_n']} | {b['oos_win'] * 100:.1f} | "
            f"{b['oos_avg']:.4f} | {b['oos_net']:.3f} | {b['oos_expR']:.2f} | {b['oos_rr']:.2f} |"
        )
    doa = [r["cell"] for r in table if r["oos"]["total_trades"] == 0]
    lines += ["", "## Verdict", ""]
    if doa:
        lines.append(
            f"> **{len(doa)} grid cells are dead-on-arrival** (planned R/R = tp/sl < 1.2 risk "
            f"floor → every signal rejected, 0 trades): `{', '.join(doa)}`. The live lab wastes "
            f"these bots — drop them from the grid.\n"
        )
    if passing:
        lines.append(f"**{len(passing)} cell(s) clear the §30 OOS bar** — investigate before trusting:")
        for r in passing:
            m = r["oos"]
            lines.append(
                f"- `{r['cell']}` — win {m['win_rate'] * 100:.1f}%, R/R {_rr(m):.2f}, "
                f"net ${m['total_pnl_usdt']:.3f}, n={m['total_trades']}"
            )
    else:
        b = table[0]["oos"] if table else {}
        lines.append(
            f"**No cell clears the §30 OOS bar.** Best was `{table[0]['cell'] if table else '—'}` "
            f"at avg ${b.get('avg_pnl_usdt', 0):.4f}/trade, win {b.get('win_rate', 0) * 100:.1f}%. "
            f"Confirms the trend_momentum grid has no out-of-sample edge on real data — "
            f"the cost floor (~{_COST_PCT:.2f}%/trade) dominates the per-trade move at 5m."
        )
    with open(f"{stem}.md", "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nwrote {os.path.normpath(stem)}.{{md,csv,json}}", flush=True)


if __name__ == "__main__":
    main()
