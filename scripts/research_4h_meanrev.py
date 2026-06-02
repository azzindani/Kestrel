#!/usr/bin/env python3
"""
Research (analysis only — does NOT touch the live 5m project or §13).

Hardens the 4h ema_spread mean-reversion edge found by edge_scan.py:
  - Real strategy: fade extreme ema_spread (short when stretched high, long when
    stretched low), non-overlapping, fixed hold.
  - Thresholds derived from TRAIN ONLY (no lookahead), applied to train + test.
  - Walk-forward 60/40, on BTC and ETH, under taker AND maker cost assumptions.

A real edge must show POSITIVE out-of-sample net expectancy with the same sign
as in-sample, on both pairs, ideally surviving taker cost (maker is the upside).

Run:
  docker run --rm --entrypoint python -v /root/Kestrel:/app -w /app \
    kestrel-kestrel:latest -u scripts/research_4h_meanrev.py --days 365
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

# Cost assumptions (round-trip, price terms). Labelled — not gospel.
TAKER_RT = 0.0018  # §13: 0.04% taker ×2 + 0.05% slippage ×2
MAKER_RT = 0.0006  # optimistic limit entry: ~0 maker fee + ~0.03%/side slippage
BANDS = [0.15, 0.25]   # fade the most-extreme 15% / 25% tail of ema_spread
HOLDS = [2, 4]         # candles held (4h each → 8h / 16h)


def _series(cfg, base, pair, tf, days):
    ex, rows = bt.fetch_ohlcv(pair, tf, days)
    candles = bt.build_candles(cfg, base, rows)
    C = [c for c in candles if c.ema21 and c.ema9 and c.atr14]
    closes = np.array([c.close for c in C], float)
    es = np.array([(c.ema9 - c.ema21) / c.close for c in C], float)
    return ex, closes, es


def _gen(es, closes, i0, i1, hi, lo, H, cost):
    """Non-overlapping fade trades over [i0, i1). Returns net per-trade returns."""
    out, i = [], i0
    while i < i1 - H:
        s = es[i]
        d = -1 if s >= hi else (1 if s <= lo else 0)
        if d:
            out.append(d * (closes[i + H] - closes[i]) / closes[i] - cost)
            i += H
        else:
            i += 1
    return np.array(out)


def _stat(a):
    if len(a) == 0:
        return dict(n=0, win=0.0, avg=0.0, tot=0.0, t=0.0)
    n = len(a)
    sd = a.std(ddof=1) if n > 1 else 0.0
    t = (a.mean() / (sd / np.sqrt(n))) if sd > 0 else 0.0
    return dict(n=n, win=float((a > 0).mean()), avg=float(a.mean()), tot=float(a.sum()), t=float(t))


def _fmt(tag, s):
    return (f"    {tag:14s} n={s['n']:4d}  win={s['win']*100:5.1f}%  "
            f"avg_net={s['avg']*100:+.4f}%  total={s['tot']*100:+.2f}%  t={s['t']:+.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--pairs", default="BTC/USDT,ETH/USDT")
    args = ap.parse_args()

    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    base = load_params("params.json")

    print(f"=== 4h ema_spread mean-reversion research ({args.tf}, {args.days}d) ===")
    print(f"costs: taker_rt={TAKER_RT*100:.3f}%  maker_rt={MAKER_RT*100:.3f}% (optimistic)\n")

    survived = []
    for pair in args.pairs.split(","):
        ex, closes, es = _series(cfg, base, pair, args.tf, args.days)
        split = int(len(closes) * 0.60)
        print(f"### {pair}  (source={ex}, candles={len(closes)}, "
              f"train {split}/test {len(closes)-split}) ###")
        for band in BANDS:
            hi = float(np.quantile(es[:split], 1 - band))   # thresholds from TRAIN only
            lo = float(np.quantile(es[:split], band))
            for H in HOLDS:
                tr_t = _gen(es, closes, 0, split, hi, lo, H, TAKER_RT)
                te_t = _gen(es, closes, split, len(closes), hi, lo, H, TAKER_RT)
                te_m = _gen(es, closes, split, len(closes), hi, lo, H, MAKER_RT)
                st_tr, st_te, st_tem = _stat(tr_t), _stat(te_t), _stat(te_m)
                print(f"  band={band} hold={H}")
                print(_fmt("train(taker)", st_tr))
                print(_fmt("test(taker)", st_te))
                print(_fmt("test(maker)", st_tem))
                # real-edge test: OOS positive, same sign as IS, decent sample
                if st_tr["avg"] > 0 and st_te["avg"] > 0 and st_te["n"] >= 30:
                    survived.append((pair, band, H, st_te["avg"], st_te["t"], st_tem["avg"]))
        print()

    print("=== VERDICT ===")
    if survived:
        print("  configs with POSITIVE out-of-sample taker expectancy (same sign as train, n>=30):")
        for pair, band, H, avg, t, mavg in sorted(survived, key=lambda r: -r[3]):
            print(f"    {pair} band={band} hold={H}: OOS avg_net(taker)={avg*100:+.4f}% (t={t:+.2f}), "
                  f"maker={mavg*100:+.4f}%")
        print("\n  >>> 4h ema_spread edge SHOWS OUT-OF-SAMPLE PROMISE — proceed to full strategy build <<<")
    else:
        print("  no config holds positive OOS taker expectancy with adequate sample.")
        print("  (check the maker rows — the edge may only be viable with limit/maker entries)")
        print("\n  >>> 4h edge DID NOT survive as a tradeable strategy under taker cost <<<")


if __name__ == "__main__":
    main()
