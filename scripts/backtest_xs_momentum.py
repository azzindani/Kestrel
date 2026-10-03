#!/usr/bin/env python3
"""Cross-pair momentum backtest (iter 75, 2026-10-03, owner "i want to see profits ... use math").

Plain ranking rule, no fitted model. At each rebalance the universe is ranked by its
past-L return; the top third is held long and the bottom third short, equal weight,
dollar-neutral, gross exposure 1 (unlevered). Position set at one close earns the next
bar's return.

WHY THIS EXISTS. Every 5m entry in the project measures ~0 bps before costs against a
~7 bps cost. The number that decides profitability is GROSS PROFIT PER UNIT TRADED versus
cost per unit traded (taker+slip 9 bps, maker ~3 bps). --ladder shows that number across
rebalance horizons: cross-pair REVERSAL is real but tiny at minutes, and the sign turns to
MOMENTUM at multi-day lookbacks, where one unit of turnover earns several times its cost.

SCOPE NOTE. Daily / 4h rebalancing is outside the minutes-scalp design (§6, §13 allows
high-TF "solely as a backtest comparison number"). Research only — nothing here is wired
into the daemon. The owner decides after seeing the numbers.

PARTS.
  A  7 years of daily spot closes (api.binance.com, cached under reports/xs_momentum/),
     three eras fixed up-front: variants are chosen on EXPLORE and read off the other two.
     Two cost models: project (9 bps per unit traded + 3 bps/day funding on gross, always
     charged — §13) and maker (3 bps per unit traded, funding neutral).
  B  the last two years on futures 1h closes with the ACTUAL funding each leg paid or
     received (data.binance.vision, the cache backtest_funding_carry.py already fills).
  --ladder  gross bps per unit traded for 1h / 4h / 1d rebalancing on the Part-B data.

CAVEATS. The universe is today's liquid pairs (survivorship); a coin enters once it has
90 daily closes. A month is positive ~60% of the time: a real edge of this size still
takes months to show.

Run (host, numpy only; keep it under a memory cap on the shared host):
  systemd-run --scope -p MemoryMax=1200M python3 scripts/backtest_xs_momentum.py [--ladder]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_funding_carry import _ARCHIVE, _csv_rows, _download, _fut_symbol, _ms  # noqa: E402
from backtest_funding_carry import _CACHE as _FUND_CACHE  # noqa: E402
from build_momentum_lab import SCALP_PAIRS  # noqa: E402 — research harness import path

_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "xs_momentum")
_DAY = 86_400_000
_HOUR = 3_600_000
_MIN_HISTORY = 90  # daily closes before a coin joins the universe

TURN_TAKER, TURN_MAKER, FUND_DAY = 9e-4, 3e-4, 3e-4

ERAS = {
    "explore 2022-24": ("2022-01-01", "2025-01-01"),
    "recent 2025-26": ("2025-01-01", "2026-10-03"),
    "old 2020-21": ("2020-01-01", "2022-01-01"),
}
FUT_ERAS = {"2024-11..2025-09": ("2024-11-15", "2025-10-01"), "2025-10..2026-09": ("2025-10-01", "2026-10-01")}
VARIANTS = [(L, every, band) for L in (14, 28) for every in (1, 7) for band in (False, True)]


def _ts(day: str) -> int:
    return int(_dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def _spot_daily(base: str) -> dict[int, float]:
    """{day open ts: close} from Binance spot, cached; today's unfinished candle dropped."""
    path = os.path.join(_CACHE, f"{base}USDT-1d.json")
    if os.path.exists(path):
        with open(path) as fh:
            return {int(k): v for k, v in json.load(fh).items()}
    out: dict[int, float] = {}
    start = _ts("2019-10-01")
    while True:
        url = f"https://api.binance.com/api/v3/klines?symbol={base}USDT&interval=1d&limit=1000&startTime={start}"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                rows = json.loads(resp.read())
        except Exception:  # noqa: BLE001 — a missing symbol or a blocked host both mean "no data"
            rows = []
        if not rows:
            break
        out.update({int(r[0]): float(r[4]) for r in rows})
        start = int(rows[-1][0]) + _DAY
        if len(rows) < 1000:
            break
        time.sleep(0.2)
    today = int(time.time() * 1000) // _DAY * _DAY
    out.pop(today, None)
    os.makedirs(_CACHE, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh)
    return out


def _matrix(series: dict[str, dict[int, float]], step: int) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Align {name: {ts: value}} on a regular grid -> (ts[T], values[T, N] with NaN gaps, names)."""
    names = sorted(n for n, s in series.items() if s)
    lo = min(min(series[n]) for n in names)
    hi = max(max(series[n]) for n in names)
    ts = np.arange(lo, hi + step, step, dtype=np.int64)
    mat = np.full((len(ts), len(names)), np.nan)
    for j, n in enumerate(names):
        keys = np.fromiter(series[n].keys(), dtype=np.int64)
        vals = np.fromiter(series[n].values(), dtype=float)
        ok = (keys - lo) % step == 0
        mat[(keys[ok] - lo) // step, j] = vals[ok]
    return ts, mat, names


def _returns(closes: np.ndarray) -> np.ndarray:
    ret = np.full_like(closes, np.nan)
    ret[1:] = closes[1:] / closes[:-1] - 1.0
    return ret


def _rank_pct(row: np.ndarray) -> np.ndarray:
    """Percentile rank within the row (average ties not needed: returns are continuous)."""
    out = np.full_like(row, np.nan)
    ok = np.isfinite(row)
    n = int(ok.sum())
    if n >= 3:
        out[ok] = (np.argsort(np.argsort(row[ok])) + 1) / n
    return out


def book(closes: np.ndarray, lookback: int, every: int, band: bool, min_history: int) -> np.ndarray:
    """Target weights[T, N]: +0.5 spread over the long third, -0.5 over the short third.

    band=True keeps a held coin until its rank crosses the median (fewer round trips).
    """
    T, N = closes.shape
    seen = np.cumsum(np.isfinite(closes), axis=0)
    weights = np.zeros((T, N))
    side = np.zeros(N)
    for t in range(lookback, T):
        past = closes[t] / closes[t - lookback] - 1.0
        past[seen[t - 1] < min_history] = np.nan
        rk = _rank_pct(past)
        live = np.isfinite(rk)
        if t % every == 0:
            long_ = rk >= 2 / 3
            short = rk <= 1 / 3
            if band:
                long_ |= (side > 0) & (rk >= 0.5)
                short |= (side < 0) & (rk <= 0.5)
            side = np.where(live, long_.astype(float) - short.astype(float), 0.0)
        else:
            side = np.where(live, side, 0.0)
        n_long, n_short = (side > 0).sum(), (side < 0).sum()
        if n_long and n_short:
            weights[t] = np.where(side > 0, 0.5 / n_long, 0.0) - np.where(side < 0, 0.5 / n_short, 0.0)
    return weights


def pnl(weights: np.ndarray, ret: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(gross return, turnover) per bar: weights set at t-1 earn bar t; turnover is paid when they are set."""
    held = np.vstack([np.zeros((1, weights.shape[1])), weights[:-1]])
    gross = np.nansum(held * ret, axis=1)
    trade = np.abs(np.diff(weights, axis=0, prepend=np.zeros((1, weights.shape[1])))).sum(axis=1)
    turnover = np.concatenate([[0.0], trade[:-1]])
    return gross, turnover


def stats(x: np.ndarray, per_year: float) -> tuple[float, float, float, float]:
    """(annual %, Sharpe, t-stat, max drawdown %)."""
    if len(x) < 2 or x.std() == 0:
        return 0.0, 0.0, 0.0, 0.0
    equity = np.cumprod(1.0 + x)
    dd = float((equity / np.maximum.accumulate(equity) - 1.0).min())
    sharpe = x.mean() / x.std()
    return x.mean() * per_year * 100, sharpe * np.sqrt(per_year), sharpe * np.sqrt(len(x)), dd * 100


def _label(lookback: int, every: int, band: bool) -> str:
    return f"L{lookback}d reb{every}d{' band' if band else ''}"


def part_a() -> None:
    bases = sorted({p.split("/")[0] for p in SCALP_PAIRS})
    ts, closes, names = _matrix({b: _spot_daily(b) for b in bases}, _DAY)
    keep = [j for j in range(len(names)) if np.isfinite(closes[:, j]).sum() >= _MIN_HISTORY + 28]
    closes, names = closes[:, keep], [names[j] for j in keep]
    ret = _returns(closes)
    print(
        f"PART A — daily spot closes, {len(names)} coins, {_dt.datetime.fromtimestamp(ts[0] / 1000, _dt.timezone.utc).date()} .. "
        f"{_dt.datetime.fromtimestamp(ts[-1] / 1000, _dt.timezone.utc).date()}"
    )
    print(
        "  gross ann% / Sharpe | project cost: ann% / Sharpe / t | maker cost: ann% / Sharpe / t / maxDD% | turnover %/day"
    )
    runs = {}
    for lookback, every, band in VARIANTS:
        w = book(closes, lookback, every, band, _MIN_HISTORY)
        gross, turn = pnl(w, ret)
        held = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])
        runs[_label(lookback, every, band)] = (gross, turn, np.abs(held).sum(axis=1))
    for era, (a, b) in ERAS.items():
        m = (ts >= _ts(a)) & (ts < _ts(b))
        print(f"\n  === {era} ===")
        for name, (gross, turn, exposure) in runs.items():
            ga, gs, _, _ = stats(gross[m], 365)
            pa, ps, pt, _ = stats((gross - turn * TURN_TAKER - exposure * FUND_DAY)[m], 365)
            ma, ms, mt, mdd = stats((gross - turn * TURN_MAKER)[m], 365)
            print(
                f"  {name:18s} {ga:6.1f} {gs:5.2f} | {pa:6.1f} {ps:5.2f} {pt:5.2f} | {ma:6.1f} {ms:5.2f} {mt:5.2f} {mdd:6.1f} | {turn[m].mean() * 100:5.1f}"
            )
    gross, turn, _ = runs[_label(28, 1, False)]  # the EXPLORE-era best
    net = gross - turn * TURN_MAKER
    years = np.array([_dt.datetime.fromtimestamp(t / 1000, _dt.timezone.utc).year for t in ts])
    by_year = "  ".join(
        f"{y}:{(np.prod(1 + net[years == y]) - 1) * 100:+.0f}%" for y in range(2020, int(years[-1]) + 1)
    )
    print(f"\n  explore-era best ({_label(28, 1, False)}), maker cost, unlevered, by year: {by_year}")


def _fut_hourly(pair: str, months: list[tuple[int, int]]) -> tuple[dict[int, float], dict[int, float]]:
    """({hour open ts: close}, {UTC day ts: summed funding rate}) from the shared archive cache."""
    for fut in _fut_symbol(pair):
        closes: dict[int, float] = {}
        funding: dict[int, float] = {}
        for y, m in months:
            tag = f"{y}-{m:02d}"
            kb = _download(
                f"{_ARCHIVE}/futures/um/monthly/klines/{fut}/1h/{fut}-1h-{tag}.zip",
                f"{_FUND_CACHE}/fut/{fut}-1h-{tag}.zip",
            )
            if kb:
                for r in _csv_rows(kb):
                    closes[_ms(r[0]) // _HOUR * _HOUR] = float(r[4])
            fb = _download(
                f"{_ARCHIVE}/futures/um/monthly/fundingRate/{fut}/{fut}-fundingRate-{tag}.zip",
                f"{_FUND_CACHE}/fut/{fut}-fundingRate-{tag}.zip",
            )
            if fb:
                for r in _csv_rows(fb):
                    day = _ms(r[0]) // _DAY * _DAY
                    funding[day] = funding.get(day, 0.0) + float(r[2])
        if closes:
            return closes, funding
    return {}, {}


def _resample(ts: np.ndarray, closes: np.ndarray, step: int) -> tuple[np.ndarray, np.ndarray]:
    """Last hourly close of each `step` bucket."""
    pick = np.flatnonzero((ts + _HOUR) % step == 0)
    return ts[pick] + _HOUR - step, closes[pick]


def part_b(ladder: bool) -> None:
    months = [(2024, m) for m in range(10, 13)] + [(2025, m) for m in range(1, 13)] + [(2026, m) for m in range(1, 10)]
    hourly: dict[str, dict[int, float]] = {}
    fund: dict[str, dict[int, float]] = {}
    for pair in SCALP_PAIRS:
        base = pair.split("/")[0]
        hourly[base], fund[base] = _fut_hourly(pair, months)
    hts, hcl, names = _matrix(hourly, _HOUR)
    dts, dcl = _resample(hts, hcl, _DAY)
    ret = _returns(dcl)
    fmat = np.zeros_like(dcl)
    for j, n in enumerate(names):
        for i, t in enumerate(dts):
            fmat[i, j] = fund[n].get(int(t), 0.0)
    print(
        f"\nPART B — futures closes + actual funding, {len(names)} coins (avg funding {fmat.mean() * 1e4:+.2f} bps/day per coin)"
    )
    print(
        "  gross ann% / Sharpe | funding ann% (+ = received) | maker net: ann% / Sharpe / t / maxDD% | taker net: ann% / Sharpe"
    )
    for lookback, every, band in VARIANTS:
        w = book(dcl, lookback, every, band, 30)
        gross, turn = pnl(w, ret)
        held = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])
        fpay = -(held * fmat).sum(axis=1)  # a long pays a positive rate, a short receives it
        for era, (a, b) in FUT_ERAS.items():
            m = (dts >= _ts(a)) & (dts < _ts(b))
            ga, gs, _, _ = stats(gross[m], 365)
            ma, ms, mt, mdd = stats((gross - turn * TURN_MAKER + fpay)[m], 365)
            ta, tsh, _, _ = stats((gross - turn * TURN_TAKER + fpay)[m], 365)
            print(
                f"  {_label(lookback, every, band):18s} {era} | {ga:6.1f} {gs:5.2f} | {fpay[m].mean() * 36500:+5.1f} | "
                f"{ma:6.1f} {ms:5.2f} {mt:5.2f} {mdd:6.1f} | {ta:6.1f} {tsh:5.2f}"
            )
    if not ladder:
        return
    print(
        "\nLADDER — gross bps per unit traded (cost per unit: taker+slip 9, maker ~3); negative = reversal, positive = momentum"
    )
    print("  rebalance | lookback | " + " | ".join(f"{e:>17s}" for e in FUT_ERAS))
    for step, tag, lookbacks in (
        (_HOUR, "1h", (4, 24, 72, 168)),
        (4 * _HOUR, "4h", (6, 18, 42, 84)),
        (_DAY, "1d", (3, 7, 14, 28)),
    ):
        ts, cl = (hts, hcl) if step == _HOUR else _resample(hts, hcl, step)
        r = _returns(cl)
        for lb in lookbacks:
            w = book(cl, lb, 1, False, 30 * _DAY // step)
            gross, turn = pnl(w, r)
            cells = []
            for a, b in FUT_ERAS.values():
                m = (ts >= _ts(a)) & (ts < _ts(b))
                cells.append(f"{gross[m].sum() / max(turn[m].sum(), 1e-12) * 1e4:17.2f}")
            print(f"  {tag:9s} | {lb * step // _HOUR:6d}h  | " + " | ".join(cells))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--ladder", action="store_true", help="also print gross bps per unit traded for 1h / 4h / 1d rebalancing"
    )
    ap.add_argument("--skip-a", action="store_true", help="skip the 7-year daily part (needs api.binance.com)")
    args = ap.parse_args()
    if not args.skip_a:
        part_a()
    part_b(args.ladder)


if __name__ == "__main__":
    main()
