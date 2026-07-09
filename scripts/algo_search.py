#!/usr/bin/env python3
"""
Algorithm-search ACCELERATOR — rank many handwritten entry algorithms in one
offline walk-forward sweep, instead of one slow live A/B at a time.

Why this exists (CLAUDE.md §5/§30, and the no-edge history): the live lab learns
at wall-clock speed — 2 days of paper trading = 2 days of data, and it took ~3 days
to rank 3 entries. This script runs *dozens* of cheap, rule-based entry algorithms
across all pairs over months of real history in minutes, walk-forward (train 60% /
test 40%), with the SAME fee+slippage model the live runner applies — so its
leaderboard is directly comparable to the live dashboard and the grid/wave sweeps.
It is the search engine: add a new algorithm = add one function below, re-run.

Design (search vs production):
    - The entry algorithms live HERE, registered into the shared registry at import
      and judged purely on their own entry edge. To do that the script neutralises
      the secondary pipeline gates that are orthogonal to entry quality:
        * regime allowlist  -> permit-all (regime becomes a later refinement)
        * trend filter       -> every experimental algo self-directs (counter-trend set)
        * volume confirm     -> pass-through (volume is a separate dimension to add later)
      It KEEPS the legitimate economic gates: QUIET regime (no tradeable move) and the
      risk manager (R/R >= 1.2, liquidation distance, fee-viability Rule 4) — clearing
      the ~0.18% round-trip cost is the whole point, so that gate stays on.
    - Production patterns.py is untouched. Only a §30 SURVIVOR gets promoted there
      later, with regime wiring + a unit test. Dead ideas never touch production.

Entry algorithms cover the archetype space (all O(small-window), no ML, no big math):
    mean-reversion/fade : rsi_revert_* · bb_fade · donch_fade_* · spike_fade · wick_revert
    momentum/breakout   : bb_break · donch_break_* · ema_cross · streak_go_* · body_go · rsi_cross_50
    volatility-compress : compress_break · compress_fade   (the one signal with a live pulse: bb_width)

Run (one-off container, repo mounted, no DB needed):
  docker run --rm --entrypoint python --env-file .env -e EXCHANGE=gate \
    -v /root/Kestrel:/app -w /app kestrel-kestrel:latest -u \
    scripts/algo_search.py --days 45
  # smoke: scripts/algo_search.py --days 30 --pairs BTC/USDT,ETH/USDT
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import statistics
import sys
import time
from typing import Callable, Optional, Sequence

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import backtest_grid as bg  # build_candles_for / _enrich_trades / _ext_metrics / _rr / _COST_PCT
import backtest_real as bt  # robust multi-exchange fetch_ohlcv
import build_lab as lab  # PAIRS — single source of truth
from dotenv import load_dotenv

import src.signal.detector as detector
from src.backtest.runner import run_backtest
from src.config import (
    AppConfig,
    Candle,
    Direction,
    Params,
    PatternResult,
    PatternType,
    VolumeResult,
    load_params,
)
from src.signal.patterns import registry

# ---------------------------------------------------------------------------
# Entry-algorithm library — each registered into the shared registry below.
# A PatternResult carries its real identity in details["variant"]; PatternType is
# only a coarse bucket (5 values), exactly as the production wave_* patterns do.
# Confidence is fixed at 0.80 so the session-adjusted min-confidence gate never
# differentially filters one algorithm vs another (entry-edge search, not tuning).
# ---------------------------------------------------------------------------

EntryFn = Callable[[Sequence[Candle], Params], Optional[PatternResult]]
_ALGOS: list[str] = []
_CONF = 0.80


def _algo(name: str, bucket: PatternType) -> Callable[[EntryFn], EntryFn]:
    def wrap(fn: EntryFn) -> EntryFn:
        def wrapped(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
            d = fn(candles, params)
            if d is None:
                return None
            return PatternResult(pattern=bucket, direction=d, confidence=_CONF, details={"variant": name})

        registry[name] = wrapped
        _ALGOS.append(name)
        return wrapped

    return wrap


def _dir(c: Candle) -> Optional[Direction]:
    if c.close > c.open:
        return Direction.LONG
    if c.close < c.open:
        return Direction.SHORT
    return None


def _body(c: Candle) -> float:
    return c.body_size if c.body_size is not None else abs(c.close - c.open)


def _opp(d: Optional[Direction]) -> Optional[Direction]:
    if d is Direction.LONG:
        return Direction.SHORT
    if d is Direction.SHORT:
        return Direction.LONG
    return None


# --- mean-reversion / fade ---------------------------------------------------
def _make_rsi_revert(lo: float, hi: float) -> None:
    @_algo(f"rsi_revert_{int(lo)}_{int(hi)}", PatternType.ANOMALY_FADE)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        c = C[-1]
        if c.rsi14 is None:
            return None
        if c.rsi14 < lo:
            return Direction.LONG
        if c.rsi14 > hi:
            return Direction.SHORT
        return None


for _lo, _hi in [(25.0, 75.0), (20.0, 80.0), (30.0, 70.0)]:
    _make_rsi_revert(_lo, _hi)


# --- Connors RSI-2 mean-reversion (research 2026-06-16) -----------------------
# The web research converged on RSI-2 (Larry Connors) as the highest-win-rate
# mean-reversion archetype (documented 62-68% on daily BTC) — the family Kestrel's
# >55% win bar actually favours. It is NOT the rsi_revert_* above: those fade a
# slow RSI-14; this uses a 2-period RSI (very responsive) AND an SMA-200 trend
# filter so it only buys dips WITH the higher trend (buy oversold in an uptrend,
# sell overbought in a downtrend). Candles store only rsi14, so RSI(2) + SMA(100)
# are computed inline from closes here. NOTE: the detector/backtest only ever pass
# the last 120 candles (runner.py window = candles[i-119:i+1]), so the trend filter
# uses SMA(100) (fits the window + stays deployable) — Connors' SMA(200) cannot be
# seen and would silently never fire.
def _rsi(closes: Sequence[float], period: int) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(-period, 0):
        ch = closes[i] - closes[i - 1]
        gains += ch if ch > 0 else 0.0
        losses += -ch if ch < 0 else 0.0
    if losses == 0.0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


def _sma(closes: Sequence[float], n: int) -> Optional[float]:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _make_rsi2(tag: str, lo: float, hi: float, use_trend: bool) -> None:
    @_algo(tag, PatternType.ANOMALY_FADE)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        closes = [c.close for c in C]
        r = _rsi(closes, 2)
        if r is None:
            return None
        if use_trend:
            ma = _sma(closes, 100)
            if ma is None:
                return None
            if closes[-1] > ma and r < lo:
                return Direction.LONG  # oversold dip inside an uptrend
            if closes[-1] < ma and r > hi:
                return Direction.SHORT  # overbought pop inside a downtrend
            return None
        if r < lo:
            return Direction.LONG
        if r > hi:
            return Direction.SHORT
        return None


_make_rsi2("rsi2_ct", 10.0, 90.0, True)  # canonical Connors RSI-2 (trend-aligned)
_make_rsi2("rsi2_ct5", 5.0, 95.0, True)  # stricter (fewer, higher-quality dips)
_make_rsi2("rsi2_raw", 10.0, 90.0, False)  # no trend filter — isolates the SMA-200 contribution


# --- MACD (owner directive 2026-06-21: "permitted to use indexes like macd, rsi") -----
# MACD has never been tested in Kestrel. Candles store ema9/ema21 but not ema12/26 or the
# MACD/signal lines, so compute them inline from closes (same inline approach as RSI-2).
# The detector/backtest pass only the last 120 candles; MACD(12,26,9) warms up in ~35, so
# it fits the window comfortably.
def _ema_series(values: Sequence[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    out = [ema]
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
        out.append(ema)
    return out


def _macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Optional[tuple[float, float, float, float]]:
    """Return (macd_last, macd_prev, signal_last, signal_prev), or None if too short."""
    if len(closes) < slow + signal + 1:
        return None
    ef = _ema_series(closes, fast)
    es = _ema_series(closes, slow)
    off = len(ef) - len(es)  # fast warms up earlier; align tails before subtracting
    macd_line = [ef[i + off] - es[i] for i in range(len(es))]
    if len(macd_line) < signal + 1:
        return None
    sig = _ema_series(macd_line, signal)
    if len(sig) < 2:
        return None
    return (macd_line[-1], macd_line[-2], sig[-1], sig[-2])


@_algo("macd_cross", PatternType.MOMENTUM_CONTINUATION)
def _macd_cross(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    m = _macd([c.close for c in C])
    if m is None:
        return None
    ml, mp, sl, sp = m
    if mp <= sp and ml > sl:
        return Direction.LONG  # MACD line crosses up through its signal line
    if mp >= sp and ml < sl:
        return Direction.SHORT
    return None


@_algo("macd_cross_ct", PatternType.MOMENTUM_CONTINUATION)
def _macd_cross_ct(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # trend-aligned: only take the signal cross on the correct side of zero (MACD>0=uptrend)
    m = _macd([c.close for c in C])
    if m is None:
        return None
    ml, mp, sl, sp = m
    if ml > 0 and mp <= sp and ml > sl:
        return Direction.LONG
    if ml < 0 and mp >= sp and ml < sl:
        return Direction.SHORT
    return None


@_algo("macd_zero", PatternType.MOMENTUM_CONTINUATION)
def _macd_zero(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # MACD line crossing the zero line = momentum-regime flip
    m = _macd([c.close for c in C])
    if m is None:
        return None
    ml, mp, _sl, _sp = m
    if mp <= 0.0 < ml:
        return Direction.LONG
    if mp >= 0.0 > ml:
        return Direction.SHORT
    return None


@_algo("macd_hist", PatternType.MOMENTUM_CONTINUATION)
def _macd_hist(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # histogram momentum: hist = macd - signal; enter when it is positive & expanding
    m = _macd([c.close for c in C])
    if m is None:
        return None
    ml, mp, sl, sp = m
    hist, hist_prev = ml - sl, mp - sp
    if hist > 0 and hist > hist_prev:
        return Direction.LONG
    if hist < 0 and hist < hist_prev:
        return Direction.SHORT
    return None


# --- moving-average crosses (owner directive 2026-06-21: "use moving average, any period") ---
# Classic fast/slow MA crossover (golden/death cross). Computed inline from closes so any
# period/method (SMA or EMA) works within the 120-candle window (slow must be < ~115).
def _make_ma_cross(fast: int, slow: int, kind: str) -> None:
    @_algo(f"{kind}_cross_{fast}_{slow}", PatternType.MOMENTUM_CONTINUATION)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        closes = [c.close for c in C]
        if len(closes) < slow + 1:
            return None
        if kind == "sma":
            f_now, f_prev = _sma(closes, fast), _sma(closes[:-1], fast)
            s_now, s_prev = _sma(closes, slow), _sma(closes[:-1], slow)
        else:  # ema
            fe, se = _ema_series(closes, fast), _ema_series(closes, slow)
            if len(fe) < 2 or len(se) < 2:
                return None
            f_now, f_prev, s_now, s_prev = fe[-1], fe[-2], se[-1], se[-2]
        if None in (f_now, f_prev, s_now, s_prev):
            return None
        if f_prev <= s_prev and f_now > s_now:
            return Direction.LONG  # fast MA crosses up through slow MA (golden cross)
        if f_prev >= s_prev and f_now < s_now:
            return Direction.SHORT
        return None


for _maf, _mas in [(9, 21), (10, 30), (20, 50), (20, 100), (50, 100)]:
    _make_ma_cross(_maf, _mas, "sma")
for _maf, _mas in [(9, 21), (12, 26), (20, 50)]:
    _make_ma_cross(_maf, _mas, "ema")


# --- Stochastic oscillator (owner directive 2026-06-21: "stochastic, etc.") -----------
# Never tested in Kestrel. %K = 100*(close-LL_n)/(HH_n-LL_n); %D = SMA(%K, smooth).
# Computed inline from candle high/low/close inside the 120-candle window (n+smooth ≪ 120).
def _stoch(C: Sequence[Candle], n: int = 14, d: int = 3) -> Optional[tuple[float, float, float, float]]:
    """Return (k_last, k_prev, d_last, d_prev) of the %K/%D stochastic, or None if too short."""
    L = len(C)
    if L < n + d:
        return None
    highs = [c.high for c in C]
    lows = [c.low for c in C]
    closes = [c.close for c in C]
    ks: list[float] = []
    for t in range(L - (d + 1), L):  # %K for the last d+1 bars
        hh = max(highs[t - n + 1 : t + 1])
        ll = min(lows[t - n + 1 : t + 1])
        ks.append(50.0 if hh == ll else 100.0 * (closes[t] - ll) / (hh - ll))
    d_last = sum(ks[-d:]) / d
    d_prev = sum(ks[-d - 1 : -1]) / d
    return (ks[-1], ks[-2], d_last, d_prev)


@_algo("stoch_revert", PatternType.ANOMALY_FADE)
def _stoch_revert(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # %K crosses up through %D while OVERSOLD → long; down while OVERBOUGHT → short
    s = _stoch(C)
    if s is None:
        return None
    kl, kp, dl, dp = s
    if kl < 25.0 and kp <= dp and kl > dl:
        return Direction.LONG
    if kl > 75.0 and kp >= dp and kl < dl:
        return Direction.SHORT
    return None


@_algo("stoch_ct", PatternType.MOMENTUM_CONTINUATION)
def _stoch_ct(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # trend-aligned: %K crosses %D (not yet extreme) on the correct side of SMA-100
    s = _stoch(C)
    if s is None:
        return None
    kl, kp, dl, dp = s
    ma = _sma([c.close for c in C], 100)
    if ma is None:
        return None
    if C[-1].close > ma and kp <= dp and kl > dl and kl < 80.0:
        return Direction.LONG
    if C[-1].close < ma and kp >= dp and kl < dl and kl > 20.0:
        return Direction.SHORT
    return None


# --- indicator CONFLUENCE (iter-18 flagged next step: "test RSI+MACD confluences") ----
# Stack two orthogonal momentum reads: the MACD signal cross (the iter-18 lead) confirmed
# by RSI-14 on the same side of 50. The hypothesis is that requiring agreement filters the
# false MACD crosses that sink macd_cross's win rate, lifting expectancy without new data.
@_algo("macd_rsi", PatternType.MOMENTUM_CONTINUATION)
def _macd_rsi(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    m = _macd([c.close for c in C])
    if m is None or C[-1].rsi14 is None:
        return None
    ml, mp, sl, sp = m
    r = C[-1].rsi14
    if mp <= sp and ml > sl and r > 50.0:
        return Direction.LONG
    if mp >= sp and ml < sl and r < 50.0:
        return Direction.SHORT
    return None


@_algo("macd_rsi_ct", PatternType.MOMENTUM_CONTINUATION)
def _macd_rsi_ct(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # macd_cross_ct (zero-line trend-aligned) + RSI-14 confirmation — the strictest confluence
    m = _macd([c.close for c in C])
    if m is None or C[-1].rsi14 is None:
        return None
    ml, mp, sl, sp = m
    r = C[-1].rsi14
    if ml > 0 and mp <= sp and ml > sl and r > 50.0:
        return Direction.LONG
    if ml < 0 and mp >= sp and ml < sl and r < 50.0:
        return Direction.SHORT
    return None


# --- ADX trend-strength confluence on the MACD family (iter-23) -----------------------
# Does requiring a real trend (ADX above a floor) on top of the MACD signal cross (+ optional
# RSI-50 confirm) improve the deployed leads macd_cross/macd_rsi? The hypothesis: MACD crosses
# in chop (low ADX) are the false signals dragging win<50%; an ADX gate should filter them.
# Candles store adx (the mom_adx algo uses it), so gate on it directly — no inline compute.
def _make_macd_adx(tag: str, adx_min: float, use_rsi: bool) -> None:
    @_algo(tag, PatternType.MOMENTUM_CONTINUATION)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        c = C[-1]
        if c.adx is None or c.adx < adx_min or (use_rsi and c.rsi14 is None):
            return None
        m = _macd([cc.close for cc in C])
        if m is None:
            return None
        ml, mp, sl, sp = m
        if mp <= sp and ml > sl and (not use_rsi or c.rsi14 > 50.0):
            return Direction.LONG
        if mp >= sp and ml < sl and (not use_rsi or c.rsi14 < 50.0):
            return Direction.SHORT
        return None


_make_macd_adx("macd_adx20", 20.0, False)
_make_macd_adx("macd_adx25", 25.0, False)
_make_macd_adx("macd_rsi_adx20", 20.0, True)
_make_macd_adx("macd_rsi_adx25", 25.0, True)


# --- CCI + Supertrend (iter-31 active search — genuinely-new families, never tested) --
def _cci_pair(C: Sequence[Candle], n: int = 20) -> Optional[tuple[float, float]]:
    """(cci_now, cci_prev) of the Commodity Channel Index, or None if too short."""
    if len(C) < n + 1:
        return None
    tp = [(c.high + c.low + c.close) / 3.0 for c in C]

    def at(idx: int) -> float:
        w = tp[idx - n + 1 : idx + 1]
        sma = sum(w) / n
        md = sum(abs(x - sma) for x in w) / n
        return 0.0 if md == 0.0 else (tp[idx] - sma) / (0.015 * md)

    return (at(len(C) - 1), at(len(C) - 2))


@_algo("cci_revert", PatternType.ANOMALY_FADE)
def _cci_revert(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # mean-revert: CCI snaps back across ±100 out of oversold/overbought
    r = _cci_pair(C)
    if r is None:
        return None
    now, prev = r
    if prev <= -100.0 and now > -100.0:
        return Direction.LONG
    if prev >= 100.0 and now < 100.0:
        return Direction.SHORT
    return None


@_algo("cci_mom", PatternType.MOMENTUM_CONTINUATION)
def _cci_mom(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # momentum: CCI breaks OUT through ±100 into a strong move
    r = _cci_pair(C)
    if r is None:
        return None
    now, prev = r
    if prev <= 100.0 and now > 100.0:
        return Direction.LONG
    if prev >= -100.0 and now < -100.0:
        return Direction.SHORT
    return None


def _supertrend_dir(C: Sequence[Candle], mult: float = 3.0) -> Optional[tuple[int, int]]:
    """(dir_now, dir_prev) of Supertrend: +1 uptrend / -1 downtrend. Canonical
    carry-forward final bands over the window (uses the stored atr14)."""
    if len(C) < 15:
        return None
    f_up: Optional[float] = None
    f_lo: Optional[float] = None
    direction = 1
    dirs: list[int] = []
    prev_close = C[0].close
    for c in C:
        atr = c.atr14
        if atr is None:
            dirs.append(direction)
            prev_close = c.close
            continue
        hl2 = (c.high + c.low) / 2.0
        b_up = hl2 + mult * atr
        b_lo = hl2 - mult * atr
        f_up = b_up if (f_up is None or b_up < f_up or prev_close > f_up) else f_up
        f_lo = b_lo if (f_lo is None or b_lo > f_lo or prev_close < f_lo) else f_lo
        if direction == 1 and c.close < f_lo:
            direction = -1
        elif direction == -1 and c.close > f_up:
            direction = 1
        dirs.append(direction)
        prev_close = c.close
    if len(dirs) < 2:
        return None
    return (dirs[-1], dirs[-2])


@_algo("supertrend", PatternType.MOMENTUM_CONTINUATION)
def _supertrend(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # enter on a Supertrend direction flip (ATR trend-follower)
    r = _supertrend_dir(C)
    if r is None:
        return None
    now, prev = r
    if prev == -1 and now == 1:
        return Direction.LONG
    if prev == 1 and now == -1:
        return Direction.SHORT
    return None


@_algo("bb_fade", PatternType.ANOMALY_FADE)
def _bb_fade(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    if c.bb_lower is None or c.bb_upper is None:
        return None
    if c.close < c.bb_lower:
        return Direction.LONG
    if c.close > c.bb_upper:
        return Direction.SHORT
    return None


def _make_donch_fade(n: int) -> None:
    @_algo(f"donch_fade_{n}", PatternType.ANOMALY_FADE)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        if len(C) < n + 1:
            return None
        c = C[-1]
        prior = C[-(n + 1) : -1]
        if c.high >= max(x.high for x in prior):
            return Direction.SHORT  # fade a new high
        if c.low <= min(x.low for x in prior):
            return Direction.LONG  # fade a new low
        return None


for _n in (10, 20):
    _make_donch_fade(_n)


@_algo("spike_fade", PatternType.ANOMALY_FADE)
def _spike_fade(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    if c.atr14 is None or c.atr14 <= 0.0:
        return None
    if abs(c.close - c.open) > 1.5 * c.atr14:
        return _opp(_dir(c))  # fade an outsized candle
    return None


@_algo("wick_revert", PatternType.WICK_REJECTION)
def _wick_revert(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    body = _body(c)
    if body <= 0.0:
        return None
    lw = c.lower_wick if c.lower_wick is not None else min(c.open, c.close) - c.low
    uw = c.upper_wick if c.upper_wick is not None else c.high - max(c.open, c.close)
    if lw > 1.5 * body and lw > uw:
        return Direction.LONG
    if uw > 1.5 * body and uw > lw:
        return Direction.SHORT
    return None


# --- momentum / breakout -----------------------------------------------------
@_algo("bb_break", PatternType.COMPRESSION_BREAKOUT)
def _bb_break(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    if c.bb_lower is None or c.bb_upper is None:
        return None
    if c.close > c.bb_upper:
        return Direction.LONG
    if c.close < c.bb_lower:
        return Direction.SHORT
    return None


def _make_donch_break(n: int) -> None:
    @_algo(f"donch_break_{n}", PatternType.MOMENTUM_CONTINUATION)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        if len(C) < n + 1:
            return None
        c = C[-1]
        prior = C[-(n + 1) : -1]
        if c.close >= max(x.high for x in prior):
            return Direction.LONG
        if c.close <= min(x.low for x in prior):
            return Direction.SHORT
        return None


for _n in (10, 20):
    _make_donch_break(_n)


@_algo("ema_cross", PatternType.MOMENTUM_CONTINUATION)
def _ema_cross(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 2:
        return None
    a, c = C[-2], C[-1]
    if None in (a.ema9, a.ema21, c.ema9, c.ema21):
        return None
    if a.ema9 <= a.ema21 and c.ema9 > c.ema21:
        return Direction.LONG
    if a.ema9 >= a.ema21 and c.ema9 < c.ema21:
        return Direction.SHORT
    return None


def _make_streak_go(n: int) -> None:
    @_algo(f"streak_go_{n}", PatternType.MOMENTUM_CONTINUATION)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        if len(C) < n:
            return None
        dirs = [_dir(x) for x in C[-n:]]
        if None in dirs or len(set(dirs)) != 1:
            return None
        return dirs[0]


for _n in (2, 3):
    _make_streak_go(_n)


@_algo("body_go", PatternType.MOMENTUM_CONTINUATION)
def _body_go(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    br = c.body_ratio if c.body_ratio is not None else 0.0
    if br > 0.6:
        return _dir(c)
    return None


@_algo("rsi_cross_50", PatternType.MOMENTUM_CONTINUATION)
def _rsi_cross_50(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 2:
        return None
    a, c = C[-2], C[-1]
    if a.rsi14 is None or c.rsi14 is None:
        return None
    if a.rsi14 <= 50.0 < c.rsi14:
        return Direction.LONG
    if a.rsi14 >= 50.0 > c.rsi14:
        return Direction.SHORT
    return None


# --- volatility compression (the one signal with a live pulse: bb_width) ------
def _compressed(C: Sequence[Candle]) -> bool:
    """bb_width in the bottom of its recent range (cheap squeeze proxy)."""
    if len(C) < 21:
        return False
    widths = [x.bb_width for x in C[-21:-1] if x.bb_width is not None]
    cur = C[-1].bb_width
    if cur is None or len(widths) < 10:
        return False
    return cur < statistics.median(widths) * 0.7


@_algo("compress_break", PatternType.COMPRESSION_BREAKOUT)
def _compress_break(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if not _compressed(C):
        return None
    return _dir(C[-1])  # enter the breakout direction


@_algo("compress_fade", PatternType.ANOMALY_FADE)
def _compress_fade(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if not _compressed(C):
        return None
    return _opp(_dir(C[-1]))  # fade the first move out of the squeeze


# --- confluence (multi-condition AND) ----------------------------------------
# The only live-positive thread was 4h momentum (sub-§30, SOL-concentrated). These
# require 2-3 independent conditions to AGREE — the handwritten alternative to ML
# feature-combination — to filter momentum down to higher-quality setups. All use
# only stored Candle indicators (cheap). Volume is read directly here (the pipeline
# volume gate is bypassed), so these explicitly test whether volume helps.
def _streak_dir(C: Sequence[Candle], n: int) -> Optional[Direction]:
    if len(C) < n:
        return None
    ds = [_dir(x) for x in C[-n:]]
    if None in ds or len(set(ds)) != 1:
        return None
    return ds[0]


@_algo("mom_adx", PatternType.MOMENTUM_CONTINUATION)
def _mom_adx(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    if c.adx is None or c.adx <= 25.0:  # 3-streak only inside a strong trend
        return None
    return _streak_dir(C, 3)


@_algo("mom_align", PatternType.MOMENTUM_CONTINUATION)
def _mom_align(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    if None in (c.ema9, c.ema21, c.adx) or c.adx <= 22.0:
        return None
    br = c.body_ratio if c.body_ratio is not None else 0.0
    d = _dir(c)
    if d is None or br < 0.5:
        return None
    trend = Direction.LONG if c.ema9 > c.ema21 else (Direction.SHORT if c.ema9 < c.ema21 else None)
    return d if d is trend else None  # conviction candle aligned with EMA trend + ADX


@_algo("breakout_vol", PatternType.MOMENTUM_CONTINUATION)
def _breakout_vol(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 21:
        return None
    c = C[-1]
    vr = c.volume_ratio if c.volume_ratio is not None else 0.0
    if vr < 1.5:  # breakout must carry real participation
        return None
    prior = C[-21:-1]
    if c.close >= max(x.high for x in prior):
        return Direction.LONG
    if c.close <= min(x.low for x in prior):
        return Direction.SHORT
    return None


@_algo("pullback_trend", PatternType.MOMENTUM_CONTINUATION)
def _pullback_trend(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 3:
        return None
    a, c = C[-2], C[-1]
    if None in (c.ema9, c.ema21, c.rsi14):
        return None
    if c.ema9 > c.ema21 and _dir(a) is Direction.SHORT and _dir(c) is Direction.LONG and 40.0 <= c.rsi14 <= 60.0:
        return Direction.LONG  # buy the pullback in an uptrend, RSI not extended
    if c.ema9 < c.ema21 and _dir(a) is Direction.LONG and _dir(c) is Direction.SHORT and 40.0 <= c.rsi14 <= 60.0:
        return Direction.SHORT
    return None


@_algo("mom_volexp", PatternType.MOMENTUM_CONTINUATION)
def _mom_volexp(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 7:
        return None
    c, past = C[-1], C[-7]
    if c.atr14 is None or past.atr14 is None or past.atr14 <= 0.0 or c.atr14 <= past.atr14:
        return None  # ATR rising = volatility expanding
    return _streak_dir(C, 3)


@_algo("triple_mom", PatternType.MOMENTUM_CONTINUATION)
def _triple_mom(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 7:
        return None
    c, past = C[-1], C[-7]
    if c.adx is None or c.adx <= 25.0:
        return None
    if c.atr14 is None or past.atr14 is None or c.atr14 <= past.atr14:
        return None
    return _streak_dir(C, 3)  # strictest: streak + strong ADX + expanding ATR


@_algo("compress_vol_break", PatternType.COMPRESSION_BREAKOUT)
def _compress_vol_break(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if not _compressed(C):
        return None
    c = C[-1]
    vr = c.volume_ratio if c.volume_ratio is not None else 0.0
    if vr < 1.3:
        return None
    return _dir(c)  # squeeze breakout confirmed by volume


# --- VWAP (iter-41 active search — the volume-weighted price anchor, never tested) ----
# Every algo above is PRICE-ONLY (MA/MACD/RSI/CCI/stoch/BB/ADX/supertrend). VWAP weights
# price by where volume ACTUALLY transacted — the one institutional reference level Kestrel
# has never used. Rolling VWAP over the last n candles (a session-reset proxy; n+1 << the
# 120-candle entry window) = sum(typical*vol)/sum(vol), typical=(h+l+c)/3.
def _vwap_pair(C: Sequence[Candle], n: int = 20) -> Optional[tuple[float, float]]:
    """(vwap_now over last n, vwap_prev over the n ending one bar back), or None if short."""
    if len(C) < n + 1:
        return None

    def at(end: int) -> Optional[float]:
        w = C[end - n + 1 : end + 1]
        vol = sum(c.volume for c in w)
        if vol <= 0.0:
            return None
        return sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in w) / vol

    now, prev = at(len(C) - 1), at(len(C) - 2)
    if now is None or prev is None:
        return None
    return (now, prev)


@_algo("vwap_revert", PatternType.ANOMALY_FADE)
def _vwap_revert(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # mean-revert: price stretched > 1 ATR from VWAP snaps back toward it
    r = _vwap_pair(C)
    if r is None:
        return None
    vwap_now, _ = r
    c = C[-1]
    if c.atr14 is None or c.atr14 <= 0.0:
        return None
    dist = c.close - vwap_now
    if dist < -c.atr14:
        return Direction.LONG  # far below VWAP -> fade up
    if dist > c.atr14:
        return Direction.SHORT  # far above VWAP -> fade down
    return None


@_algo("vwap_mom", PatternType.MOMENTUM_CONTINUATION)
def _vwap_mom(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    # momentum: close reclaims a RISING VWAP (or loses a FALLING VWAP) = trend with the anchor
    r = _vwap_pair(C)
    if r is None:
        return None
    vwap_now, vwap_prev = r
    prev_c, now_c = C[-2], C[-1]
    if prev_c.close <= vwap_prev and now_c.close > vwap_now and vwap_now > vwap_prev:
        return Direction.LONG
    if prev_c.close >= vwap_prev and now_c.close < vwap_now and vwap_now < vwap_prev:
        return Direction.SHORT
    return None


# --- Ensemble / voting confluence (iter 52) — never tested: every prior filter (ADX,
# volatility, HTF-trend) gated ONE lead against a regime or a different timeframe. This
# gates the SAME-timeframe leads against EACH OTHER — only fire when >=K of the 4
# deployed 1h leads agree on direction at the same candle. A structurally different
# confluence family (cross-signal, not cross-regime/cross-TF) from everything tried so
# far. Registered as its own algo so it flows through the identical run_backtest/risk/
# exit pipeline as every other entry — no separate simulation logic needed.
_ENSEMBLE_MEMBERS = ("cci_mom", "macd_cross", "macd_rsi", "sma_cross_9_21")


def _make_ensemble(min_agree: int) -> None:
    @_algo(f"ensemble_{min_agree}of{len(_ENSEMBLE_MEMBERS)}", PatternType.MOMENTUM_CONTINUATION)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        votes: list[Direction] = []
        for member in _ENSEMBLE_MEMBERS:
            fn = registry.get(member)
            if fn is None:
                continue
            r = fn(C, p)
            if r is not None:
                votes.append(r.direction)
        longs = votes.count(Direction.LONG)
        shorts = votes.count(Direction.SHORT)
        if longs >= min_agree and longs > shorts:
            return Direction.LONG
        if shorts >= min_agree and shorts > longs:
            return Direction.SHORT
        return None


for _k in (2, 3):
    _make_ensemble(_k)


# ---------------------------------------------------------------------------
# Neutralise secondary gates so each algo is judged on its own entry edge.
# (Module-global rebinds — detector resolves these names at call time.)
# ---------------------------------------------------------------------------
def _install_search_gates(regime_filter: Optional[str] = None) -> None:
    if regime_filter:
        want = regime_filter.lower()
        detector.regime_permits_pattern = lambda regime, p: regime.name.lower() == want  # one regime only
    else:
        detector.regime_permits_pattern = lambda regime, p: True  # permit-all
    detector.SELF_DIRECTING_PATTERNS = frozenset(_ALGOS)  # every algo self-directs

    def _pass_volume(candle: Candle, params: Params, mult: float) -> VolumeResult:
        return VolumeResult(
            volume_ratio=candle.volume_ratio if candle.volume_ratio is not None else 1.0,
            volume_ma20=candle.volume_ma20 if candle.volume_ma20 is not None else 0.0,
        )

    detector._volume_confirm = _pass_volume


# Exit profiles (ATR-mode). Entry style interacts with exit, so bracket tight/wide.
# Both keep planned R/R = tp/sl >= 1.2 (risk Rule 3).
EXITS = {
    # "scalp": fast minutes-window exit (added 2026-06-16, user wants a hyper-speed
    # scalper). Smallest viable bracket: tp/sl = 0.8/0.6 = 1.33 ≥ Rule 3's 1.2, hold 2.
    # Only has a prayer of clearing cost under MAKER fees (taker minutes-scalping is
    # proven dead by the cost wall) — run it with --fees maker.
    "scalp": {"tp_atr_multiplier": 0.8, "sl_atr_multiplier": 0.6, "max_hold_candles": 2},
    "tight": {"tp_atr_multiplier": 1.4, "sl_atr_multiplier": 1.0, "max_hold_candles": 4},
    "wide": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.5, "max_hold_candles": 8},
    # Added 2026-06-14 to chase the §30 R/R gap on the confluence-momentum family
    # (mom_adx was net-+ on 10/10 pairs but realized R/R < 1.2 on several). All keep
    # planned R/R = tp/sl ≥ 1.2 (risk Rule 3). "trail" rides winners via the
    # trailing-close exit (backtest/runner.py honours trailing_enabled).
    "medium": {"tp_atr_multiplier": 2.0, "sl_atr_multiplier": 1.0, "max_hold_candles": 6},
    "runner": {"tp_atr_multiplier": 2.5, "sl_atr_multiplier": 1.2, "max_hold_candles": 6},
    "trail": {
        "tp_atr_multiplier": 2.0,
        "sl_atr_multiplier": 1.0,
        "max_hold_candles": 12,
        "trailing_enabled": True,
        "trail_activation_r": 1.0,
        "trail_distance_r": 1.0,
    },
    # HiWin inverted-geometry brackets (docs/13-points-framework.md §5 S1, 2026-07-09).
    # DELIBERATELY g = tp/sl < 1.2: win rate ~ 1/(1+g), so these are the only
    # geometries that can reach the owner's 70% win-rate target (g>=1.2 caps it
    # ~45-50%). Risk Rule 3 rejects g<1.2, so these REQUIRE --points, which bypasses
    # the R/R floor at runtime for this research process only (frozen risk file
    # untouched — same precedent as the fee patch above). Scored on GROSS POINTS.
    "hiwin50": {"tp_atr_multiplier": 0.6, "sl_atr_multiplier": 1.2, "max_hold_candles": 4},
    "hiwin43": {"tp_atr_multiplier": 0.6, "sl_atr_multiplier": 1.4, "max_hold_candles": 4},
    "hiwin33": {"tp_atr_multiplier": 0.5, "sl_atr_multiplier": 1.5, "max_hold_candles": 6},
    "scratch": {"tp_atr_multiplier": 0.5, "sl_atr_multiplier": 2.0, "max_hold_candles": 3},
}

# Forex / metals universe for --forex mode (yfinance symbols). Majors + crosses +
# gold/oil — the instruments BingX's TradFi side covers but ccxt doesn't expose, so
# we research them on free Yahoo data before building any live forex integration.
FOREX_PAIRS = [
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "AUDUSD=X",
    "USDCAD=X",
    "USDCHF=X",
    "NZDUSD=X",
    "EURJPY=X",
    "GBPJPY=X",
    "GC=F",
]


def _fetch_forex(symbol: str, tf: str, days: int) -> tuple[str, list]:
    """Fetch forex/metal OHLCV via yfinance → rows [ts_ms, o, h, l, c, v].

    Yahoo caps intraday history (5m≈60d, 1h≈730d) and has no native 4h, so we
    fetch the largest available base interval and resample up. yfinance is a
    research-only dep (NOT in the image) — imported lazily, pip-installed in the
    one-off container that runs --forex.
    """
    import pandas as pd
    import yfinance as yf

    if tf == "5m":
        base, period = "5m", f"{min(days, 59)}d"
    elif tf in ("1h", "4h"):
        base, period = "1h", f"{min(days, 729)}d"
    else:  # 1d or higher
        base, period = "1d", f"{days}d"

    df = yf.download(symbol, period=period, interval=base, progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        raise RuntimeError("yfinance returned no rows")
    if getattr(df.columns, "nlevels", 1) > 1:  # single-ticker MultiIndex → flatten
        df.columns = df.columns.get_level_values(0)

    if tf == "4h":  # resample 1h → 4h
        df = pd.DataFrame(
            {
                "Open": df["Open"].resample("4h").first(),
                "High": df["High"].resample("4h").max(),
                "Low": df["Low"].resample("4h").min(),
                "Close": df["Close"].resample("4h").last(),
                "Volume": df["Volume"].resample("4h").sum(),
            }
        ).dropna()

    rows = []
    for ts, r in df.iterrows():
        rows.append(
            [
                int(ts.timestamp() * 1000),
                float(r["Open"]),
                float(r["High"]),
                float(r["Low"]),
                float(r["Close"]),
                float(r["Volume"] or 0.0),
            ]
        )
    return ("yfinance", rows)


# Fee-model toggle (research 2026-06-16). The live runner/sim model TAKER fees
# (0.04% + 0.05% slippage = 0.18% round trip). The web research found maker
# (post-only limit) execution on BingX is ~0.02%/side with ~0 slippage (you set the
# fill price), cutting the round trip to ~0.04% — a ~4x lower cost wall that could
# make high-win-rate mean-reversion viable. This patches the cost model AT RUNTIME
# in THIS research process only: the backtest runner's fee constants AND the risk
# manager's fee-viability gate (Rule 4 reads config.round_trip_fee_pct, imported by
# name into risk.manager — so patch it there). It does NOT edit risk/manager.py,
# execution/live.py, or simulation.py; live keeps real taker fees until maker order
# execution is actually built and validated.
def _apply_fee_model(mode: str) -> float:
    import src.backtest.runner as _runner
    import src.risk.manager as _risk

    if mode == "none":
        # ZERO cost — measures the PURE GROSS directional edge (iter-41 diagnostic). If a
        # signal is gross-positive cross-era here but ~breakeven under maker, the edge is
        # REAL and merely eaten by fees → a sub-fee venue (§4) rescues it. If it is flat/
        # negative even at zero cost, there is no directional edge and no venue can help.
        _runner._TAKER_FEE_PCT = 0.0
        _runner._SLIPPAGE_PCT = 0.0
        _risk.round_trip_fee_pct = lambda: 0.0  # viability gate passes any positive expected gross
        bg._COST_PCT = 0.0
        return bg._COST_PCT
    if mode == "maker":
        _runner._TAKER_FEE_PCT = 0.02 / 100.0  # BingX perp maker, per side
        _runner._SLIPPAGE_PCT = 0.0  # post-only limit fills at the set price (no slippage)
        _risk.round_trip_fee_pct = lambda: 0.02 + 0.02  # 0.04% round trip for the viability gate
        bg._COST_PCT = (0.0002 + 0.0) * 2 * 100.0  # 0.04% (display only)
        return bg._COST_PCT
    return bg._COST_PCT  # taker default: 0.18% (runner/risk/bg untouched)


# --------------------------------------------------------------------------- #
# Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014) — stop-#2's missing test.
# RESEARCH_LOOP.md stop-condition #2 requires "deflated Sharpe > 0" but the loop
# only ever used the §30 win>55% proxy. The DSR answers: is the BEST strategy's
# Sharpe higher than the EXPECTED MAXIMUM of N random tries? (the multiple-testing
# / data-mining haircut). A marginal edge that looks fine in isolation usually
# fails it once you account for how many configs were searched.
# --------------------------------------------------------------------------- #
_EULER_GAMMA = 0.5772156649015329


def _pertrade_sharpe(returns: list[float]) -> float:
    n = len(returns)
    if n < 2:
        return 0.0
    m = sum(returns) / n
    sd = (sum((r - m) ** 2 for r in returns) / (n - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def _skew_kurt(returns: list[float]) -> tuple[float, float]:
    n = len(returns)
    m = sum(returns) / n
    sd = (sum((r - m) ** 2 for r in returns) / n) ** 0.5
    if sd == 0:
        return 0.0, 3.0
    skew = sum(((r - m) / sd) ** 3 for r in returns) / n
    kurt = sum(((r - m) / sd) ** 4 for r in returns) / n
    return skew, kurt


def _psr(sr: float, sr_star: float, t: int, skew: float, kurt: float) -> float:
    """Probabilistic Sharpe Ratio: P(true Sharpe > sr_star), non-normality-corrected."""
    if t < 2:
        return 0.0
    denom = (max(1e-9, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr)) ** 0.5
    z = (sr - sr_star) * ((t - 1) ** 0.5) / denom
    return statistics.NormalDist().cdf(z)


def _expected_max_sharpe(var_trials: float, n_trials: int) -> float:
    """E[max Sharpe] of N independent trials drawn from N(0, var_trials) under the null."""
    if n_trials < 2 or var_trials <= 0:
        return 0.0
    nd = statistics.NormalDist()
    a = nd.inv_cdf(1.0 - 1.0 / n_trials)
    b = nd.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return (var_trials**0.5) * ((1.0 - _EULER_GAMMA) * a + _EULER_GAMMA * b)


# --------------------------------------------------------------------------- #
# Higher-timeframe trend confirmation (iter 51) — never tested before: every prior
# regime/confluence filter (ADX floor, volatility floor) gated on the SAME timeframe
# as the entry. This gates the entry-TF signal on the TREND of a genuinely higher,
# separately-fetched timeframe (e.g. 1h entries confirmed by 4h EMA9/21 direction) —
# a structurally different confluence never tried on the deployed cross-leads.
# --------------------------------------------------------------------------- #


def _htf_trend_map(pair: str, htf: str, days: int, offset_days: int, fetch_fn) -> dict[int, str]:
    """Fetch `htf` OHLCV and return {htf_candle_close_ts: 'long'|'short'} via EMA9/21 cross."""
    _, rows = fetch_fn(pair, htf, days)
    closes = [float(r[4]) for r in rows]
    ema9, ema21 = _ema_series(closes, 9), _ema_series(closes, 21)
    off = len(ema9) - len(ema21)  # ema9[off+i] aligns with ema21[i] at closes-index (20+i)
    out: dict[int, str] = {}
    for i in range(len(ema21)):
        ts = int(rows[20 + i][0])  # ts of the closes-index the aligned ema9/ema21[i] pair belongs to
        out[ts] = "long" if ema9[off + i] > ema21[i] else "short"
    return out


def _htf_trend_at(trend_map: dict[int, str], entry_ts: int, htf_ms: int) -> Optional[str]:
    """The most-recently-CLOSED htf bar strictly before entry_ts (no lookahead)."""
    bucket_ts = (entry_ts // htf_ms) * htf_ms - htf_ms
    for _ in range(10):  # tolerate a few missing/gapped bars before giving up
        if bucket_ts in trend_map:
            return trend_map[bucket_ts]
        bucket_ts -= htf_ms
    return None


# --------------------------------------------------------------------------- #
# Points scoreboard (docs/13-points-framework.md, 2026-07-09). 1 point = 1 bp of
# entry price, direction-signed, computed from FILL prices — so under --fees
# maker/none (zero slippage) points are pure GROSS directional capture, fee-free
# by construction. Gross R comes from bg._enrich_trades' realized_R (signed price
# move / planned risk distance — already fee-free). The survivor bar is the §6.1
# joint target: points win >= 65% AND points expectancy > 0 (win rate alone is
# purchasable via geometry and never counts as success on its own).
# --------------------------------------------------------------------------- #

_POINTS_MIN_N = 30  # same small-sample floor the $-leaderboard survivor line uses


def _trade_points_bps(t: dict) -> float:
    """Direction-signed price move in bps of entry (gross of fees)."""
    entry, exit_ = float(t["entry_price"]), float(t["exit_price"])
    sign = 1.0 if t["direction"] == "long" else -1.0
    return sign * (exit_ - entry) / entry * 10_000.0


def _points_metrics(trades: list[dict]) -> dict[str, float]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "win": 0.0, "avg_bps": 0.0, "med_bps": 0.0, "pf": 0.0, "r_exp": 0.0}
    pts = [_trade_points_bps(t) for t in trades]
    win_sum = sum(p for p in pts if p > 0)
    loss_sum = sum(p for p in pts if p <= 0)
    rs = [float(t.get("realized_R", 0.0)) for t in trades]
    return {
        "n": n,
        "win": sum(1 for p in pts if p > 0) / n,
        "avg_bps": sum(pts) / n,
        "med_bps": sorted(pts)[n // 2],
        "pf": (win_sum / abs(loss_sum)) if loss_sum != 0.0 else float("inf"),
        "r_exp": sum(rs) / n,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--pairs", default=None, help="comma list to override PAIRS")
    ap.add_argument(
        "--by-pair",
        action="store_true",
        help="also print a per-pair OOS avg$/trade table (breadth check for stop-#2: ≥3 pairs +EV)",
    )
    ap.add_argument(
        "--deflated-sharpe",
        action="store_true",
        help="compute PSR + Deflated Sharpe (stop-#2's multiple-testing test) for the top combo; "
        "run a BROAD --algos set so the trial count N is meaningful",
    )
    ap.add_argument("--algos", default=None, help="comma list to restrict the algo set")
    ap.add_argument("--exits", default="tight,wide", help="comma list of exit profiles")
    ap.add_argument("--regime", default=None, help="restrict firing to one regime: ranging|trending|volatile")
    ap.add_argument(
        "--fees", default="taker", choices=["taker", "maker", "none"], help="cost model (see _apply_fee_model)"
    )
    ap.add_argument(
        "--offset-days",
        type=int,
        default=0,
        dest="offset_days",
        help="shift the window END back N days for a LOCKBOX test (e.g. --days 365 --offset-days 365 "
        "= the year before last, never seen by any recent-window search). Crypto only.",
    )
    ap.add_argument("--forex", action="store_true", help="search forex/metals (yfinance) instead of crypto")
    ap.add_argument(
        "--htf-confirm",
        default=None,
        dest="htf_confirm",
        choices=["4h", "1d"],
        help="only keep trades whose direction agrees with the EMA9/21 trend on this HIGHER timeframe "
        "(genuinely new confluence — never same-TF like the ADX/volatility-regime filters)",
    )
    ap.add_argument(
        "--points",
        action="store_true",
        help="score on the POINTS framework (docs/13-points-framework.md): gross bps of entry price + "
        "points win rate; ALSO bypasses risk Rule 3's R/R>=1.2 floor at runtime so the inverted-"
        "geometry hiwin* exit presets can trade (research-only patch; frozen risk file untouched)",
    )
    args = ap.parse_args()

    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    base = load_params("params.json")
    base = dataclasses.replace(base, volume_ratio_min=1.1)  # most-permissive (volume gate is bypassed anyway)
    _apply_fee_model(args.fees)
    _install_search_gates(args.regime)
    if args.points:
        # Rule 3 (tp/sl >= 1.2) would reject every hiwin bracket at the door. Bypass the
        # floor in THIS research process only — the same runtime-patch precedent as
        # _apply_fee_model (the frozen risk/manager.py is never edited). Rules 1/2/4/5/6
        # stay live so fee-viability and liquidation realism are preserved.
        import src.risk.manager as _risk_mgr

        _risk_mgr._MIN_RR = 0.0
        print("[points] risk Rule 3 R/R floor bypassed for this process (research-only; see docs/13 §9)")

    default_pairs = FOREX_PAIRS if args.forex else lab.PAIRS
    pairs = [p.strip() for p in args.pairs.split(",")] if args.pairs else default_pairs
    if args.forex and args.offset_days:
        raise SystemExit("--offset-days (lockbox) is crypto-only; not supported with --forex")

    def fetch(pair: str, tf: str, days: int) -> tuple:
        if args.forex:
            return _fetch_forex(pair, tf, days)
        return bt.fetch_ohlcv(pair, tf, days, offset_days=args.offset_days)

    algos = [a.strip() for a in args.algos.split(",")] if args.algos else list(_ALGOS)
    exits = [e.strip() for e in args.exits.split(",") if e.strip() in EXITS]
    tag = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    combos = [(a, e) for a in algos for e in exits]

    print(
        f"=== Kestrel ALGORITHM SEARCH ({args.tf}, {args.days}d, {cfg.leverage}x, "
        f"fees={args.fees}, regime={args.regime or 'all'}"
        f"{f', LOCKBOX offset={args.offset_days}d' if args.offset_days else ''}) ===",
        flush=True,
    )
    print(
        f"algos={len(algos)} exits={len(exits)} pairs={len(pairs)} "
        f"combos={len(combos)} backtests={len(combos) * len(pairs)} "
        f"cost/trade≈{bg._COST_PCT:.2f}%",
        flush=True,
    )

    # pooled[(algo,exit)] = {"oos": [...], "ins": [...], "n_oos": int}
    pooled: dict[tuple[str, str], dict] = {c: {"oos": [], "ins": [], "n_oos": 0} for c in combos}
    pooled_htf: dict[tuple[str, str], dict] = {c: {"oos": [], "ins": [], "n_oos": 0} for c in combos}
    # by_pair[(algo,exit,pair)] = list of OOS trades (only when --by-pair)
    by_pair: dict[tuple[str, str, str], list] = {}
    skipped: list[str] = []

    for pi, pair in enumerate(pairs, 1):
        try:
            ex_used, raw = fetch(pair, args.tf, args.days)
        except Exception as exc:  # noqa: BLE001 — survey loop: report and continue
            print(
                f"[{pi}/{len(pairs)}] {pair}: FETCH FAILED ({type(exc).__name__}: {str(exc)[:70]}) — skipped",
                flush=True,
            )
            skipped.append(pair)
            continue
        candles = bg.build_candles_for(pair, args.tf, cfg, base, raw)
        ts_index = {int(c.ts): i for i, c in enumerate(candles)}
        split_ts = candles[int(len(candles) * 0.60)].ts
        n_oos = sum(1 for c in candles if c.ts >= split_ts)
        print(f"[{pi}/{len(pairs)}] {pair}: src={ex_used} candles={len(candles)} (OOS={n_oos})", flush=True)

        htf_map: dict[int, str] = {}
        if args.htf_confirm:
            try:
                htf_map = _htf_trend_map(pair, args.htf_confirm, args.days, args.offset_days, fetch)
            except Exception as exc:  # noqa: BLE001 — survey loop: degrade to no-confirmation for this pair
                print(f"  htf-confirm fetch failed for {pair}: {type(exc).__name__} — skipped for this pair")
            htf_ms = bt._TF_MS[args.htf_confirm]

        for algo, exit_name in combos:
            p = dataclasses.replace(base, **EXITS[exit_name])
            trades = run_backtest(candles, p, cfg, bot_id=f"as-{pair}-{algo}-{exit_name}", enabled_patterns=[algo])[
                "trades"
            ]
            bg._enrich_trades(trades, ts_index, candles)
            d = pooled[(algo, exit_name)]
            oos_trades = [t for t in trades if t["entry_ts"] >= split_ts]
            d["oos"].extend(oos_trades)
            d["ins"].extend(t for t in trades if t["entry_ts"] < split_ts)
            d["n_oos"] += n_oos
            if args.by_pair:
                by_pair[(algo, exit_name, pair)] = oos_trades

            if args.htf_confirm and htf_map:
                confirmed = [t for t in trades if _htf_trend_at(htf_map, int(t["entry_ts"]), htf_ms) == t["direction"]]
                dh = pooled_htf[(algo, exit_name)]
                dh["oos"].extend(t for t in confirmed if t["entry_ts"] >= split_ts)
                dh["ins"].extend(t for t in confirmed if t["entry_ts"] < split_ts)
                dh["n_oos"] += n_oos

    # Build leaderboard
    rows = []
    for (algo, exit_name), d in pooled.items():
        mo = bg._ext_metrics(d["oos"], d["n_oos"])
        mi = bg._ext_metrics(d["ins"], d["n_oos"])
        rows.append(
            {
                "algo": algo,
                "exit": exit_name,
                "oos": mo,
                "ins": mi,
                "degrade": round(mo["avg_pnl_usdt"] - mi["avg_pnl_usdt"], 5),
            }
        )
    rows.sort(key=lambda r: (r["oos"]["total_trades"] == 0, -r["oos"]["avg_pnl_usdt"]))

    # §30 OOS bar: win>55% · R/R>=1.2 · net>0 · n>=30
    def passes(m: dict) -> bool:
        return m["total_pnl_usdt"] > 0 and m["win_rate"] > 0.55 and bg._rr(m) >= 1.2 and m["total_trades"] >= 30

    survivors = [r for r in rows if passes(r["oos"])]

    print("\n=== ALGO LEADERBOARD (out-of-sample, top 25) ===", flush=True)
    print(
        f"  {'algo':16s} {'exit':5s} {'n':>5s} {'win%':>6s} {'avg$':>9s} {'net$':>9s} "
        f"{'R/R':>5s} {'expR':>6s} {'tp%':>5s} {'to%':>5s} {'clr%':>5s} {'IS→OOS':>8s}",
        flush=True,
    )
    for r in rows[:25]:
        m = r["oos"]
        print(
            f"  {r['algo']:16s} {r['exit']:5s} {m['total_trades']:5d} {m['win_rate'] * 100:6.1f} "
            f"{m['avg_pnl_usdt']:9.4f} {m['total_pnl_usdt']:9.3f} {bg._rr(m):5.2f} "
            f"{m['expectancy_R']:6.2f} {m['tp_rate'] * 100:5.0f} {m['timeout_rate'] * 100:5.0f} "
            f"{m['pct_clearing_cost'] * 100:5.0f} {r['degrade']:+8.4f}",
            flush=True,
        )

    print("\n=== VERDICT (§30 OOS: win>55% · R/R≥1.2 · net>0 · n≥30) ===", flush=True)
    print(f"  algorithms clearing the bar: {len(survivors)} / {len(rows)}", flush=True)
    if survivors:
        for r in survivors:
            print(
                f"  SURVIVOR: {r['algo']} / {r['exit']} — win {r['oos']['win_rate'] * 100:.1f}% "
                f"net ${r['oos']['total_pnl_usdt']:.2f} R/R {bg._rr(r['oos']):.2f} "
                f"n={r['oos']['total_trades']}",
                flush=True,
            )
    elif rows:
        b = rows[0]
        print(
            f"  best: {b['algo']}/{b['exit']} avg=${b['oos']['avg_pnl_usdt']:.4f}/trade "
            f"win={b['oos']['win_rate'] * 100:.1f}% n={b['oos']['total_trades']} "
            f"— no edge; cost floor dominates (consistent with the no-edge history)",
            flush=True,
        )
    if skipped:
        print(f"  NOTE: {len(skipped)} pair(s) skipped (fetch failed): {', '.join(skipped)}", flush=True)

    if args.points:
        # POINTS scoreboard (docs/13-points-framework.md §2/§6.1) — gross bps of entry
        # price, fee-free by construction. Survivor bar = the JOINT §6.1 target:
        # points win >= 65% AND avg gross bps > 0 AND n >= 30. Win rate alone never counts.
        prows = []
        for (algo, exit_name), d in pooled.items():
            po = _points_metrics(d["oos"])
            pi_ = _points_metrics(d["ins"])
            prows.append({"algo": algo, "exit": exit_name, "oos": po, "ins": pi_})
        prows.sort(key=lambda r: (r["oos"]["n"] == 0, -r["oos"]["avg_bps"]))

        print("\n=== POINTS LEADERBOARD (gross bps of entry price — OOS pooled; docs/13 §2) ===", flush=True)
        print(
            f"  {'algo':16s} {'exit':7s} {'n':>5s} {'pwin%':>6s} {'avg_bps':>8s} {'med_bps':>8s} "
            f"{'ptsPF':>6s} {'grossR':>7s} {'IS→OOS':>8s}",
            flush=True,
        )
        for r in prows:
            po, pi_ = r["oos"], r["ins"]
            if po["n"] == 0:
                continue
            pf_s = f"{po['pf']:6.2f}" if po["pf"] != float("inf") else "   inf"
            print(
                f"  {r['algo']:16s} {r['exit']:7s} {po['n']:5d} {po['win'] * 100:6.1f} "
                f"{po['avg_bps']:+8.2f} {po['med_bps']:+8.2f} {pf_s} {po['r_exp']:+7.3f} "
                f"{po['avg_bps'] - pi_['avg_bps']:+8.2f}",
                flush=True,
            )

        psurv = [
            r for r in prows if r["oos"]["win"] >= 0.65 and r["oos"]["avg_bps"] > 0.0 and r["oos"]["n"] >= _POINTS_MIN_N
        ]
        print("\n=== POINTS VERDICT (§6.1 joint bar: pwin>=65% AND avg_bps>0 AND n>=30) ===", flush=True)
        print(
            f"  combos clearing the joint bar: {len(psurv)} / {sum(1 for r in prows if r['oos']['n'] > 0)}", flush=True
        )
        for r in psurv:
            po = r["oos"]
            print(
                f"  POINTS SURVIVOR: {r['algo']} / {r['exit']} — pwin {po['win'] * 100:.1f}% "
                f"avg {po['avg_bps']:+.2f} bps (fee shelf: {'taker-viable' if po['avg_bps'] >= 18 else 'maker-viable' if po['avg_bps'] >= 4 else 'signal-only (<4bps)'}) "
                f"grossR {po['r_exp']:+.3f} n={po['n']}",
                flush=True,
            )
        if not psurv and prows and prows[0]["oos"]["n"] > 0:
            b = prows[0]
            print(
                f"  best by avg_bps: {b['algo']}/{b['exit']} — pwin {b['oos']['win'] * 100:.1f}% "
                f"avg {b['oos']['avg_bps']:+.2f} bps n={b['oos']['n']} — joint bar NOT met",
                flush=True,
            )

    if args.htf_confirm:
        print(
            f"\n=== HTF-CONFIRM ({args.htf_confirm} EMA9/21 trend agreement — unfiltered vs confirmed) ===",
            flush=True,
        )
        print(
            f"  {'algo':16s} {'exit':5s} {'n_all':>6s} {'avg_all':>9s} {'n_htf':>6s} {'avg_htf':>9s} "
            f"{'win_htf':>8s} {'kept%':>6s}",
            flush=True,
        )
        for algo, exit_name in combos:
            mo = bg._ext_metrics(pooled[(algo, exit_name)]["oos"], pooled[(algo, exit_name)]["n_oos"])
            dh = pooled_htf[(algo, exit_name)]
            mh = bg._ext_metrics(dh["oos"], dh["n_oos"])
            if mo["total_trades"] == 0:
                continue
            kept_pct = 100.0 * mh["total_trades"] / mo["total_trades"] if mo["total_trades"] else 0.0
            print(
                f"  {algo:16s} {exit_name:5s} {mo['total_trades']:6d} {mo['avg_pnl_usdt']:9.4f} "
                f"{mh['total_trades']:6d} {mh['avg_pnl_usdt']:9.4f} {mh['win_rate'] * 100:7.1f}% {kept_pct:5.1f}%",
                flush=True,
            )

    if args.by_pair and by_pair:
        print("\n=== PER-PAIR OOS avg$/trade (breadth check — +EV pair count per algo/exit) ===", flush=True)
        seen_combos = sorted({(a, e) for (a, e, _p) in by_pair})
        for algo, exit_name in seen_combos:
            cells = []
            pos = 0
            for pair in pairs:
                trades = by_pair.get((algo, exit_name, pair))
                if not trades:
                    continue
                avg = sum(t["pnl_net_usdt"] for t in trades) / len(trades)
                pos += 1 if avg > 0 else 0
                cells.append(f"{pair.split('/')[0]}:{avg:+.4f}(n{len(trades)})")
            print(f"  {algo}/{exit_name}  [+EV {pos}/{len(cells)} pairs]  " + "  ".join(cells), flush=True)

        if args.points:
            # Same breadth check on the POINTS scoreboard: per-pair avg gross bps + points win %.
            print("\n=== PER-PAIR OOS points (avg gross bps @ points-win% — breadth on the §6.1 bar) ===", flush=True)
            for algo, exit_name in seen_combos:
                cells = []
                pos = 0
                for pair in pairs:
                    trades = by_pair.get((algo, exit_name, pair))
                    if not trades:
                        continue
                    pm = _points_metrics(trades)
                    pos += 1 if pm["avg_bps"] > 0 else 0
                    cells.append(f"{pair.split('/')[0]}:{pm['avg_bps']:+.1f}@{pm['win'] * 100:.0f}%(n{pm['n']})")
                print(f"  {algo}/{exit_name}  [pts+ {pos}/{len(cells)} pairs]  " + "  ".join(cells), flush=True)

    if args.deflated_sharpe:
        min_n = 30
        trials = [
            ((a, e), [float(t["pnl_net_usdt"]) for t in d["oos"]])
            for (a, e), d in pooled.items()
            if len(d["oos"]) >= min_n
        ]
        print("\n=== DEFLATED SHARPE (stop-#2 multiple-testing test) ===", flush=True)
        if len(trials) < 2:
            print(
                f"  need ≥2 combos with ≥{min_n} OOS trades (got {len(trials)}) — run a broader --algos set", flush=True
            )
        else:
            sharpes = [_pertrade_sharpe(r) for _, r in trials]
            var_trials = statistics.pvariance(sharpes)
            n_trials = len(trials)
            (balgo, bexit), brets = max(trials, key=lambda x: _pertrade_sharpe(x[1]))
            bsr = _pertrade_sharpe(brets)
            skew, kurt = _skew_kurt(brets)
            t_obs = len(brets)
            psr0 = _psr(bsr, 0.0, t_obs, skew, kurt)
            print(
                f"  trials N={n_trials} (combos ≥{min_n} trades) · Var(trial Sharpe)={var_trials:.5f} · "
                f"Sharpe spread [{min(sharpes):+.3f},{max(sharpes):+.3f}]",
                flush=True,
            )
            print(
                f"  BEST by Sharpe: {balgo}/{bexit} — per-trade Sharpe {bsr:+.4f}, T={t_obs}, "
                f"skew {skew:+.2f}, kurt {kurt:.2f}",
                flush=True,
            )
            print(
                f"  PSR(>0)={psr0:.3f}  (P the true Sharpe is positive at all, BEFORE the data-mining haircut)",
                flush=True,
            )
            for mult in (1, 3, 10):
                n_eff = n_trials * mult
                sr0 = _expected_max_sharpe(var_trials, n_eff)
                dsr = _psr(bsr, sr0, t_obs, skew, kurt)
                verdict = "PASS — beats data-mining" if dsr > 0.95 else "FAIL — within data-mining noise"
                print(f"  DSR @ N={n_eff:<4d}: SR*={sr0:+.4f} → DSR={dsr:.3f}  [{verdict}]", flush=True)
            print(
                "  (stop-#2 'deflated Sharpe>0' ⇒ DSR>0.95: Sharpe beats the expected MAX of N random tries)",
                flush=True,
            )

    _write_reports(tag, args, cfg, pairs, rows, survivors, skipped)


def _write_reports(tag, args, cfg, pairs, rows, survivors, skipped) -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, f"algo_search_{tag}")

    lines = [
        f"# Kestrel algorithm search — {tag} UTC",
        "",
        f"- **window:** {args.days}d {args.tf} · **leverage:** {cfg.leverage}x · cost/trade ≈ {bg._COST_PCT:.2f}%",
        f"- **pairs:** {', '.join(pairs)}" + (f" · **skipped:** {', '.join(skipped)}" if skipped else ""),
        f"- **algorithms × exits:** {len({r['algo'] for r in rows})} × "
        f"{len({r['exit'] for r in rows})} = {len(rows)} pooled cells",
        f"- **§30 OOS survivors:** {len(survivors)} / {len(rows)}",
        "",
        "| algo | exit | n | win% | avg $ | net $ | R/R | expR | tp% | to% | clr% | IS→OOS |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        m = r["oos"]
        lines.append(
            f"| {r['algo']} | {r['exit']} | {m['total_trades']} | {m['win_rate'] * 100:.1f} | "
            f"{m['avg_pnl_usdt']:.4f} | {m['total_pnl_usdt']:.3f} | {bg._rr(m):.2f} | "
            f"{m['expectancy_R']:.2f} | {m['tp_rate'] * 100:.0f} | {m['timeout_rate'] * 100:.0f} | "
            f"{m['pct_clearing_cost'] * 100:.0f} | {r['degrade']:+.4f} |"
        )
    lines += ["", "## Verdict", ""]
    if survivors:
        lines.append(
            f"**{len(survivors)} algorithm(s) clear the §30 OOS bar** — promote to "
            "`src/signal/patterns.py` (with regime wiring + unit test) before deploying:"
        )
        for r in survivors:
            lines.append(
                f"- `{r['algo']}` / {r['exit']}: win {r['oos']['win_rate'] * 100:.1f}%, "
                f"net ${r['oos']['total_pnl_usdt']:.2f}, R/R {bg._rr(r['oos']):.2f}, "
                f"n={r['oos']['total_trades']}"
            )
    elif rows:
        b = rows[0]
        lines.append(
            f"**No algorithm clears the §30 OOS bar.** Best was `{b['algo']}`/{b['exit']} at "
            f"avg ${b['oos']['avg_pnl_usdt']:.4f}/trade, win {b['oos']['win_rate'] * 100:.1f}%, "
            f"n={b['oos']['total_trades']}. Consistent with the documented no-edge result."
        )
    with open(f"{stem}.md", "w") as f:
        f.write("\n".join(lines) + "\n")

    survivor_specs = [
        {
            "algo": r["algo"],
            "exit": r["exit"],
            "params": EXITS[r["exit"]],
            "oos": {k: r["oos"][k] for k in ("total_trades", "win_rate", "total_pnl_usdt")},
        }
        for r in survivors
    ]
    with open(f"{stem}_survivors.json", "w") as f:
        json.dump(survivor_specs, f, indent=2)
    print(f"\nwrote {os.path.normpath(stem)}.md  (+ _survivors.json: {len(survivor_specs)})", flush=True)


if __name__ == "__main__":
    main()
