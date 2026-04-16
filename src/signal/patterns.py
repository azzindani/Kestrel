"""
Layer 1 — pattern registry and five pattern implementations.

Extension model (CLAUDE.md §9):
    Decorating a function with @register("name") adds it to the registry.
    The detector uses the registry — no if/else dispatch, no hardcoding.

Public API:
    registry: dict[str, PatternFn]
    register(name) -> decorator
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from src.config import Candle, Direction, Params, PatternResult, PatternType
from src.signal.indicators import compute_atr, compute_volume_ma, compute_volume_stddev

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PatternFn = Callable[[Sequence[Candle], Params], Optional[PatternResult]]

registry: dict[str, PatternFn] = {}


def register(name: str) -> Callable[[PatternFn], PatternFn]:
    """Decorator that registers a pattern function into the registry."""

    def wrap(fn: PatternFn) -> PatternFn:
        registry[name] = fn
        return fn

    return wrap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _direction_from_candle(candle: Candle) -> Optional[Direction]:
    if candle.direction == "bullish":
        return Direction.LONG
    if candle.direction == "bearish":
        return Direction.SHORT
    if candle.close > candle.open:
        return Direction.LONG
    if candle.close < candle.open:
        return Direction.SHORT
    return None


def _body_size(c: Candle) -> float:
    return c.body_size if c.body_size is not None else abs(c.close - c.open)


def _total_range(c: Candle) -> float:
    return c.total_range if c.total_range is not None else (c.high - c.low)


def _body_ratio(c: Candle) -> float:
    tr = _total_range(c)
    return c.body_ratio if c.body_ratio is not None else (_body_size(c) / tr if tr > 0 else 0.0)


def _upper_wick(c: Candle) -> float:
    return c.upper_wick if c.upper_wick is not None else c.high - max(c.open, c.close)


def _lower_wick(c: Candle) -> float:
    return c.lower_wick if c.lower_wick is not None else min(c.open, c.close) - c.low


def _vol_ratio(c: Candle, volume_ma: float) -> float:
    if c.volume_ratio is not None:
        return c.volume_ratio
    return c.volume / volume_ma if volume_ma > 0 else 1.0


# ---------------------------------------------------------------------------
# Pattern: impulse_retracement (CLAUDE.md §23)
# ---------------------------------------------------------------------------


@register("impulse_retracement")
def detect_impulse_retracement(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """
    Trigger: body_ratio > body_ratio_min · volume_ratio > volume_ratio_min
    Next:    retracement 30–50% of trigger body · lower volume · ✗ close below trigger open (long)
    Entry:   close of retracement candle
    """
    if len(candles) < 3:
        return None

    trigger = candles[-2]
    retrace = candles[-1]

    volumes = [c.volume for c in candles]
    vol_ma = compute_volume_ma(volumes, 20)

    trigger_br = _body_ratio(trigger)
    trigger_vol_ratio = _vol_ratio(trigger, vol_ma)

    if trigger_br < params.body_ratio_min:
        return None
    if trigger_vol_ratio < params.volume_ratio_min:
        return None

    direction = _direction_from_candle(trigger)
    if direction is None:
        return None

    trigger_body = _body_size(trigger)
    if trigger_body == 0.0:
        return None

    # Retracement size relative to trigger body
    retrace_body = _body_size(retrace)
    retrace_frac = retrace_body / trigger_body

    if not (params.retracement_min <= retrace_frac <= params.retracement_max):
        return None

    # Retrace volume must be lower than trigger volume
    if retrace.volume >= trigger.volume:
        return None

    # For long: retrace candle must not close below trigger open
    if direction is Direction.LONG and retrace.close < trigger.open:
        return None
    # For short: retrace candle must not close above trigger open
    if direction is Direction.SHORT and retrace.close > trigger.open:
        return None

    # Confidence: influenced by body ratio and volume excess
    confidence = min(
        0.4 + trigger_br * 0.3 + min(trigger_vol_ratio / params.volume_ratio_min - 1.0, 0.3),
        1.0,
    )

    return PatternResult(
        pattern=PatternType.IMPULSE_RETRACEMENT,
        direction=direction,
        confidence=round(confidence, 3),
        details={
            "trigger_body_ratio": round(trigger_br, 3),
            "trigger_vol_ratio": round(trigger_vol_ratio, 3),
            "retrace_frac": round(retrace_frac, 3),
        },
    )


# ---------------------------------------------------------------------------
# Pattern: wick_rejection (CLAUDE.md §23)
# ---------------------------------------------------------------------------


@register("wick_rejection")
def detect_wick_rejection(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """
    Trigger: lower_wick > wick_ratio_min × body · close in top 30% of range · within 1 ATR of support
    Long only (price rejecting lower boundary).
    """
    if len(candles) < 3:
        return None

    c = candles[-1]
    atr = compute_atr(candles, 14)
    if atr == 0.0:
        return None

    body = _body_size(c)
    lower = _lower_wick(c)
    total = _total_range(c)

    if body == 0.0:
        return None

    wick_ratio = lower / body
    if wick_ratio < params.wick_ratio_min:
        return None

    if total == 0.0:
        return None

    # Close in top 30% of candle range
    close_position = (c.close - c.low) / total
    if close_position < 0.70:
        return None

    # Find approximate support: lowest close in last 10 candles (excluding current)
    recent_lows = [x.low for x in candles[-11:-1]]
    if not recent_lows:
        return None
    support = min(recent_lows)
    if abs(c.low - support) > atr:
        return None

    # Short wick rejection is the inverse — upper wick at resistance
    # Here we implement the long version only; registry could hold "wick_rejection_short" separately
    direction = Direction.LONG

    confidence = min(0.45 + (wick_ratio - params.wick_ratio_min) * 0.1 + close_position * 0.15, 1.0)

    return PatternResult(
        pattern=PatternType.WICK_REJECTION,
        direction=direction,
        confidence=round(confidence, 3),
        details={
            "wick_ratio": round(wick_ratio, 3),
            "close_position": round(close_position, 3),
            "support": round(support, 2),
        },
    )


# ---------------------------------------------------------------------------
# Pattern: compression_breakout (CLAUDE.md §23)
# ---------------------------------------------------------------------------


@register("compression_breakout")
def detect_compression_breakout(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """
    Setup:   ATR(5) < ATR(20) × compression_factor · BB width declining 3+ candles · volume declining
    Trigger: close outside BB boundary · volume > volume_ma20 × 1.5
    """
    if len(candles) < 25:
        return None

    c = candles[-1]

    atr5 = compute_atr(candles[-6:], 5) if len(candles) >= 6 else 0.0
    atr20 = compute_atr(candles[-21:], 20) if len(candles) >= 21 else 0.0

    if atr20 == 0.0:
        return None
    if atr5 >= atr20 * params.compression_factor:
        return None

    # BB width must have been declining for 3+ candles
    bb_widths = [x.bb_width for x in candles[-5:] if x.bb_width is not None]
    if len(bb_widths) < 4:
        return None
    if not all(bb_widths[i] >= bb_widths[i + 1] for i in range(len(bb_widths) - 1)):
        return None

    # Volume declining in pre-breakout candles
    pre_vols = [x.volume for x in candles[-4:-1]]
    if len(pre_vols) >= 2:
        if not all(pre_vols[i] >= pre_vols[i + 1] for i in range(len(pre_vols) - 1)):
            return None

    # Trigger: close outside BB with high volume
    bb_upper = c.bb_upper
    bb_lower = c.bb_lower
    if bb_upper is None or bb_lower is None:
        return None

    volumes = [x.volume for x in candles]
    vol_ma = compute_volume_ma(volumes, 20)
    vol_ratio = _vol_ratio(c, vol_ma)

    if vol_ratio < 1.5:
        return None

    if c.close > bb_upper:
        direction = Direction.LONG
    elif c.close < bb_lower:
        direction = Direction.SHORT
    else:
        return None

    confidence = min(0.50 + (vol_ratio - 1.5) * 0.1, 0.95)

    return PatternResult(
        pattern=PatternType.COMPRESSION_BREAKOUT,
        direction=direction,
        confidence=round(confidence, 3),
        details={
            "atr5_atr20_ratio": round(atr5 / atr20, 3),
            "vol_ratio": round(vol_ratio, 3),
            "bb_upper": round(bb_upper, 2),
            "bb_lower": round(bb_lower, 2),
        },
    )


# ---------------------------------------------------------------------------
# Pattern: momentum_continuation (CLAUDE.md §23)
# ---------------------------------------------------------------------------


@register("momentum_continuation")
def detect_momentum_continuation(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """
    Setup:   N consecutive same-direction candles · each body ≥ previous (acceleration) · volume increasing
    Trigger: (N+1)th candle is small retracement · body < 40% of Nth · lower volume
    Entry:   close of retracement candle
    """
    n = params.momentum_acceleration_candles
    required = n + 2  # N setup + 1 retracement (current) + 1 for boundary
    if len(candles) < required:
        return None

    # The N acceleration candles (excluding the current retracement candle)
    setup_candles = list(candles[-(n + 1) : -1])
    retrace = candles[-1]

    # All must be same direction
    directions = [_direction_from_candle(c) for c in setup_candles]
    if None in directions or len(set(directions)) != 1:
        return None
    direction = directions[0]

    # Bodies must be non-decreasing (acceleration)
    bodies = [_body_size(c) for c in setup_candles]
    if not all(bodies[i] <= bodies[i + 1] for i in range(len(bodies) - 1)):
        return None

    # Volumes must be non-decreasing
    vols = [c.volume for c in setup_candles]
    if not all(vols[i] <= vols[i + 1] for i in range(len(vols) - 1)):
        return None

    # Retracement candle: body < 40% of last setup candle body
    last_body = bodies[-1]
    if last_body == 0.0:
        return None
    if _body_size(retrace) >= 0.4 * last_body:
        return None

    # Retracement volume lower than last setup candle
    if retrace.volume >= setup_candles[-1].volume:
        return None

    # Retracement direction must be opposite or doji
    retrace_dir = _direction_from_candle(retrace)
    if retrace_dir == direction:
        return None

    if direction is None:
        return None

    avg_body_growth = bodies[-1] / bodies[0] if bodies[0] > 0 else 1.0
    confidence = min(0.50 + avg_body_growth * 0.05 + n * 0.03, 0.95)

    return PatternResult(
        pattern=PatternType.MOMENTUM_CONTINUATION,
        direction=direction,
        confidence=round(confidence, 3),
        details={
            "setup_candles": n,
            "body_growth": round(avg_body_growth, 3),
            "retrace_body_ratio": round(_body_size(retrace) / last_body, 3),
        },
    )


# ---------------------------------------------------------------------------
# Pattern: anomaly_fade (CLAUDE.md §23)
# ---------------------------------------------------------------------------


@register("anomaly_fade")
def detect_anomaly_fade(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """
    Trigger: volume > vol_ma20 + stddev_multiplier × vol_stddev
             AND price move > ATR × anomaly_price_atr in single candle
    Action:  wait for reversal candle close → enter AGAINST spike direction
    """
    if len(candles) < 22:
        return None

    spike = candles[-2]
    reversal = candles[-1]

    volumes = [c.volume for c in candles[:-1]]
    vol_ma = compute_volume_ma(volumes, 20)
    vol_std = compute_volume_stddev(volumes, 20)
    atr = compute_atr(list(candles[:-1]), 14)

    if atr == 0.0:
        return None

    # Spike must exceed volume threshold
    vol_threshold = vol_ma + params.anomaly_volume_stddev * vol_std
    if spike.volume < vol_threshold:
        return None

    # Spike price move must exceed ATR threshold
    spike_move = abs(spike.close - spike.open)
    if spike_move < params.anomaly_price_atr * atr:
        return None

    spike_dir = _direction_from_candle(spike)
    if spike_dir is None:
        return None

    # Fade direction is opposite to spike
    fade_dir = Direction.SHORT if spike_dir is Direction.LONG else Direction.LONG

    # Reversal candle must confirm: close in fade direction vs spike close
    if fade_dir is Direction.SHORT and reversal.close >= spike.close:
        return None
    if fade_dir is Direction.LONG and reversal.close <= spike.close:
        return None

    vol_ratio = spike.volume / vol_ma if vol_ma > 0 else 1.0
    confidence = min(0.50 + (vol_ratio - 1.0) * 0.05, 0.95)

    return PatternResult(
        pattern=PatternType.ANOMALY_FADE,
        direction=fade_dir,
        confidence=round(confidence, 3),
        details={
            "spike_vol_ratio": round(vol_ratio, 3),
            "spike_move_atr": round(spike_move / atr, 3),
        },
    )
