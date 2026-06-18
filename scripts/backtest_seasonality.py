#!/usr/bin/env python3
"""
Time-of-day / overnight seasonality probe (research only — NOT a deployed strategy).

Tests the documented crypto anomaly (e.g. Quantpedia "long BTC ~22:00 UTC, hold a few
hours"): is there a UTC entry hour where going long and holding N hours has a positive,
cost-clearing expectancy that survives BOTH a recent walk-forward window AND an untouched
prior-year lockbox, across multiple pairs? If yes → worth building a signal/patterns.py
entry. If no → refute and move on (the loop's edge arbiter is OOS + lockbox, not live).

This is a pure long-hold-by-hour test (no indicators) — it isolates the seasonal effect.
A strongly-NEGATIVE hour is a short candidate and is reported too.

Run (in container):
  docker compose exec -T kestrel python3 scripts/backtest_seasonality.py \
     --days 365 --pairs "BTC/USDT,ETH/USDT,SOL/USDT,DOGE/USDT,BNB/USDT,XRP/USDT" --holds 1,2,3,4,6
"""

from __future__ import annotations

import argparse
import datetime as _dt

import backtest_real as bt  # fetch_ohlcv(pair, timeframe, days, offset_days) -> (src, rows)

_MAKER_RT = 0.0004  # ~0.02% x2, no slippage (post-only)
_TAKER_RT = 0.0018  # 0.04% x2 + 0.05% x2 slippage  (CLAUDE.md §13)


def _hour(ts_ms: int) -> int:
    return _dt.datetime.utcfromtimestamp(ts_ms / 1000).hour


def _returns_by_hour(rows: list[list], hold: int) -> dict[int, list[float]]:
    """rows = [[ts,o,h,l,c,v],...] hourly. Long at close[i], exit at close[i+hold]."""
    by_hour: dict[int, list[float]] = {h: [] for h in range(24)}
    for i in range(len(rows) - hold):
        entry_c = rows[i][4]
        exit_c = rows[i + hold][4]
        if entry_c <= 0:
            continue
        by_hour[_hour(rows[i][0])].append((exit_c - entry_c) / entry_c)  # gross long return
    return by_hour


def _agg(window_name: str, pairs: list[str], days: int, offset: int, holds: list[int]) -> list[dict]:
    # accumulate gross returns per (hour, hold) across all pairs
    acc: dict[tuple[int, int], list[float]] = {}
    for pair in pairs:
        src, rows = bt.fetch_ohlcv(pair, "1h", days, offset)
        print(f"  [{window_name}] {pair}: src={src} candles={len(rows)}")
        for hold in holds:
            bh = _returns_by_hour(rows, hold)
            for h, rets in bh.items():
                acc.setdefault((h, hold), []).extend(rets)
    out = []
    for (h, hold), rets in acc.items():
        if len(rets) < 100:
            continue
        n = len(rets)
        gross = sum(rets) / n
        win = 100.0 * sum(1 for r in rets if r > 0) / n
        out.append(
            {
                "hour": h,
                "hold": hold,
                "n": n,
                "gross_pct": gross * 100,
                "net_maker_pct": (gross - _MAKER_RT) * 100,
                "net_taker_pct": (gross - _TAKER_RT) * 100,
                "win": win,
            }
        )
    return out


def _top(rows: list[dict], key: str, k: int = 6, rev: bool = True) -> list[dict]:
    return sorted(rows, key=lambda r: r[key], reverse=rev)[:k]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--pairs", default="BTC/USDT,ETH/USDT,SOL/USDT,DOGE/USDT,BNB/USDT,XRP/USDT")
    ap.add_argument("--holds", default="1,2,3,4,6")
    args = ap.parse_args()
    pairs = [p.strip() for p in args.pairs.split(",")]
    holds = [int(x) for x in args.holds.split(",")]

    print(f"=== SEASONALITY PROBE (1h, {args.days}d, pairs={len(pairs)}, holds={holds}) ===")
    print("RECENT (walk-forward proxy):")
    recent = _agg("recent", pairs, args.days, 0, holds)
    print("LOCKBOX (prior year, never searched):")
    lock = _agg("lockbox", pairs, args.days, args.days, holds)

    rec_by = {(r["hour"], r["hold"]): r for r in recent}
    lock_by = {(r["hour"], r["hold"]): r for r in lock}

    print("\n-- RECENT top by gross long return (hour UTC / hold h) --")
    print("  hour hold     n   win%  gross%  netMaker%  netTaker%")
    for r in _top(recent, "gross_pct"):
        print(
            f"  {r['hour']:>4} {r['hold']:>4} {r['n']:>6} {r['win']:>5.1f} {r['gross_pct']:>7.3f} {r['net_maker_pct']:>9.3f} {r['net_taker_pct']:>9.3f}"
        )

    print("\n-- VERDICT: (hour,hold) that are NET-MAKER-POSITIVE in BOTH recent AND lockbox --")
    survivors = []
    for key, r in rec_by.items():
        lk = lock_by.get(key)
        if lk and r["net_maker_pct"] > 0 and lk["net_maker_pct"] > 0:
            survivors.append((key, r, lk))
    if not survivors:
        print("  NONE — no entry-hour clears maker cost in both windows (seasonality not a durable edge)")
    else:
        for (h, hold), r, lk in sorted(survivors, key=lambda x: x[1]["net_maker_pct"], reverse=True)[:10]:
            print(
                f"  hour={h:>2} hold={hold}h  recent netMaker={r['net_maker_pct']:+.3f}% (n={r['n']}, win {r['win']:.0f}%)"
                f"  | lockbox netMaker={lk['net_maker_pct']:+.3f}% (n={lk['n']}, win {lk['win']:.0f}%)"
            )
    print(f"\nsurvivors: {len(survivors)} (taker bar is stricter — see netTaker columns)")


if __name__ == "__main__":
    main()
