#!/usr/bin/env python3
"""
Backtest the three 'wave' strategy variants on real data, walk-forward (§30).

Each variant = one registered pattern + a TP/SL/hold param profile, isolated via
run_backtest(enabled_patterns=...). Reuses the grid sweep's robust multi-exchange
fetch, the production CandleBuilder, and the full extended-metric suite, with the
same fee+slippage model the live runner applies — so results are directly
comparable to both the grid leaderboard and the live dashboard.

    ride   wave_ride  — ride a trend wave after a pullback; WIDE SL / FAR TP / LONG hold
    scalp  vol_burst  — trend entry only while volatility expands; tight, short hold
    flip   wave_flip  — fade an exhausted run (counter-trend); moderate

Run (one-off container, repo mounted, no DB needed):
  docker run --rm --entrypoint python --env-file .env -e EXCHANGE=gate \
    -v /root/Kestrel:/app -w /app kestrel-kestrel:latest -u \
    scripts/backtest_wave.py --days 120
  # smoke: scripts/backtest_wave.py --days 45 --pairs BTC/USDT,ETH/USDT,SOL/USDT
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import backtest_grid as bg  # build_candles_for / _enrich_trades / _ext_metrics / _rr / _COST_PCT
import backtest_real as bt  # robust multi-exchange fetch_ohlcv
import build_lab as lab  # PAIRS — single source of truth
from dotenv import load_dotenv

from src.backtest.runner import run_backtest
from src.config import AppConfig, load_params

# Each variant's exit philosophy lives in its param profile (the patterns only
# pick entry + direction). 'ride' uses a wide SL so noise can't stop it out and a
# far TP + long hold so the wave can run — the direct fix for the 73% premature
# stop-out seen live. All three keep planned R/R = tp/sl ≥ 1.2 (risk Rule 3).
VARIANTS = [
    {"name": "ride", "patterns": ["wave_ride"],
     "params": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.6, "max_hold_candles": 8}},
    {"name": "scalp", "patterns": ["vol_burst"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 3}},
    {"name": "flip", "patterns": ["wave_flip"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 4}},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--pairs", default=None, help="comma list to override PAIRS")
    args = ap.parse_args()

    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    base = load_params("params.json")
    pairs = [p.strip() for p in args.pairs.split(",")] if args.pairs else lab.PAIRS
    tag = time.strftime("%Y%m%d-%H%M%S", time.gmtime())

    print(f"=== Kestrel WAVE strategy backtest ({args.tf}, {args.days}d, {cfg.leverage}x) ===", flush=True)
    print(f"pairs={len(pairs)} variants={len(VARIANTS)} backtests={len(pairs) * len(VARIANTS)} "
          f"cost/trade≈{bg._COST_PCT:.2f}%", flush=True)

    pooled: dict[str, dict[str, list]] = {
        v["name"]: {"oos": [], "ins": [], "full": [], "n_oos_candles": 0} for v in VARIANTS
    }
    skipped: list[str] = []

    for pi, pair in enumerate(pairs, 1):
        try:
            ex_used, raw = bt.fetch_ohlcv(pair, args.tf, args.days)
        except Exception as exc:  # noqa: BLE001 — survey loop: report and continue
            print(f"[{pi}/{len(pairs)}] {pair}: FETCH FAILED ({type(exc).__name__}: {str(exc)[:80]}) — skipped",
                  flush=True)
            skipped.append(pair)
            continue

        candles = bg.build_candles_for(pair, args.tf, cfg, base, raw)
        ts_index = {int(c.ts): i for i, c in enumerate(candles)}
        split_ts = candles[int(len(candles) * 0.60)].ts
        n_oos = sum(1 for c in candles if c.ts >= split_ts)
        print(f"[{pi}/{len(pairs)}] {pair}: src={ex_used} candles={len(candles)} (OOS={n_oos})", flush=True)

        for v in VARIANTS:
            p = dataclasses.replace(base, **v["params"])
            trades = run_backtest(
                candles, p, cfg, bot_id=f"bt-{pair}-{v['name']}", enabled_patterns=v["patterns"]
            )["trades"]
            bg._enrich_trades(trades, ts_index, candles)
            oos = [t for t in trades if t["entry_ts"] >= split_ts]
            ins = [t for t in trades if t["entry_ts"] < split_ts]
            pooled[v["name"]]["oos"].extend(oos)
            pooled[v["name"]]["ins"].extend(ins)
            pooled[v["name"]]["full"].extend(trades)
            pooled[v["name"]]["n_oos_candles"] += n_oos
            mo = bg._ext_metrics(oos, n_oos)
            print(f"        {v['name']:6s}: OOS n={mo['total_trades']} win={mo['win_rate'] * 100:.1f}% "
                  f"avg=${mo['avg_pnl_usdt']:.4f} net=${mo['total_pnl_usdt']:.3f} "
                  f"expR={mo['expectancy_R']:.3f}", flush=True)

    table: list[dict] = []
    for v in VARIANTS:
        d = pooled[v["name"]]
        mo = bg._ext_metrics(d["oos"], d["n_oos_candles"])
        mi = bg._ext_metrics(d["ins"], d["n_oos_candles"])
        table.append({"name": v["name"], "patterns": v["patterns"], "params": v["params"],
                      "oos": mo, "ins": mi, "degrade": round(mo["avg_pnl_usdt"] - mi["avg_pnl_usdt"], 5)})
    table.sort(key=lambda r: (r["oos"]["total_trades"] == 0, -r["oos"]["avg_pnl_usdt"]))

    print("\n=== WAVE VARIANT LEADERBOARD (out-of-sample) ===", flush=True)
    print(f"  {'variant':8s} {'n':>5s} {'win%':>6s} {'avg$':>9s} {'net$':>9s} {'R/R':>5s} "
          f"{'expR':>6s} {'PF':>5s} {'tp%':>5s} {'sl%':>5s} {'to%':>5s} {'IS→OOS':>8s}", flush=True)
    for r in table:
        m = r["oos"]
        pf = m["profit_factor"] if m["profit_factor"] is not None else float("inf")
        print(f"  {r['name']:8s} {m['total_trades']:5d} {m['win_rate'] * 100:6.1f} {m['avg_pnl_usdt']:9.4f} "
              f"{m['total_pnl_usdt']:9.3f} {bg._rr(m):5.2f} {m['expectancy_R']:6.2f} {pf:5.2f} "
              f"{m['tp_rate'] * 100:5.1f} {m['sl_rate'] * 100:5.1f} {m['timeout_rate'] * 100:5.1f} "
              f"{r['degrade']:+8.4f}", flush=True)

    passing = [r for r in table if r["oos"]["total_pnl_usdt"] > 0 and r["oos"]["win_rate"] > 0.55
               and bg._rr(r["oos"]) >= 1.2 and r["oos"]["total_trades"] >= 30]
    print("\n=== VERDICT (§30 OOS: win>55% · R/R≥1.2 · net>0 · n≥30) ===", flush=True)
    print(f"  variants clearing the bar: {len(passing)} / {len(table)}", flush=True)
    if table:
        b = table[0]["oos"]
        verdict = "VALIDATES" if passing else "still negative — cost floor dominates"
        print(f"  best: {table[0]['name']} avg=${b['avg_pnl_usdt']:.4f}/trade win={b['win_rate'] * 100:.1f}% "
              f"n={b['total_trades']} expR={b['expectancy_R']:.3f} — {verdict}", flush=True)
    if skipped:
        print(f"  NOTE: {len(skipped)} pair(s) skipped (fetch failed): {', '.join(skipped)}", flush=True)

    _write_md(tag, args, cfg, pairs, table, passing, skipped)


def _write_md(tag, args, cfg, pairs, table, passing, skipped) -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, f"backtest_wave_{tag}")
    lines = [f"# Kestrel wave-strategy backtest — {tag} UTC", "",
             f"- **window:** {args.days}d {args.tf} · **leverage:** {cfg.leverage}x · "
             f"cost/trade ≈ {bg._COST_PCT:.2f}%",
             f"- **pairs:** {', '.join(pairs)}"
             + (f" · **skipped (fetch failed):** {', '.join(skipped)}" if skipped else ""),
             f"- **variants clearing §30 OOS:** {len(passing)} / {len(table)}", "",
             "| variant | pattern | TP/SL/hold | n | win% | avg $ | net $ | R/R | expR | tp% | sl% | to% | IS→OOS |",
             "|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for r in table:
        m, p = r["oos"], r["params"]
        prof = f"{p['tp_atr_multiplier']}/{p['sl_atr_multiplier']}/{p['max_hold_candles']}"
        lines.append(f"| {r['name']} | {r['patterns'][0]} | {prof} | {m['total_trades']} | "
                     f"{m['win_rate'] * 100:.1f} | {m['avg_pnl_usdt']:.4f} | {m['total_pnl_usdt']:.3f} | "
                     f"{bg._rr(m):.2f} | {m['expectancy_R']:.2f} | {m['tp_rate'] * 100:.0f} | "
                     f"{m['sl_rate'] * 100:.0f} | {m['timeout_rate'] * 100:.0f} | {r['degrade']:+.4f} |")
    lines += ["", "## Verdict", ""]
    if passing:
        lines.append(f"**{len(passing)} variant(s) clear the §30 OOS bar** — investigate before trusting.")
    elif table:
        b = table[0]["oos"]
        lines.append(f"**No variant clears the §30 OOS bar.** Best was `{table[0]['name']}` at "
                     f"avg ${b['avg_pnl_usdt']:.4f}/trade, win {b['win_rate'] * 100:.1f}%, "
                     f"expR {b['expectancy_R']:.3f}, n={b['total_trades']}.")
    with open(f"{stem}.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwrote {os.path.normpath(stem)}.md", flush=True)


if __name__ == "__main__":
    main()
