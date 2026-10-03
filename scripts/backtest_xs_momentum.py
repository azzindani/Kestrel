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
  --bots    the form the fleet would trade: one bot per pair on 4h candles, one decision
            candle per day, the sim's close-resolved exits, costs, volume gate and
            isolated-margin liquidation. Verdict (iter 75): at 20x with ATR stops half the
            positions are stopped out inside two days and the effect is gone; it needs
            ~3x leverage and no tight stop.

CAVEATS. The universe is today's liquid pairs (survivorship); a coin enters once it has
90 daily closes. A month is positive ~60% of the time: a real edge of this size still
takes months to show.

Run (host, numpy only; keep it under a memory cap on the shared host):
  systemd-run --scope -p MemoryMax=1200M python3 scripts/backtest_xs_momentum.py [--ladder | --bots]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import urllib.request
from typing import Optional

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


# ---------------------------------------------------------------------------
# BOT FORM — what the fleet would actually trade (one bot per pair, 4h candles).
#
# The daemon has no 1d timeframe, so the daily decision is taken on ONE 4h close per
# day (the candle opening at `eval_hour` UTC). Everything the live pipeline does to an
# entry is replayed: the volume-confirm gate (volume_ratio >= volume_ratio_min x the
# session multiplier), ATR brackets resolved on the 4h CLOSE and filled at the close
# (SimulationExecution.check_exits), the 20x isolated liquidation distance, the
# max_hold timeout, fixed-fractional risk sizing, and the sim's costs (maker entry
# 2 bps, maker TP 2 bps, every other exit taker 4 + slippage 5). The group-exit rule
# ("no longer a leader / laggard at the daily close") is the indicator exit.
# ---------------------------------------------------------------------------
_H4 = 4 * _HOUR
_SESSION_VOL_MULT = {0: 1.2, 4: 1.2, 8: 1.0, 12: 1.0, 16: 0.9, 20: 0.9}  # by 4h candle open hour (§22)
_COST_IN, _COST_TP, _COST_MKT = 2.0, 2.0, 9.0


def _fut_ohlcv_4h(
    pair: str, months: list[tuple[int, int]]
) -> tuple[dict[int, tuple[float, float, float, float]], dict]:
    """({4h open ts: (high, low, close, volume)}, {UTC day: funding}) built from the cached 1h futures klines."""
    for fut in _fut_symbol(pair):
        bars: dict[int, list[float]] = {}
        funding: dict[int, float] = {}
        for y, m in months:
            tag = f"{y}-{m:02d}"
            kb = _download(
                f"{_ARCHIVE}/futures/um/monthly/klines/{fut}/1h/{fut}-1h-{tag}.zip",
                f"{_FUND_CACHE}/fut/{fut}-1h-{tag}.zip",
            )
            if kb:
                for r in sorted(_csv_rows(kb), key=lambda r: int(r[0])):
                    key = _ms(r[0]) // _H4 * _H4
                    hi, lo, cl, vol = float(r[2]), float(r[3]), float(r[4]), float(r[5])
                    b = bars.get(key)
                    if b is None:
                        bars[key] = [hi, lo, cl, vol]
                    else:
                        b[0], b[1], b[2], b[3] = max(b[0], hi), min(b[1], lo), cl, b[3] + vol
            fb = _download(
                f"{_ARCHIVE}/futures/um/monthly/fundingRate/{fut}/{fut}-fundingRate-{tag}.zip",
                f"{_FUND_CACHE}/fut/{fut}-fundingRate-{tag}.zip",
            )
            if fb:
                for r in _csv_rows(fb):
                    day = _ms(r[0]) // _DAY * _DAY
                    funding[day] = funding.get(day, 0.0) + float(r[2])
        if bars:
            return {k: (v[0], v[1], v[2], v[3]) for k, v in bars.items()}, funding
    return {}, {}


def _wilder_atr_frac(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """ATR(period, Wilder) as a fraction of the close — signal/indicators.compute_atr on a full series."""
    out = np.full(len(close), np.nan)
    atr = np.nan
    seed: list[float] = []
    for t in range(1, len(close)):
        if not (np.isfinite(high[t]) and np.isfinite(close[t - 1])):
            atr, seed = np.nan, []
            continue
        tr = max(high[t] - low[t], abs(high[t] - close[t - 1]), abs(low[t] - close[t - 1]))
        if np.isnan(atr):
            seed.append(tr)
            if len(seed) == period:
                atr = sum(seed) / period
        else:
            atr = (atr * (period - 1) + tr) / period
        if np.isfinite(atr):
            out[t] = atr / close[t]
    return out


def bot_trades(
    ts: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    lookback: int,
    frac: float,
    eval_hour: int,
    tp_atr: float,
    sl_atr: float,
    max_hold: int,
    max_loss_pct: float,
    vol_min: Optional[float],
    leverage: float = 20.0,
) -> list[tuple[int, int, int, int, float, float, float, str]]:
    """Replay one bot per pair. Returns (pair idx, entry bar, exit bar, side, notional/equity, gross bps, net bps, reason)."""
    T, N = close.shape
    is_eval = (ts // _HOUR) % 24 == eval_hour
    seen = np.cumsum(np.isfinite(close), axis=0)
    group = np.zeros((T, N), dtype=np.int8)
    for t in np.flatnonzero(is_eval):
        if t < lookback:
            continue
        past = close[t] / close[t - lookback] - 1.0
        past[seen[t] < lookback + 1] = np.nan
        rk = _rank_pct(past)
        group[t] = np.where(rk >= 1.0 - frac, 1, 0) - np.where(rk <= frac, 1, 0)
    vma = np.full((T, N), np.nan)
    csum = np.nancumsum(volume, axis=0)
    vma[19:] = (csum[19:] - np.vstack([np.zeros((1, N)), csum[:-20]])) / 20.0
    vratio = volume / vma
    gate = (
        np.ones(T) if vol_min is None else np.array([vol_min * _SESSION_VOL_MULT[int(h)] for h in (ts // _HOUR) % 24])
    )
    out = []
    for j in range(N):
        atr = _wilder_atr_frac(high[:, j], low[:, j], close[:, j])
        t = 0
        while t < T - 1:
            side = int(group[t, j])
            ok = side != 0 and np.isfinite(atr[t]) and (vol_min is None or vratio[t, j] >= gate[t])
            if not ok:
                t += 1
                continue
            entry, tp, sl = close[t, j], tp_atr * atr[t], sl_atr * atr[t]
            liq = 1.0 / leverage - 0.005  # isolated margin, mmr 0.5% (§17)
            stop = min(sl, liq)
            notional = min(leverage, max_loss_pct / stop)
            k, done = t, None
            while done is None and k < T - 1:
                k += 1
                if not np.isfinite(close[k, j]):
                    continue
                mv = side * (close[k, j] / entry - 1.0)
                if mv >= tp:
                    done = (mv * 1e4, _COST_TP, "take_profit")
                elif mv <= -stop:
                    done = (mv * 1e4, _COST_MKT, "stop_loss" if sl <= liq else "liquidated")
                elif k - t >= max_hold:
                    done = (mv * 1e4, _COST_MKT, "timeout")
                elif is_eval[k] and group[k, j] != side:
                    done = (mv * 1e4, _COST_MKT, "signal_exit")
            if done is None:  # still open at the end of the data: mark it to the last close
                last = k
                while last > t and not np.isfinite(close[last, j]):
                    last -= 1
                if last == t:
                    break
                k, done = last, (side * (close[last, j] / entry - 1.0) * 1e4, _COST_MKT, "open")
            out.append((j, t, k, side, notional, done[0], done[0] - _COST_IN - done[1], done[2]))
            t = k + 1  # the daemon returns after a close: the next entry is a later candle
    return out


def part_bots() -> None:
    months = [(2024, m) for m in range(10, 13)] + [(2025, m) for m in range(1, 13)] + [(2026, m) for m in range(1, 10)]
    bars, fund = {}, {}
    for pair in SCALP_PAIRS:
        base = pair.split("/")[0]
        bars[base], fund[base] = _fut_ohlcv_4h(pair, months)
    cols = [{b: {k: v[i] for k, v in s.items()} for b, s in bars.items()} for i in range(4)]
    ts, high, names = _matrix(cols[0], _H4)
    _, low, _ = _matrix(cols[1], _H4)
    _, close, _ = _matrix(cols[2], _H4)
    _, volume, _ = _matrix(cols[3], _H4)
    day = ts // _DAY * _DAY
    print(f"\nBOT FORM — one bot per pair on 4h candles, {len(names)} pairs, sim costs, dev funding 0")
    print(
        "  config | era | trades | win% | net bps/trade | avg hold d | exits tp/sl/sig/to % | "
        "return on cohort capital %/yr | + actual funding | t (monthly)"
    )

    def report(label: str, **kw) -> None:
        trades = bot_trades(ts, high, low, close, volume, **kw)
        for era, (a, b) in FUT_ERAS.items():
            sel = [x for x in trades if _ts(a) <= ts[x[1]] < _ts(b)]
            if len(sel) < 20:
                print(f"  {label:34s} {era} | {len(sel)} trades")
                continue
            net = np.array([x[6] for x in sel])
            hold = np.array([(x[2] - x[1]) / 6.0 for x in sel])
            why = [x[7] for x in sel]
            mix = "/".join(
                f"{100 * sum(w in g for w in why) / len(why):.0f}"
                for g in (("take_profit",), ("stop_loss", "liquidated"), ("signal_exit",), ("timeout",))
            )
            days = (_ts(b) - max(_ts(a), int(ts[0]))) / _DAY
            cap = np.array([x[4] * x[6] / 1e4 for x in sel])  # P&L as a fraction of ONE bot's bucket
            fpay = np.array(
                [
                    -x[3] * x[4] * sum(fund[names[x[0]]].get(int(d), 0.0) for d in np.unique(day[x[1] + 1 : x[2] + 1]))
                    for x in sel
                ]
            )
            month = np.array(
                [_dt.datetime.fromtimestamp(ts[x[2]] / 1000, _dt.timezone.utc).strftime("%Y-%m") for x in sel]
            )
            monthly = np.array([cap[month == m].sum() for m in sorted(set(month))])
            t_m = monthly.mean() / (monthly.std() / np.sqrt(len(monthly))) if monthly.std() > 0 else 0.0
            print(
                f"  {label:34s} {era} | {len(sel):5d} | {100 * (net > 0).mean():4.1f} | {net.mean():7.1f} | {hold.mean():5.1f} | "
                f"{mix:>11s} | {cap.sum() / len(names) / days * 36500:6.1f} | {(cap.sum() + fpay.sum()) / len(names) / days * 36500:6.1f} | {t_m:5.2f}"
            )

    base = dict(frac=1 / 3, eval_hour=12, tp_atr=3.0, sl_atr=2.0, max_hold=48, max_loss_pct=0.04, vol_min=1.1)
    report("L28d tp3/sl2 hold48 eval12", lookback=168, **base)
    report("L14d tp3/sl2 hold48 eval12", lookback=84, **base)
    report("L28d tp3/sl1.5", lookback=168, **{**base, "sl_atr": 1.5})
    print("  -- what each piece of fleet machinery costs (L28d) --")
    report("no volume gate", lookback=168, **{**base, "vol_min": None})
    report("no TP cap (tp 99)", lookback=168, **{**base, "tp_atr": 99.0})
    report("no gate, no TP, hold 400", lookback=168, **{**base, "vol_min": None, "tp_atr": 99.0, "max_hold": 400})
    print("  -- room the positions need: no ATR stop, liquidation only, notional 1x the bucket (L28d, hold 48) --")
    for lev in (20, 10, 5, 3, 2, 1):
        wide = {**base, "sl_atr": 99.0, "leverage": float(lev), "max_loss_pct": 1.0 / lev - 0.005}
        report(f"{lev}x isolated, tp3", lookback=168, **wide)
        report(f"{lev}x isolated, no tp", lookback=168, **{**wide, "tp_atr": 99.0})
    print("  -- consistency check: the bare portfolio rule replayed bot-by-bot (no gate / TP / stop / timeout) --")
    bare = dict(frac=1 / 3, tp_atr=99.0, sl_atr=99.0, max_hold=10**6, max_loss_pct=0.995, vol_min=None, leverage=1.0)
    for hour in (20, 12):
        report(f"bare rule, decision candle {hour:02d}", lookback=168, eval_hour=hour, **bare)
    report("bare rule 12 + volume gate", lookback=168, eval_hour=12, **{**bare, "vol_min": 1.1})
    report("bare rule 12 + hold 48", lookback=168, eval_hour=12, **{**bare, "max_hold": 48})
    report("bare rule 12 + tp 3 ATR", lookback=168, eval_hour=12, **{**bare, "tp_atr": 3.0})
    print("  -- decision candle (4h open hour UTC), L28d --")
    for hour in (0, 4, 8, 16, 20):
        report(f"eval{hour:02d}", lookback=168, **{**base, "eval_hour": hour})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--ladder", action="store_true", help="also print gross bps per unit traded for 1h / 4h / 1d rebalancing"
    )
    ap.add_argument("--skip-a", action="store_true", help="skip the 7-year daily part (needs api.binance.com)")
    ap.add_argument("--bots", action="store_true", help="only the per-bot 4h form the fleet would trade")
    args = ap.parse_args()
    if args.bots:
        part_bots()
        return
    if not args.skip_a:
        part_a()
    part_b(args.ladder)


if __name__ == "__main__":
    main()
