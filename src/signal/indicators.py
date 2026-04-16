"""
Layer 1 — pure indicator computation functions.
All functions are stateless transforms: data in → value out. No I/O.

Indicators computed here are stored in the candles table at candle close.
They are NOT recomputed later — read from DB when needed.
"""

from __future__ import annotations

import math
from typing import Sequence

from src.config import Candle

# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------


def compute_ema(prices: Sequence[float], period: int) -> float:
    """Compute the EMA of the last element given a price series.

    Uses SMA of first `period` values as the seed, then applies
    Wilder-style smoothing (k = 2 / (period + 1)) for each subsequent value.

    Requires len(prices) >= period.
    """
    if len(prices) < period:
        return sum(prices) / len(prices)

    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1.0 - k)
    return ema


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


def compute_rsi(closes: Sequence[float], period: int = 14) -> float:
    """Compute RSI(period) for the last value in the close series.

    Uses Wilder's smoothed average (equivalent to EMA with α = 1/period).
    Requires len(closes) >= period + 1.
    """
    if len(closes) < period + 1:
        return 50.0  # neutral fallback

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    # Initial averages (simple)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder's smoothing for remaining periods
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------


def compute_bb(closes: Sequence[float], period: int = 20, std_dev: float = 2.0) -> tuple[float, float, float]:
    """Return (upper, lower, width) Bollinger Bands for the last value.

    width = (upper - lower) / middle  (normalised BB width)
    Requires len(closes) >= period.
    """
    if len(closes) < period:
        avg = sum(closes) / len(closes)
        return avg, avg, 0.0

    window = list(closes[-period:])
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    sigma = math.sqrt(variance)

    upper = mean + std_dev * sigma
    lower = mean - std_dev * sigma
    width = (upper - lower) / mean if mean != 0.0 else 0.0
    return upper, lower, width


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------


def compute_atr(candles: Sequence[Candle], period: int = 14) -> float:
    """Compute ATR(period) using Wilder's smoothing.

    True Range = max(|H-L|, |H-prev_C|, |L-prev_C|)
    Requires len(candles) >= period + 1.
    """
    if len(candles) < 2:
        return 0.0

    trs: list[float] = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev_close = candles[i - 1].close
        tr = max(
            c.high - c.low,
            abs(c.high - prev_close),
            abs(c.low - prev_close),
        )
        trs.append(tr)

    if len(trs) < period:
        return sum(trs) / len(trs)

    # Initial ATR = SMA of first `period` TRs
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


# ---------------------------------------------------------------------------
# ADX
# ---------------------------------------------------------------------------


def compute_adx(candles: Sequence[Candle], period: int = 14) -> float:
    """Compute ADX(period) using Wilder's smoothing.

    Requires at least 2*period + 1 candles for a meaningful value.
    Returns 0.0 with insufficient data.
    """
    if len(candles) < period + 1:
        return 0.0

    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []
    tr_list: list[float] = []

    for i in range(1, len(candles)):
        curr = candles[i]
        prev = candles[i - 1]

        up_move = curr.high - prev.high
        down_move = prev.low - curr.low

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0

        tr = max(
            curr.high - curr.low,
            abs(curr.high - prev.close),
            abs(curr.low - prev.close),
        )

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)

    if len(tr_list) < period:
        return 0.0

    # Seed smoothed values with SMA of first period
    smooth_tr = sum(tr_list[:period])
    smooth_plus = sum(plus_dm_list[:period])
    smooth_minus = sum(minus_dm_list[:period])

    dx_values: list[float] = []

    def _dx(sp: float, sm: float, st: float) -> float:
        if st == 0.0:
            return 0.0
        di_plus = 100.0 * sp / st
        di_minus = 100.0 * sm / st
        denom = di_plus + di_minus
        return 100.0 * abs(di_plus - di_minus) / denom if denom != 0.0 else 0.0

    dx_values.append(_dx(smooth_plus, smooth_minus, smooth_tr))

    # Wilder's smoothing for subsequent periods
    for i in range(period, len(tr_list)):
        smooth_tr = smooth_tr - smooth_tr / period + tr_list[i]
        smooth_plus = smooth_plus - smooth_plus / period + plus_dm_list[i]
        smooth_minus = smooth_minus - smooth_minus / period + minus_dm_list[i]
        dx_values.append(_dx(smooth_plus, smooth_minus, smooth_tr))

    if not dx_values:
        return 0.0

    # ADX = smoothed DX
    if len(dx_values) < period:
        return sum(dx_values) / len(dx_values)

    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = (adx * (period - 1) + dx) / period
    return adx


# ---------------------------------------------------------------------------
# Volume MA and ratio
# ---------------------------------------------------------------------------


def compute_volume_ma(volumes: Sequence[float], period: int = 20) -> float:
    """Compute simple moving average of volume over `period` bars."""
    if not volumes:
        return 0.0
    window = list(volumes[-period:])
    return sum(window) / len(window)


def compute_volume_stddev(volumes: Sequence[float], period: int = 20) -> float:
    """Compute standard deviation of volume over `period` bars."""
    if len(volumes) < 2:
        return 0.0
    window = list(volumes[-period:])
    mean = sum(window) / len(window)
    variance = sum((v - mean) ** 2 for v in window) / len(window)
    return math.sqrt(variance)


# ---------------------------------------------------------------------------
# Aggregate — compute all indicators for the latest candle
# ---------------------------------------------------------------------------


def compute_all_indicators(
    candles: Sequence[Candle],
    ema_fast: int = 9,
    ema_slow: int = 21,
) -> dict[str, float | None]:
    """Compute all indicators for the most recent candle in the series.

    Returns a dict of indicator name → float value.
    Requires at least 2 candles; more is better for ADX accuracy.
    """
    if not candles:
        return {}

    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]

    ema9 = compute_ema(closes, ema_fast)
    ema21 = compute_ema(closes, ema_slow)
    rsi14 = compute_rsi(closes, 14)
    atr14 = compute_atr(candles, 14)
    bb_upper, bb_lower, bb_width = compute_bb(closes, 20)
    adx = compute_adx(candles, 14)
    volume_ma20 = compute_volume_ma(volumes, 20)
    volume_ratio = candles[-1].volume / volume_ma20 if volume_ma20 > 0.0 else 1.0

    return {
        "ema9": ema9,
        "ema21": ema21,
        "rsi14": rsi14,
        "atr14": atr14,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_width": bb_width,
        "adx": adx,
        "volume_ma20": volume_ma20,
        "volume_ratio": volume_ratio,
    }
