#!/usr/bin/env python3
"""Adaptive (online, no-lookahead) selection study over backtest trade dumps (iter 69).

Input: `algo_search.py --dump-trades reports/iter69/trades_<era>.jsonl` per era.
Run:   python3 scripts/study_adaptive_selection.py lbA lbB recent
       (ADAPTIVE_STUDY_DIR overrides the dump directory; default reports/iter69)

Verdict 2026-09-19: REFUTED in all three eras — see retired_strategies.json.

Every trade in the dump is the ungated stream (what dev would trade). A selection rule
keeps a trade only when the trailing stats of its key — computed from trades CLOSED
before this trade's entry — show mean net bps > threshold with enough samples. This is
the learning loop the tiers were designed around: dev feeds, lab/staging act.

Per era it reports kept vs baseline over the SAME post-warm-up period, and a day-level
t-stat (trades within a day are correlated across pairs; the day is the unit).
"""

import collections
import heapq
import json
import math
import os
import statistics as st
import sys

ERAS = sys.argv[1:] or ["lbA", "lbB", "recent"]
DIR = os.environ.get("ADAPTIVE_STUDY_DIR", "reports/iter69")
DAY = 86_400_000
HOUR = 3_600_000


def session(ts: int) -> str:
    h = (ts // HOUR) % 24
    if 13 <= h < 16:
        return "overlap"
    if 8 <= h < 16:
        return "london"
    if 13 <= h < 21:
        return "us"
    return "asian"


KEYS = {
    "algo": lambda t: (t["algo"],),
    "algo_dir": lambda t: (t["algo"], t["direction"]),
    "algo_dir_sess": lambda t: (t["algo"], t["direction"], session(t["entry_ts"])),
    "algo_pair": lambda t: (t["algo"], t["pair"]),
    "algo_pair_dir": lambda t: (t["algo"], t["pair"], t["direction"]),
    "algo_hour": lambda t: (t["algo"], (t["entry_ts"] // HOUR) % 24),
    "pair": lambda t: (t["pair"],),
    "pair_dir": lambda t: (t["pair"], t["direction"]),
}
# window: ("n", N) = last N closed trades of the key; ("d", D) = closes in the last D days
WINDOWS = [("n", 30), ("n", 100), ("d", 2), ("d", 7)]
MIN_NS = [20, 50]
THRESH = 0.0


def load(era: str) -> list[dict]:
    with open(f"{DIR}/trades_{era}.jsonl", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    rows.sort(key=lambda t: (t["entry_ts"], t["pair"], t["algo"]))
    return rows


def simulate(rows: list[dict], keyfn, window, min_n: int) -> tuple[list[dict], int]:
    """Return (kept trades, warm-up end ts)."""
    pending: list[tuple[int, int, tuple, float]] = []  # (exit_ts, seq, key, net_bps)
    hist: dict[tuple, collections.deque] = collections.defaultdict(collections.deque)
    kept = []
    seq = 0
    kind, size = window
    for t in rows:
        now = t["entry_ts"]
        while pending and pending[0][0] <= now:
            ex, _, k, nb = heapq.heappop(pending)
            dq = hist[k]
            dq.append((ex, nb))
            if kind == "n" and len(dq) > size:
                dq.popleft()
        k = keyfn(t)
        dq = hist[k]
        if kind == "d":
            while dq and dq[0][0] < now - size * DAY:
                dq.popleft()
        if len(dq) >= min_n and sum(nb for _, nb in dq) / len(dq) > THRESH:
            kept.append(t)
        seq += 1
        heapq.heappush(pending, (t["exit_ts"], seq, k, t["net_bps"]))
    return kept, rows[0]["entry_ts"] + 7 * DAY  # score from day 7 on (all rules warmed)


def day_stats(trades: list[dict]) -> tuple[float, float, int, float]:
    """(mean net bps/trade, day-level t-stat of daily net $, n_days, share of + days)."""
    if not trades:
        return 0.0, 0.0, 0, 0.0
    by_day: dict[int, float] = collections.defaultdict(float)
    for t in trades:
        by_day[t["entry_ts"] // DAY] += t["net_usdt"]
    days = list(by_day.values())
    mean_bps = st.mean(t["net_bps"] for t in trades)
    if len(days) < 3:
        return mean_bps, 0.0, len(days), 0.0
    sd = st.stdev(days)
    tstat = st.mean(days) / (sd / math.sqrt(len(days))) if sd > 0 else 0.0
    return mean_bps, tstat, len(days), sum(1 for d in days if d > 0) / len(days)


def main() -> None:
    data = {e: load(e) for e in ERAS}
    results: dict[tuple, dict[str, tuple]] = {}
    for era, rows in data.items():
        for kname, kfn in KEYS.items():
            for w in WINDOWS:
                for mn in MIN_NS:
                    kept, t0 = simulate(rows, kfn, w, mn)
                    kept = [t for t in kept if t["entry_ts"] >= t0]
                    base = [t for t in rows if t["entry_ts"] >= t0]
                    kb, kt, kd, kp = day_stats(kept)
                    bb, _bt, _bd, _bp = day_stats(base)
                    results.setdefault((kname, w, mn), {})[era] = (
                        len(kept),
                        len(base),
                        kb,
                        bb,
                        sum(t["net_usdt"] for t in kept),
                        kt,
                        kp,
                    )
    print(f"eras={ERAS}  cells: kept n / share  kept net bps (baseline)  kept net $  day-t  +days%")
    order = sorted(results.items(), key=lambda kv: -min(v[2] for v in kv[1].values()))
    for (kname, w, mn), per in order:
        cells = []
        for era in ERAS:
            n, nb, kb, bb, usd, tt, pp = per[era]
            cells.append(
                f"{era}: n{n:5d}/{n / max(nb, 1):4.0%} {kb:+6.2f}({bb:+5.2f}) ${usd:+7.2f} t{tt:+5.2f} {pp:4.0%}"
            )
        allpos = all(per[e][2] > 0 for e in ERAS)
        print(f"{'*' if allpos else ' '} {kname:14s} {w[0]}{w[1]:<4d} min{mn:<3d} | " + " | ".join(cells))


if __name__ == "__main__":
    main()
