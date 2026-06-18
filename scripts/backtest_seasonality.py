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


def _window_returns(rows: list[list], start: int, window: int, hold: int, non_overlap: bool) -> list[float]:
    """Gross long returns for the DEPLOYED window (hours [start, start+window) mod 24, hold h).

    non_overlap=True keeps only independent samples (next entry after the prior exit) so the
    t-stat is not inflated by overlapping holds — the defensible significance number.
    """
    rets: list[float] = []
    last_exit = -1
    for i in range(len(rows) - hold):
        if (_hour(rows[i][0]) - start) % 24 >= window:
            continue
        if non_overlap and i <= last_exit:
            continue
        ec, xc = rows[i][4], rows[i + hold][4]
        if ec <= 0:
            continue
        rets.append((xc - ec) / ec)
        last_exit = i + hold
    return rets


def _wstats(rets: list[float]) -> "dict | None":
    import math
    import statistics

    n = len(rets)
    if n < 2:
        return None
    m = statistics.fmean(rets)
    sd = statistics.pstdev(rets)
    t = (m * math.sqrt(n) / sd) if sd > 0 else 0.0
    return {
        "n": n,
        "gross_pct": m * 100,
        "net_maker_pct": (m - _MAKER_RT) * 100,
        "t": t,
        "win": 100.0 * sum(1 for r in rets if r > 0) / n,
    }


def _validate(pairs: list[str], days: int, start: int, window: int, hold: int) -> None:
    """Validate the SPECIFIC deployed window per-pair (breadth + non-overlap significance).

    Honesty: the window was selected partly using the lockbox, so neither window here is a
    clean OOS test for it — the definitive OOS test is the live forward cohort. This measures
    BREADTH (is it broad across pairs or 1-2-pair-driven?) and within-sample SIGNIFICANCE.
    """
    end_h = (start + window - 1) % 24
    print(f"=== VALIDATE deployed window: entry hours {start}..{end_h} UTC, hold={hold}h, maker cost ===")
    for label, offset in [("RECENT", 0), ("LOCKBOX", days)]:
        print(f"-- {label} --")
        print("  pair         n  win%  gross%  netMaker%   t(ovlp) | nonOvlp:  n  netMaker%   t")
        pooled: list[float] = []
        pooled_no: list[float] = []
        for p in pairs:
            _src, rows = bt.fetch_ohlcv(p, "1h", days, offset)
            ov = _window_returns(rows, start, window, hold, False)
            no = _window_returns(rows, start, window, hold, True)
            pooled += ov
            pooled_no += no
            s, sn = _wstats(ov), _wstats(no)
            if s and sn:
                print(
                    f"  {p:10} {s['n']:>5} {s['win']:>5.1f} {s['gross_pct']:>7.3f} {s['net_maker_pct']:>9.3f} {s['t']:>8.2f} | {sn['n']:>5} {sn['net_maker_pct']:>9.3f} {sn['t']:>6.2f}"
                )
        ps, pn = _wstats(pooled), _wstats(pooled_no)
        if ps and pn:
            print(
                f"  POOLED     {ps['n']:>5} {ps['win']:>5.1f} {ps['gross_pct']:>7.3f} {ps['net_maker_pct']:>9.3f} {ps['t']:>8.2f} | {pn['n']:>5} {pn['net_maker_pct']:>9.3f} {pn['t']:>6.2f}"
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--pairs", default="BTC/USDT,ETH/USDT,SOL/USDT,DOGE/USDT,BNB/USDT,XRP/USDT")
    ap.add_argument("--holds", default="1,2,3,4,6")
    ap.add_argument("--validate", action="store_true", help="per-pair robustness of the deployed window")
    ap.add_argument("--win-start", type=int, default=18)
    ap.add_argument("--win-hours", type=int, default=7)
    ap.add_argument("--hold", type=int, default=4)
    args = ap.parse_args()
    pairs = [p.strip() for p in args.pairs.split(",")]
    holds = [int(x) for x in args.holds.split(",")]

    if args.validate:
        _validate(pairs, args.days, args.win_start, args.win_hours, args.hold)
        return

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
