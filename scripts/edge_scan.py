#!/usr/bin/env python3
"""
Predictive-power scan — does ANY feature predict forward returns on real data?

Prerequisite question before building any new strategy: is there exploitable
structure at all? For each causal feature available at candle close, measure:
  - IC: Pearson correlation between the feature and the forward return.
  - Quintile spread: mean forward return of the top feature-quintile minus the
    bottom. This is the "go long the top, short the bottom" edge.
The only thing that matters economically is whether |spread| beats the
~0.18% round-trip cost (CLAUDE.md §13/§17). Below that, it is untradeable noise.

Run:
  docker run --rm --entrypoint python -v /root/Kestrel:/app -w /app \
    kestrel-kestrel:latest -u scripts/edge_scan.py --days 90
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import numpy as np
from dotenv import load_dotenv

from src.config import AppConfig, load_params
import backtest_real as bt

COST_RT = 0.0018  # ~0.18% round-trip price cost (taker fees + slippage)
HORIZONS = [1, 4, 8]  # candles forward (strategy holds ~3-4)


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 30 or x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def _quintile_spread(feat: np.ndarray, fwd: np.ndarray) -> tuple[float, float, float]:
    order = np.argsort(feat)
    k = len(order) // 5
    if k < 30:
        return 0.0, 0.0, 0.0
    lo = float(fwd[order[:k]].mean())
    hi = float(fwd[order[-k:]].mean())
    return hi - lo, lo, hi


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--tf", default=None, help="timeframe override (e.g. 1h, 4h)")
    ap.add_argument("--walkforward", action="store_true", help="split 60/40 and check edge holds out-of-sample")
    args = ap.parse_args()

    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    base = load_params("params.json")
    tf = args.tf or cfg.timeframe_entry

    print(f"=== Kestrel predictive-power scan ({cfg.pair} {tf}, {args.days}d) ===", flush=True)
    ex_used, rows = bt.fetch_ohlcv(cfg.pair, tf, args.days)
    candles = bt.build_candles(cfg, base, rows)
    C = [c for c in candles if c.rsi14 is not None and c.adx is not None and c.atr14 and c.ema21]
    n = len(C)
    closes = np.array([c.close for c in C], float)
    print(f"source={ex_used}  usable candles={n}  cost_threshold={COST_RT * 100:.3f}% round-trip\n", flush=True)

    # Causal numeric features at candle i
    feats = {
        "rsi14": np.array([c.rsi14 for c in C], float),
        "volume_ratio": np.array([c.volume_ratio for c in C], float),
        "adx": np.array([c.adx for c in C], float),
        "bb_width": np.array([c.bb_width or 0.0 for c in C], float),
        "body_ratio": np.array([c.body_ratio or 0.0 for c in C], float),
        "atr_pct": np.array([c.atr14 / c.close for c in C], float),
        "ema_spread": np.array([((c.ema9 or c.close) - c.ema21) / c.close for c in C], float),
        "prior_ret1": np.concatenate([[0.0], np.diff(closes) / closes[:-1]]),  # momentum
    }

    print(f"{'feature':14s} " + "".join(f"  IC@{h:<2d}  |Q5-Q1|@{h:<2d}" for h in HORIZONS), flush=True)
    best_spread = 0.0
    best_desc = ""
    for name, fv in feats.items():
        cells = ""
        for h in HORIZONS:
            fwd = (closes[h:] - closes[:-h]) / closes[:-h]
            fa = fv[: len(fwd)]
            ic = _pearson(fa, fwd)
            spread, _, _ = _quintile_spread(fa, fwd)
            if abs(spread) > abs(best_spread):
                best_spread, best_desc = spread, f"{name} @h={h}"
            cells += f"  {ic:+.3f}    {spread * 100:+.3f}%"
        print(f"{name:14s} {cells}", flush=True)

    # Baseline: typical move size vs cost
    fwd4 = (closes[4:] - closes[:-4]) / closes[:-4]
    frac_exceed = float(np.mean(np.abs(fwd4) > COST_RT))
    print(
        f"\nbaseline 4-candle move: mean|ret|={np.mean(np.abs(fwd4)) * 100:.3f}%  "
        f"std={fwd4.std() * 100:.3f}%  P(|move|>cost)={frac_exceed * 100:.1f}%",
        flush=True,
    )

    # Categorical: prior candle direction, hour-of-day
    print("\nby prior-candle direction (mean 4-candle fwd return):", flush=True)
    dirs = [c.direction for c in C]
    for d in sorted(set(dirs)):
        idx = [i for i in range(len(fwd4)) if dirs[i] == d]
        if idx:
            print(f"  {str(d):10s} n={len(idx):6d}  mean_fwd={np.mean(fwd4[idx]) * 100:+.4f}%", flush=True)

    hours = np.array([(c.ts // 3_600_000) % 24 for c in C])[: len(fwd4)]
    hr_means = [(h, float(fwd4[hours == h].mean()) if (hours == h).any() else 0.0) for h in range(24)]
    hr_means.sort(key=lambda kv: kv[1])
    print("\nhour-of-day extremes (UTC, mean 4-candle fwd return):", flush=True)
    print(
        f"  worst: h{hr_means[0][0]:02d}={hr_means[0][1] * 100:+.4f}%   "
        f"best: h{hr_means[-1][0]:02d}={hr_means[-1][1] * 100:+.4f}%",
        flush=True,
    )

    # Walk-forward: does the edge hold in BOTH halves with the same sign + above cost?
    if args.walkforward:
        H = 4
        split = int(n * 0.60)

        def _stats(seg_closes: np.ndarray, seg_feats: dict) -> dict:
            fwd = (seg_closes[H:] - seg_closes[:-H]) / seg_closes[:-H]
            out = {}
            for nm, fv in seg_feats.items():
                fa = fv[: len(fwd)]
                out[nm] = (_pearson(fa, fwd), _quintile_spread(fa, fwd)[0])
            return out

        tr = _stats(closes[:split], {k: v[:split] for k, v in feats.items()})
        te = _stats(closes[split:], {k: v[split:] for k, v in feats.items()})
        print(f"\n=== WALK-FORWARD (h={H}, train {split} / test {n - split}) ===", flush=True)
        print(f"{'feature':14s}  train_IC  train_spread   test_IC  test_spread   consistent?", flush=True)
        survivors = []
        for nm in feats:
            tic, tsp = tr[nm]
            eic, esp = te[nm]
            same_sign = (tsp * esp) > 0
            both_beat = abs(tsp) > COST_RT and abs(esp) > COST_RT
            ok = same_sign and both_beat
            if ok:
                survivors.append(nm)
            print(
                f"{nm:14s}  {tic:+.3f}   {tsp * 100:+.3f}%     {eic:+.3f}   {esp * 100:+.3f}%     "
                f"{'YES ✓' if ok else ('sign-flip' if not same_sign else 'below-cost')}",
                flush=True,
            )
        print(
            f"\n  features with edge surviving out-of-sample (same sign, both > cost): "
            f"{survivors if survivors else 'NONE'}",
            flush=True,
        )

    print(f"\n=== VERDICT ===", flush=True)
    print(
        f"  strongest feature edge: {best_desc}  |Q5-Q1|={abs(best_spread) * 100:.3f}%  vs cost {COST_RT * 100:.3f}%",
        flush=True,
    )
    tradeable = abs(best_spread) > COST_RT
    print(
        f"\n  >>> {'POTENTIAL EDGE — strongest spread exceeds cost; worth modelling' if tradeable else 'NO EXPLOITABLE EDGE — no feature spread beats the cost threshold'} <<<",
        flush=True,
    )
    print("  (caveat: overlapping forward windows inflate significance; magnitude vs cost is what matters)", flush=True)


if __name__ == "__main__":
    main()
