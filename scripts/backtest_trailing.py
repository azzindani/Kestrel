#!/usr/bin/env python3
"""
A/B backtest: trailing-close vs fixed TP/SL, per wave variant, walk-forward (§30).

For each strategy we run the SAME entries on the SAME candles twice —
  fixed : the current fixed-TP / fixed-SL exit
  trail : trailing-close (trailing_enabled, fixed TP dropped, stop ratchets)
— so the only difference is the exit policy. This isolates trailing's effect:
does letting winners run (and exiting on reversal) lift avg $/trade, expectancy
and net over a hard take-profit? Reuses backtest_wave/backtest_grid machinery,
so the same fee+slippage model the live runner applies is in force.

Run (one-off container, repo mounted, no DB needed):
  docker run --rm --entrypoint python --env-file .env -e EXCHANGE=gate \
    -v /root/Kestrel:/app -w /app kestrel-kestrel:latest -u \
    scripts/backtest_trailing.py --days 120
  # smoke: scripts/backtest_trailing.py --days 45 --pairs BTC/USDT,ETH/USDT,SOL/USDT
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

# Each variant gets a fixed exit profile and a trailing overlay on the SAME entry
# pattern. Trailing drops the fixed TP, so tp_atr_multiplier is irrelevant there;
# the trail rides until price reverses trail_distance_r×R from the peak. Holds are
# widened for the trailing flavour so a runner isn't cut short by the timeout.
VARIANTS = [
    {
        "name": "ride",
        "patterns": ["wave_ride"],
        "fixed": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.6, "max_hold_candles": 8},
        # tp_atr stays 3.0 even though trailing ignores it at EXIT — risk Rule 3
        # still gates ENTRY on planned R/R (tp/sl ≥ 1.2), so the TP must clear it.
        "trail": {
            "tp_atr_multiplier": 3.0,
            "sl_atr_multiplier": 1.6,
            "max_hold_candles": 24,
            "trailing_enabled": True,
            "trail_activation_r": 1.0,
            "trail_distance_r": 1.0,
        },
    },
    {
        "name": "scalp",
        "patterns": ["vol_burst"],
        "fixed": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 3},
        "trail": {
            "sl_atr_multiplier": 1.0,
            "max_hold_candles": 8,
            "trailing_enabled": True,
            "trail_activation_r": 0.8,
            "trail_distance_r": 0.5,
        },
    },
    {
        "name": "flip",
        "patterns": ["wave_flip"],
        "fixed": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 4},
        "trail": {
            "sl_atr_multiplier": 1.0,
            "max_hold_candles": 8,
            "trailing_enabled": True,
            "trail_activation_r": 1.0,
            "trail_distance_r": 0.8,
        },
    },
]

ARMS = ("fixed", "trail")


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

    print(f"=== Kestrel TRAILING A/B backtest ({args.tf}, {args.days}d, {cfg.leverage}x) ===", flush=True)
    print(
        f"pairs={len(pairs)} variants={len(VARIANTS)} arms={len(ARMS)} "
        f"backtests={len(pairs) * len(VARIANTS) * len(ARMS)} cost/trade≈{bg._COST_PCT:.2f}%",
        flush=True,
    )

    # pooled[(variant, arm)] = {oos, ins, full, n_oos_candles}
    pooled: dict[tuple[str, str], dict[str, list]] = {
        (v["name"], a): {"oos": [], "ins": [], "full": [], "n_oos_candles": 0} for v in VARIANTS for a in ARMS
    }
    skipped: list[str] = []

    for pi, pair in enumerate(pairs, 1):
        try:
            ex_used, raw = bt.fetch_ohlcv(pair, args.tf, args.days)
        except Exception as exc:  # noqa: BLE001 — survey loop: report and continue
            print(
                f"[{pi}/{len(pairs)}] {pair}: FETCH FAILED ({type(exc).__name__}: {str(exc)[:80]}) — skipped",
                flush=True,
            )
            skipped.append(pair)
            continue

        candles = bg.build_candles_for(pair, args.tf, cfg, base, raw)
        ts_index = {int(c.ts): i for i, c in enumerate(candles)}
        split_ts = candles[int(len(candles) * 0.60)].ts
        n_oos = sum(1 for c in candles if c.ts >= split_ts)
        print(f"[{pi}/{len(pairs)}] {pair}: src={ex_used} candles={len(candles)} (OOS={n_oos})", flush=True)

        for v in VARIANTS:
            for arm in ARMS:
                p = dataclasses.replace(base, **v[arm])
                trades = run_backtest(
                    candles, p, cfg, bot_id=f"bt-{pair}-{v['name']}-{arm}", enabled_patterns=v["patterns"]
                )["trades"]
                bg._enrich_trades(trades, ts_index, candles)
                oos = [t for t in trades if t["entry_ts"] >= split_ts]
                ins = [t for t in trades if t["entry_ts"] < split_ts]
                d = pooled[(v["name"], arm)]
                d["oos"].extend(oos)
                d["ins"].extend(ins)
                d["full"].extend(trades)
                d["n_oos_candles"] += n_oos
            mfx = bg._ext_metrics(pooled[(v["name"], "fixed")]["oos"], 1)
            mtr = bg._ext_metrics(pooled[(v["name"], "trail")]["oos"], 1)
            print(
                f"        {v['name']:6s}: fixed avg=${mfx['avg_pnl_usdt']:.4f} | trail avg=${mtr['avg_pnl_usdt']:.4f}",
                flush=True,
            )

    rows: list[dict] = []
    for v in VARIANTS:
        for arm in ARMS:
            d = pooled[(v["name"], arm)]
            mo = bg._ext_metrics(d["oos"], d["n_oos_candles"])
            mi = bg._ext_metrics(d["ins"], d["n_oos_candles"])
            rows.append(
                {
                    "name": v["name"],
                    "arm": arm,
                    "oos": mo,
                    "ins": mi,
                    "degrade": round(mo["avg_pnl_usdt"] - mi["avg_pnl_usdt"], 5),
                }
            )

    print("\n=== TRAILING A/B LEADERBOARD (out-of-sample) ===", flush=True)
    print(
        f"  {'variant':8s} {'arm':6s} {'n':>5s} {'win%':>6s} {'avg$':>9s} {'net$':>9s} "
        f"{'expR':>6s} {'PF':>5s} {'trail%':>6s} {'sl%':>5s} {'to%':>5s} {'IS→OOS':>8s}",
        flush=True,
    )
    for v in VARIANTS:
        for arm in ARMS:
            r = next(x for x in rows if x["name"] == v["name"] and x["arm"] == arm)
            m = r["oos"]
            pf = m["profit_factor"] if m["profit_factor"] is not None else float("inf")
            # trail-exit share isn't in _ext_metrics; derive from pooled trades.
            tr = pooled[(v["name"], arm)]["oos"]
            trail_rate = sum(1 for t in tr if t.get("close_reason") == "trailing_stop") / len(tr) * 100.0 if tr else 0.0
            print(
                f"  {r['name']:8s} {arm:6s} {m['total_trades']:5d} {m['win_rate'] * 100:6.1f} "
                f"{m['avg_pnl_usdt']:9.4f} {m['total_pnl_usdt']:9.3f} {m['expectancy_R']:6.2f} {pf:5.2f} "
                f"{trail_rate:6.1f} {m['sl_rate'] * 100:5.1f} {m['timeout_rate'] * 100:5.1f} "
                f"{r['degrade']:+8.4f}",
                flush=True,
            )

    print("\n=== VERDICT: does trailing beat fixed (OOS avg $/trade)? ===", flush=True)
    wins = 0
    for v in VARIANTS:
        fx = next(x for x in rows if x["name"] == v["name"] and x["arm"] == "fixed")["oos"]
        tr = next(x for x in rows if x["name"] == v["name"] and x["arm"] == "trail")["oos"]
        better = tr["avg_pnl_usdt"] > fx["avg_pnl_usdt"]
        wins += better
        delta = tr["avg_pnl_usdt"] - fx["avg_pnl_usdt"]
        print(
            f"  {v['name']:6s}: fixed ${fx['avg_pnl_usdt']:.4f} → trail ${tr['avg_pnl_usdt']:.4f} "
            f"({delta:+.4f}/trade) — {'TRAILING BETTER' if better else 'fixed better'}",
            flush=True,
        )
    print(f"  trailing wins on {wins}/{len(VARIANTS)} variants", flush=True)
    if skipped:
        print(f"  NOTE: {len(skipped)} pair(s) skipped (fetch failed): {', '.join(skipped)}", flush=True)

    _write_md(tag, args, cfg, pairs, rows, pooled, wins, skipped)


def _write_md(tag, args, cfg, pairs, rows, pooled, wins, skipped) -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, f"backtest_trailing_{tag}")
    lines = [
        f"# Kestrel trailing-close A/B backtest — {tag} UTC",
        "",
        f"- **window:** {args.days}d {args.tf} · **leverage:** {cfg.leverage}x · cost/trade ≈ {bg._COST_PCT:.2f}%",
        f"- **pairs:** {', '.join(pairs)}"
        + (f" · **skipped (fetch failed):** {', '.join(skipped)}" if skipped else ""),
        f"- **trailing beats fixed on:** {wins} / {len(VARIANTS)} variants (OOS avg $/trade)",
        "",
        "| variant | arm | n | win% | avg $ | net $ | expR | trail% | sl% | to% | IS→OOS |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        m = r["oos"]
        tr = pooled[(r["name"], r["arm"])]["oos"]
        trail_rate = sum(1 for t in tr if t.get("close_reason") == "trailing_stop") / len(tr) * 100.0 if tr else 0.0
        lines.append(
            f"| {r['name']} | {r['arm']} | {m['total_trades']} | {m['win_rate'] * 100:.1f} | "
            f"{m['avg_pnl_usdt']:.4f} | {m['total_pnl_usdt']:.3f} | {m['expectancy_R']:.2f} | "
            f"{trail_rate:.0f} | {m['sl_rate'] * 100:.0f} | {m['timeout_rate'] * 100:.0f} | "
            f"{r['degrade']:+.4f} |"
        )
    lines += [
        "",
        "## Verdict",
        "",
        f"Trailing-close beat the fixed take-profit on **{wins}/{len(VARIANTS)}** variants "
        "by out-of-sample average \\$/trade. Trailing lets winners ride and exits on reversal; "
        "where it loses, the fixed TP banked more before the move retraced.",
    ]
    with open(f"{stem}.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwrote {os.path.normpath(stem)}.md", flush=True)


if __name__ == "__main__":
    main()
