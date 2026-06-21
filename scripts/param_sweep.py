#!/usr/bin/env python3
"""
Param sweep — does ANY in-range params.json config clear the §30 bar on real data?

Fetches + builds the candle series ONCE, then runs run_backtest() for each grid
config (only signal/risk thresholds vary — they are applied at evaluate/validate
time, not baked into stored indicators). OOS = trades whose entry is in the last
40% of the period. Reports the best configs and whether any passes §30.

Run (background, repo mounted, no DB):
  docker run --rm --entrypoint python -v /root/Kestrel:/app -w /app \
    kestrel-kestrel:latest -u scripts/param_sweep.py --days 90
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

from dotenv import load_dotenv

from src.config import AppConfig, load_params
from src.backtest.metrics import compute_metrics
from src.backtest.runner import run_backtest
import backtest_real as bt  # reuse fetch + build helpers

# Grid over the params most able to flip profitability given the fee/edge structure.
TP_GRID = [1.0, 1.6, 2.4]
SL_GRID = [0.7, 1.0, 1.4]
MC_GRID = [0.55, 0.70]
VOL_GRID = [1.3, 1.9]
HOLD = 6


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    base = load_params("params.json")
    pair, tf = cfg.pair, cfg.timeframe_entry

    print(f"=== Kestrel param sweep ({pair} {tf}, {args.days}d, {cfg.leverage}x) ===", flush=True)
    ex_used, rows = bt.fetch_ohlcv(pair, tf, args.days)
    candles = bt.build_candles(cfg, base, rows)
    split_ts = candles[int(len(candles) * 0.60)].ts
    print(
        f"source={ex_used}  candles={len(candles)}  "
        f"grid={len(TP_GRID) * len(SL_GRID) * len(MC_GRID) * len(VOL_GRID)} configs",
        flush=True,
    )

    results = []
    i, total = 0, len(TP_GRID) * len(SL_GRID) * len(MC_GRID) * len(VOL_GRID)
    for tp in TP_GRID:
        for sl in SL_GRID:
            for mc in MC_GRID:
                for vol in VOL_GRID:
                    i += 1
                    p = dataclasses.replace(
                        base,
                        tp_atr_multiplier=tp,
                        sl_atr_multiplier=sl,
                        min_confidence=mc,
                        volume_ratio_min=vol,
                        max_hold_candles=HOLD,
                    )
                    trades = run_backtest(candles, p, cfg)["trades"]
                    oos = [t for t in trades if t["entry_ts"] >= split_ts]
                    ins = [t for t in trades if t["entry_ts"] < split_ts]
                    m, mi = compute_metrics(oos), compute_metrics(ins)
                    rr = abs(m["avg_win_usdt"] / m["avg_loss_usdt"]) if m["avg_loss_usdt"] else 0.0
                    results.append(
                        {
                            "net": m["total_pnl_usdt"],
                            "win": m["win_rate"],
                            "rr": rr,
                            "n": m["total_trades"],
                            "tp": tp,
                            "sl": sl,
                            "mc": mc,
                            "vol": vol,
                            "is_win": mi["win_rate"],
                            "is_net": mi["total_pnl_usdt"],
                        }
                    )
                    print(
                        f"[{i:2d}/{total}] tp={tp} sl={sl} mc={mc} vol={vol} -> "
                        f"OOS n={m['total_trades']:3d} win={m['win_rate'] * 100:5.1f}% "
                        f"net=${m['total_pnl_usdt']:8.3f} rr={rr:.2f}",
                        flush=True,
                    )

    results.sort(key=lambda r: -r["net"])
    print("\n=== TOP 10 configs by OOS net PnL ===", flush=True)
    for r in results[:10]:
        print(
            f"  net=${r['net']:8.3f}  win={r['win'] * 100:5.1f}%  rr={r['rr']:4.2f}  n={r['n']:3d}  "
            f"| tp={r['tp']} sl={r['sl']} mc={r['mc']} vol={r['vol']}  "
            f"| IS win={r['is_win'] * 100:.1f}% net=${r['is_net']:.2f}",
            flush=True,
        )

    passing = [r for r in results if r["net"] > 0 and r["win"] > 0.55 and r["rr"] >= 1.2 and r["n"] >= 30]
    best = results[0]
    print(f"\n=== VERDICT ===", flush=True)
    print(f"  configs passing §30 (OOS net>0, win>55%, rr>=1.2, n>=30): {len(passing)}", flush=True)
    print(
        f"  best OOS net = ${best['net']:.3f}  (win {best['win'] * 100:.1f}%, n={best['n']}) "
        f"at tp={best['tp']} sl={best['sl']} mc={best['mc']} vol={best['vol']}",
        flush=True,
    )
    print(
        f"\n  >>> {'AT LEAST ONE CONFIG VALIDATES — investigate it' if passing else 'NO in-range config clears the bar — confirms the strategy has no edge'} <<<",
        flush=True,
    )


if __name__ == "__main__":
    main()
