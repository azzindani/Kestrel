#!/usr/bin/env python3
"""Lead-lag check (iter 69c): does BTC's 5m move predict the alts' NEXT candles?

REFUTED 2026-09-19 in all three eras — see retired_strategies.json. Reads the per-day
OHLCV cache written by algo_search/backtest_real (reports/ohlcv_cache, 20260919 files).

Entry at alt close[t], exit at close[t+k]. Signed by the signal direction. Gross bps, no fees.
Signals:
  big_btc      |r_btc[t]| in BTC's top-5% of |r| (trailing 2-day quantile): trade alt in BTC's direction
  lag_resid    same BTC trigger, alt's same-candle move < 0.5 * beta * r_btc (under-reacted): catch-up
  over_resid   same BTC trigger, alt over-reacted (> 1.5 * beta * r_btc): fade toward beta
"""

import collections
import json
import math
import statistics as st

CACHE = "reports/ohlcv_cache"
ERAS = {"lbA": "90d_off365", "lbB": "90d_off180", "recent": "45d_off0"}
ALTS = ["ETH", "SOL", "DOGE", "PEPE", "XRP", "BNB", "ADA", "AVAX", "HYPE"]
DAY = 86_400_000


def load(sym: str, tag: str) -> dict[int, float]:
    try:
        with open(f"{CACHE}/{sym}_USDT_5m_{tag}_20260919.json", encoding="utf-8") as fh:
            rows = json.load(fh)["rows"]
    except FileNotFoundError:
        return {}
    return {int(r[0]): float(r[4]) for r in rows}


def rets(closes: dict[int, float]) -> dict[int, float]:
    ts = sorted(closes)
    return {ts[i]: closes[ts[i]] / closes[ts[i - 1]] - 1.0 for i in range(1, len(ts)) if ts[i] - ts[i - 1] == 300_000}


def main() -> None:
    for era, tag in ERAS.items():
        btc = rets(load("BTC", tag))
        bts = sorted(btc)
        # trailing 2-day 95th pct of |r_btc| (576 candles), no lookahead
        thr: dict[int, float] = {}
        win: collections.deque = collections.deque()
        for t in bts:
            if len(win) >= 288:
                thr[t] = sorted(win)[int(0.95 * len(win))]
            win.append(abs(btc[t]))
            if len(win) > 576:
                win.popleft()
        out: dict[tuple[str, int], list[tuple[int, float]]] = collections.defaultdict(list)
        for alt in ALTS:
            closes = load(alt, tag)
            if not closes:
                continue
            ar = rets(closes)
            ats = sorted(closes)
            idx = {t: i for i, t in enumerate(ats)}
            # trailing beta over the last 576 candles (cov/var), updated incrementally
            pairs: collections.deque = collections.deque()
            for t in bts:
                if t not in ar or t not in idx:
                    continue
                rb, ra = btc[t], ar[t]
                beta = None
                if len(pairs) >= 288:
                    xs = [p[0] for p in pairs]
                    ys = [p[1] for p in pairs]
                    mx, my = st.fmean(xs), st.fmean(ys)
                    vx = sum((x - mx) ** 2 for x in xs)
                    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx if vx > 0 else None
                pairs.append((rb, ra))
                if len(pairs) > 576:
                    pairs.popleft()
                if t not in thr or abs(rb) < thr[t] or beta is None:
                    continue
                d = 1.0 if rb > 0 else -1.0
                i = idx[t]
                for k in (1, 2, 3, 6):
                    if i + k >= len(ats):
                        continue
                    fwd = closes[ats[i + k]] / closes[t] - 1.0
                    out[("big_btc", k)].append((t, d * fwd * 1e4))
                    expected = beta * rb
                    if abs(ra) < 0.5 * abs(expected) or ra * rb < 0:
                        out[("lag_resid", k)].append((t, d * fwd * 1e4))
                    if abs(ra) > 1.5 * abs(expected) and ra * rb > 0:
                        out[("over_resid", k)].append((t, -d * fwd * 1e4))
        print(f"== {era} ==")
        for (sig, k), xs in sorted(out.items()):
            vals = [v for _, v in xs]
            by_day: dict[int, list[float]] = collections.defaultdict(list)
            for t, v in xs:
                by_day[t // DAY].append(v)
            dm = [st.fmean(v) for v in by_day.values()]
            tday = st.fmean(dm) / (st.stdev(dm) / math.sqrt(len(dm))) if len(dm) > 2 and st.stdev(dm) > 0 else 0.0
            print(
                f"  {sig:10s} k={k}  n={len(vals):6d}  mean={st.fmean(vals):+7.2f} bps  "
                f"win={sum(v > 0 for v in vals) / len(vals):5.1%}  day-t={tday:+5.2f}  days+={sum(m > 0 for m in dm) / len(dm):4.0%}"
            )


if __name__ == "__main__":
    main()
