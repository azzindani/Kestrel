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

import datetime
from typing import Callable, Optional, Sequence

from src.config import Candle, Direction, Params, PatternResult, PatternType
from src.signal.indicators import compute_atr, compute_volume_ma, compute_volume_stddev

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PatternFn = Callable[[Sequence[Candle], Params], Optional[PatternResult]]

registry: dict[str, PatternFn] = {}

# Patterns whose entry deliberately OPPOSES the prevailing EMA trend (mean-reversion
# / "flip the position"). Kept as a named subset for clarity/back-compat.
COUNTER_TREND_PATTERNS: frozenset[str] = frozenset({"wave_flip"})

# Patterns that supply their OWN entry direction and therefore bypass the detector's
# trend-alignment gate (the RSI-band / EMA-streak trend filter). Every other pattern
# must agree with the trend filter. Two kinds live here:
#   * counter-trend mean-reversion (wave_flip) — trades AGAINST the EMA trend
#   * self-directing momentum (mom_adx, triple_mom) — trades the price-streak
#     direction inside a strong (high-ADX) move. These were validated WITHOUT the
#     RSI/EMA trend gate (which would drop the strongest, most overbought momentum
#     entries), so they self-direct to reproduce that validated behaviour.
SELF_DIRECTING_PATTERNS: frozenset[str] = COUNTER_TREND_PATTERNS | frozenset(
    {"mom_adx", "triple_mom", "session_seasonal", "macd_cross", "macd_rsi", "cci_mom", "sma_cross"}
)


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


def _streak_direction(candles: Sequence[Candle], n: int) -> Optional[Direction]:
    """Common direction of the last ``n`` candles, or None if shorter than ``n``,
    any candle is a doji, or they are not all the same direction."""
    if len(candles) < n:
        return None
    dirs = [_direction_from_candle(c) for c in candles[-n:]]
    if None in dirs or len(set(dirs)) != 1:
        return None
    return dirs[0]


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    """EMA series over ``values`` (seeded with the SMA of the first ``period``)."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    out = [ema]
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
        out.append(ema)
    return out


def _macd_lines(
    closes: Sequence[float], fast: int, slow: int, signal: int
) -> Optional[tuple[float, float, float, float]]:
    """MACD (macd_last, macd_prev, signal_last, signal_prev) from closes, or None.

    Candles store ema9/ema21 but not the MACD/signal lines, so compute inline (same
    method as scripts/algo_search.py so the live pattern matches the backtested one).
    """
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


def _sma(values: Sequence[float], n: int) -> Optional[float]:
    """Simple moving average of the last ``n`` values, or None if too few."""
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _cci_pair(candles: Sequence[Candle], period: int) -> Optional[tuple[float, float]]:
    """(cci_now, cci_prev) of the Commodity Channel Index over typical price, or None.

    Inline (same method as scripts/algo_search.py) so the live cci_mom pattern matches
    the backtested one. Candles don't store CCI; computed from high/low/close.
    """
    if len(candles) < period + 1:
        return None
    tp = [(float(c.high) + float(c.low) + float(c.close)) / 3.0 for c in candles]

    def _at(idx: int) -> float:
        window = tp[idx - period + 1 : idx + 1]
        sma = sum(window) / period
        mean_dev = sum(abs(x - sma) for x in window) / period
        return 0.0 if mean_dev == 0.0 else (tp[idx] - sma) / (0.015 * mean_dev)

    return (_at(len(candles) - 1), _at(len(candles) - 2))


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

    # Retracement candle must actually move against trigger (genuine pullback, not continuation)
    retrace_dir = _direction_from_candle(retrace)
    if direction is Direction.LONG and retrace_dir is Direction.LONG:
        return None
    if direction is Direction.SHORT and retrace_dir is Direction.SHORT:
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
    Long:  lower_wick > wick_ratio_min × body · close in top 30% of range · within 1 ATR of support
    Short: upper_wick > wick_ratio_min × body · close in bottom 30% of range · within 1 ATR of resistance
    Returns whichever direction qualifies (long checked first).
    """
    if len(candles) < 3:
        return None

    c = candles[-1]
    atr = compute_atr(candles, 14)
    if atr == 0.0:
        return None

    body = _body_size(c)
    total = _total_range(c)

    if body == 0.0 or total == 0.0:
        return None

    close_pos = (c.close - c.low) / total  # 0.0 = bottom, 1.0 = top

    # --- Long: lower wick rejection at support ---
    lower = _lower_wick(c)
    lower_wick_ratio = lower / body
    if lower_wick_ratio >= params.wick_ratio_min and close_pos >= 0.70:
        recent_lows = [x.low for x in candles[-11:-1]]
        if recent_lows and abs(c.low - min(recent_lows)) <= atr:
            confidence = min(0.45 + (lower_wick_ratio - params.wick_ratio_min) * 0.1 + close_pos * 0.15, 1.0)
            return PatternResult(
                pattern=PatternType.WICK_REJECTION,
                direction=Direction.LONG,
                confidence=round(confidence, 3),
                details={
                    "wick_ratio": round(lower_wick_ratio, 3),
                    "close_position": round(close_pos, 3),
                    "level": round(min(recent_lows), 2),
                },
            )

    # --- Short: upper wick rejection at resistance ---
    upper = _upper_wick(c)
    upper_wick_ratio = upper / body
    if upper_wick_ratio >= params.wick_ratio_min and close_pos <= 0.30:
        recent_highs = [x.high for x in candles[-11:-1]]
        if recent_highs and abs(c.high - max(recent_highs)) <= atr:
            confidence = min(0.45 + (upper_wick_ratio - params.wick_ratio_min) * 0.1 + (1.0 - close_pos) * 0.15, 1.0)
            return PatternResult(
                pattern=PatternType.WICK_REJECTION,
                direction=Direction.SHORT,
                confidence=round(confidence, 3),
                details={
                    "wick_ratio": round(upper_wick_ratio, 3),
                    "close_position": round(close_pos, 3),
                    "level": round(max(recent_highs), 2),
                },
            )

    return None


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


# ---------------------------------------------------------------------------
# Pattern: trend_momentum — permissive momentum entry
# ---------------------------------------------------------------------------


@register("trend_momentum")
def detect_trend_momentum(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """
    Permissive momentum entry: take a position in the established EMA-trend
    direction whenever the latest candle closes in that same direction with a
    non-trivial body. A simpler, higher-frequency complement to the five strict
    patterns (which almost never trigger on real 5m data — see signals table).

    Direction is derived from the EMA relationship so it always agrees with the
    detector's trend filter. This is a real (if unsophisticated) momentum signal,
    NOT a forced/always-fire trigger: it still requires trend alignment, a directional
    close, and a real body — and it remains subject to every downstream gate
    (volume confirm, min confidence, and all six risk rules incl. fee viability).
    """
    if len(candles) < params.ema_slow + 1:
        return None

    c = candles[-1]
    ema9 = c.ema9
    ema21 = c.ema21
    if ema9 is None or ema21 is None:
        return None

    if ema9 > ema21:
        direction = Direction.LONG
    elif ema9 < ema21:
        direction = Direction.SHORT
    else:
        return None

    # Candle must close in the trend direction (momentum confirmation, not a fade)
    if _direction_from_candle(c) is not direction:
        return None

    body_ratio = _body_ratio(c)
    if body_ratio < params.body_ratio_min:
        return None

    # Confidence scales with conviction (body ratio); capped below the 0.75
    # full-size band so these size at the conservative half-bucket by default.
    confidence = min(0.55 + body_ratio * 0.20, 0.72)

    return PatternResult(
        pattern=PatternType.MOMENTUM_CONTINUATION,
        direction=direction,
        confidence=round(confidence, 3),
        details={"variant": "trend_momentum", "body_ratio": round(body_ratio, 3)},
    )


# ---------------------------------------------------------------------------
# Wave strategy family — "surf the wave, flip when wrong"
#
# Three entry styles the daemon runs as separate bots (see build_wave_lab.py):
#   wave_ride  — ride an established trend wave after a shallow pullback (trend-aligned)
#   vol_burst  — enter ONLY while volatility is expanding, in the trend direction
#   wave_flip  — fade an exhausted extended run (COUNTER-TREND; COUNTER_TREND_PATTERNS)
#
# The patterns only decide entry + direction. The "ride it vs cut it" exit
# behaviour is expressed by each bot's TP/SL/hold param profile, not here.
# ---------------------------------------------------------------------------


@register("wave_ride")
def detect_wave_ride(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """Ride a trend wave on the resumption candle after a shallow pullback.

    Trend-aligned (EMA direction). Fires when the latest candle closes in the EMA
    direction with a real body AND at least one of the prior two candles closed
    against the trend (a minor pullback) — i.e. enter the continuation of the wave,
    not an extended blow-off. Meant to be held with a wide SL / far TP / long hold
    so the wave has room to run (the fix for the 73% premature stop-out).
    """
    if len(candles) < max(params.ema_slow + 1, 4):
        return None

    c = candles[-1]
    ema9, ema21 = c.ema9, c.ema21
    if ema9 is None or ema21 is None:
        return None

    if ema9 > ema21:
        direction = Direction.LONG
    elif ema9 < ema21:
        direction = Direction.SHORT
    else:
        return None

    # Resumption candle must close in the trend direction with conviction
    if _direction_from_candle(c) is not direction:
        return None
    br = _body_ratio(c)
    if br < params.body_ratio_min:
        return None

    # Require a shallow pullback in the prior two candles (don't chase extended runs)
    prior_dirs = [_direction_from_candle(p) for p in candles[-3:-1]]
    pulled_back = any(d is not None and d is not direction for d in prior_dirs)
    if not pulled_back:
        return None

    confidence = min(0.58 + br * 0.20, 0.80)
    return PatternResult(
        pattern=PatternType.MOMENTUM_CONTINUATION,
        direction=direction,
        confidence=round(confidence, 3),
        details={"variant": "wave_ride", "body_ratio": round(br, 3)},
    )


@register("vol_burst")
def detect_vol_burst(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """Enter in the trend direction only while volatility is EXPANDING.

    Fires when short-window ATR(5) exceeds long-window ATR(20) by
    atr_volatile_multiplier (a genuine volatility burst, where the move is large
    enough to clear the round-trip cost) and the latest candle closes in the
    EMA-trend direction with a real body. Trend-aligned. The 'selective scalp':
    high frequency during bursts, silent during chop.
    """
    if len(candles) < 22:
        return None

    c = candles[-1]
    ema9, ema21 = c.ema9, c.ema21
    if ema9 is None or ema21 is None:
        return None

    if ema9 > ema21:
        direction = Direction.LONG
    elif ema9 < ema21:
        direction = Direction.SHORT
    else:
        return None

    if _direction_from_candle(c) is not direction:
        return None

    atr5 = compute_atr(list(candles[-6:]), 5)
    atr20 = compute_atr(list(candles[-21:]), 20)
    if atr20 == 0.0:
        return None
    expansion = atr5 / atr20
    if expansion < params.atr_volatile_multiplier:
        return None

    br = _body_ratio(c)
    if br < params.body_ratio_min:
        return None

    confidence = min(0.55 + (expansion - params.atr_volatile_multiplier) * 0.10 + br * 0.10, 0.85)
    return PatternResult(
        pattern=PatternType.COMPRESSION_BREAKOUT,
        direction=direction,
        confidence=round(confidence, 3),
        details={"variant": "vol_burst", "atr_expansion": round(expansion, 3), "body_ratio": round(br, 3)},
    )


@register("wave_flip")
def detect_wave_flip(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """Fade an exhausted extended run — the 'flip the position' entry.

    After N consecutive same-direction candles (an extended wave), a candle that
    closes AGAINST the run with a real body signals exhaustion; enter in the
    OPPOSITE direction. This is COUNTER-TREND (see COUNTER_TREND_PATTERNS): the
    detector permits it to set its own direction rather than requiring EMA
    alignment. Captures "if the wave turns, ride it the other way."
    """
    n = max(params.momentum_acceleration_candles, 2)
    if len(candles) < n + 2:
        return None

    run = list(candles[-(n + 1) : -1])  # the extended run, excluding the current candle
    reversal = candles[-1]

    run_dirs = [_direction_from_candle(x) for x in run]
    if None in run_dirs or len(set(run_dirs)) != 1:
        return None
    run_dir = run_dirs[0]

    rev_dir = _direction_from_candle(reversal)
    if rev_dir is None or rev_dir is run_dir:
        return None  # current candle must close against the run (the reversal)

    rev_br = _body_ratio(reversal)
    if rev_br < params.body_ratio_min:
        return None

    fade_dir = rev_dir  # opposite of run_dir by construction
    confidence = min(0.55 + n * 0.04 + rev_br * 0.10, 0.80)
    return PatternResult(
        pattern=PatternType.ANOMALY_FADE,
        direction=fade_dir,
        confidence=round(confidence, 3),
        details={"variant": "wave_flip", "run_len": n, "rev_body_ratio": round(rev_br, 3)},
    )


# ---------------------------------------------------------------------------
# Confluence momentum family — "ride the strong trend"
#
# Handwritten multi-condition AND entries (the low-compute alternative to ML
# feature-combination). Validated on 4h walk-forward across 10 crypto pairs as
# the project's broadest positive result: mom_adx is net-positive on 10/10 pairs
# (clears the §30 OOS bar on ETH). Both are SELF_DIRECTING_PATTERNS — they take
# the price-streak direction inside a strong trend WITHOUT the RSI/EMA trend gate,
# which is exactly how they were validated. They still pass through every
# downstream gate (volume confirm, min confidence, QUIET-regime block, and all six
# risk rules incl. fee viability). Both read only stored Candle indicators (cheap).
# ---------------------------------------------------------------------------


@register("mom_adx")
def detect_mom_adx(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """Enter the direction of a 3-candle price streak inside a strong trend.

    Confluence: a 3-candle same-direction streak AND ADX > adx_strong_min (a
    genuinely strong directional move, not chop). Self-directing momentum.
    """
    if len(candles) < 3:
        return None

    c = candles[-1]
    if c.adx is None or c.adx <= params.adx_strong_min:
        return None

    direction = _streak_direction(candles, 3)
    if direction is None:
        return None

    # Confidence rises with trend strength above the floor; stays in the full-size
    # band (>= 0.75) the way the validation sized these (full bucket).
    confidence = min(0.78 + (c.adx - params.adx_strong_min) * 0.004, 0.92)

    return PatternResult(
        pattern=PatternType.MOMENTUM_CONTINUATION,
        direction=direction,
        confidence=round(confidence, 3),
        details={"variant": "mom_adx", "adx": round(c.adx, 2)},
    )


@register("triple_mom")
def detect_triple_mom(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """Strictest confluence momentum: streak + strong ADX + expanding volatility.

    Confluence: a 3-candle same-direction streak AND ADX > adx_strong_min AND ATR
    rising over the last ~6 candles (volatility expanding into the move, so it is
    more likely to clear the round-trip cost). Self-directing momentum.
    """
    if len(candles) < 7:
        return None

    c = candles[-1]
    past = candles[-7]
    if c.adx is None or c.adx <= params.adx_strong_min:
        return None
    if c.atr14 is None or past.atr14 is None or c.atr14 <= past.atr14:
        return None  # ATR must be rising = volatility expanding

    direction = _streak_direction(candles, 3)
    if direction is None:
        return None

    confidence = min(0.80 + (c.adx - params.adx_strong_min) * 0.004, 0.93)

    return PatternResult(
        pattern=PatternType.MOMENTUM_CONTINUATION,
        direction=direction,
        confidence=round(confidence, 3),
        details={"variant": "triple_mom", "adx": round(c.adx, 2)},
    )


# ---------------------------------------------------------------------------
# Pattern: macd_cross (trend-aligned MACD signal-line cross) — owner directive
# 2026-06-21 ("permitted to use indexes like macd, rsi"). This is the project's FIRST
# signal that is +EV in BOTH the recent year AND the untouched prior-year LOCKBOX (1h,
# maker fees; scripts/algo_search.py macd_cross_ct; see RESEARCH_LOOP iter 18). It is a
# live FORWARD-TEST of a modest lead (expR ~+0.13/+0.17, win <55%), deployed at 1h as a
# §13 research-comparison arm — NOT a validated edge, NOT the 5m live default. Trend-
# aligned: only take the signal cross on the correct side of zero (MACD>0 = uptrend).
# Self-directing; still passes volume-confirm, min-confidence, QUIET block + all risk rules.
# ---------------------------------------------------------------------------
@register("macd_cross")
def detect_macd_cross(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """Enter on a trend-aligned MACD signal-line cross (macd_cross_ct, validated 1h)."""
    if len(candles) < params.macd_slow + params.macd_signal + 1:
        return None
    m = _macd_lines([c.close for c in candles], params.macd_fast, params.macd_slow, params.macd_signal)
    if m is None:
        return None
    macd_last, macd_prev, sig_last, sig_prev = m

    direction: Optional[Direction] = None
    if macd_last > 0 and macd_prev <= sig_prev and macd_last > sig_last:
        direction = Direction.LONG  # MACD crosses up through signal, above zero (uptrend)
    elif macd_last < 0 and macd_prev >= sig_prev and macd_last < sig_last:
        direction = Direction.SHORT
    if direction is None:
        return None

    return PatternResult(
        pattern=PatternType.MOMENTUM_CONTINUATION,
        direction=direction,
        confidence=0.78,  # full-size band, matching how the momentum patterns are sized
        details={"variant": "macd_cross", "macd": round(macd_last, 8), "signal": round(sig_last, 8)},
    )


# ---------------------------------------------------------------------------
# Pattern: macd_rsi (MACD signal cross CONFIRMED by RSI-14) — research-loop iter 22.
# A second cross-era +EV 1h signal: the RAW MACD signal-line cross (NOT zero-aligned)
# gated by RSI-14 agreeing on the same side of its 50 centerline. The raw cross alone
# was data-mined (+recent/-lockbox); requiring RSI agreement filters the false crosses
# and makes it +EV in BOTH eras (1h maker: recent expR +0.06..0.09, lockbox +0.12,
# R/R 1.52, lockbox-positive on 5/6 pairs — same breadth as macd_cross, ~50% MORE
# trades; scripts/algo_search.py macd_rsi). Like macd_cross this is a live FORWARD-TEST
# of a modest lead (win <50%), at 1h as a §13 research-comparison arm — NOT a validated
# edge, NOT the 5m default. The RSI 50 centerline is definitional (like the MACD zero
# line in macd_cross), not a tunable. Self-directing; still clears volume/min-conf/risk.
# ---------------------------------------------------------------------------
@register("macd_rsi")
def detect_macd_rsi(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """Enter on a MACD signal-line cross confirmed by RSI-14 (macd_rsi, validated 1h)."""
    if len(candles) < params.macd_slow + params.macd_signal + 1:
        return None
    rsi = candles[-1].rsi14
    if rsi is None:
        return None
    m = _macd_lines([c.close for c in candles], params.macd_fast, params.macd_slow, params.macd_signal)
    if m is None:
        return None
    macd_last, macd_prev, sig_last, sig_prev = m

    direction: Optional[Direction] = None
    if macd_prev <= sig_prev and macd_last > sig_last and rsi > 50.0:
        direction = Direction.LONG  # MACD crosses up through signal, RSI confirms (>50)
    elif macd_prev >= sig_prev and macd_last < sig_last and rsi < 50.0:
        direction = Direction.SHORT
    if direction is None:
        return None

    return PatternResult(
        pattern=PatternType.MOMENTUM_CONTINUATION,
        direction=direction,
        confidence=0.78,  # full-size band, matching the other momentum patterns
        details={"variant": "macd_rsi", "macd": round(macd_last, 8), "rsi": round(rsi, 2)},
    )


# ---------------------------------------------------------------------------
# Pattern: cci_mom (CCI momentum breakout) — research-loop iter 31. The project's
# THIRD cross-era +EV 1h signal: enter when the Commodity Channel Index breaks OUT
# through its definitional ±100 level (a momentum-confirmation breakout, not a fade).
# +EV in BOTH recent and the untouched prior-year lockbox (1h maker: recent expR +0.12,
# lockbox +0.07, R/R 1.46, lockbox-positive on 4/6 pairs) and ~3x the activity of macd
# (scripts/algo_search.py cci_mom). Like the macd leads this is a live FORWARD-TEST of a
# modest lead (win <50%), at 1h as a §13 research-comparison arm — NOT a validated edge,
# NOT the 5m default. The ±100 level is definitional (like the MACD zero line), not a
# tunable. Self-directing; still clears volume/min-conf/risk.
# ---------------------------------------------------------------------------
@register("cci_mom")
def detect_cci_mom(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """Enter on a CCI momentum breakout through ±100 (cci_mom, validated 1h)."""
    if len(candles) < params.cci_period + 1:
        return None
    pair = _cci_pair(candles, params.cci_period)
    if pair is None:
        return None
    cci_now, cci_prev = pair

    direction: Optional[Direction] = None
    if cci_prev <= 100.0 and cci_now > 100.0:
        direction = Direction.LONG  # breaks out above +100 into strong up-momentum
    elif cci_prev >= -100.0 and cci_now < -100.0:
        direction = Direction.SHORT
    if direction is None:
        return None

    return PatternResult(
        pattern=PatternType.MOMENTUM_CONTINUATION,
        direction=direction,
        confidence=0.78,  # full-size band, matching the other momentum patterns
        details={"variant": "cci_mom", "cci": round(cci_now, 2)},
    )


# ---------------------------------------------------------------------------
# Pattern: sma_cross (9/21 simple-MA golden/death cross) — research-loop iter 32.
# The project's FOURTH cross-era +EV 1h signal. Of 14 undeployed breakout/momentum
# algos swept at 1h (maker), it was the ONLY one +EV in BOTH the recent year AND the
# untouched prior-year lockbox: recent expR +0.14 (net +$4.01, R/R 1.48, n=466), lockbox
# expR +0.12 (net +$3.92, R/R 1.32, n=535), OOS>IS in both, and +EV on ETH/DOGE/XRP in
# BOTH eras (≥3-pair breadth gate). Every other breakout (ema_cross, donch_break,
# breakout_vol…) was the SAME shape as the macd leads' false kin — strong recent, NEGATIVE
# lockbox = data-mined. SMA(9)/SMA(21) is distinct from MACD's EMA(12/26) signal-cross and
# fires on different bars. Like the other 1h leads this is a live FORWARD-TEST of a modest
# lead (win <50%, BTC negative), at 1h as a §13 research-comparison arm — NOT a validated
# edge, NOT the 5m default. Computed inline (same method as scripts/algo_search.py
# _make_ma_cross) so the live pattern matches the backtested one. Self-directing.
# ---------------------------------------------------------------------------
@register("sma_cross")
def detect_sma_cross(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """Enter on a fast/slow SMA golden/death cross (sma_cross 9/21, validated 1h)."""
    fast, slow = params.sma_cross_fast, params.sma_cross_slow
    if len(candles) < slow + 1:
        return None
    closes = [c.close for c in candles]
    f_now, f_prev = _sma(closes, fast), _sma(closes[:-1], fast)
    s_now, s_prev = _sma(closes, slow), _sma(closes[:-1], slow)
    if None in (f_now, f_prev, s_now, s_prev):
        return None
    assert f_now is not None and f_prev is not None and s_now is not None and s_prev is not None

    direction: Optional[Direction] = None
    if f_prev <= s_prev and f_now > s_now:
        direction = Direction.LONG  # fast SMA crosses up through slow (golden cross)
    elif f_prev >= s_prev and f_now < s_now:
        direction = Direction.SHORT
    if direction is None:
        return None

    return PatternResult(
        pattern=PatternType.MOMENTUM_CONTINUATION,
        direction=direction,
        confidence=0.78,  # full-size band, matching the other momentum patterns
        details={"variant": "sma_cross", "sma_fast": round(f_now, 8), "sma_slow": round(s_now, 8)},
    )


# ---------------------------------------------------------------------------
# Pattern: session_seasonal (time-of-day / overnight seasonality)
# ---------------------------------------------------------------------------
@register("session_seasonal")
def detect_session_seasonal(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    """Seasonal long: enter LONG when the candle's UTC hour is in the validated window.

    scripts/backtest_seasonality.py found the US-afternoon/overnight block
    (≈18:00–00:00 UTC, params.seasonal_entry_hour_start + window_hours) net-MAKER-positive
    in BOTH a recent window and an untouched prior-year lockbox across 6 pairs — the
    project's first lockbox-surviving signal. It is razor-thin and maker-only (negative at
    taker), so this is a live FORWARD-TEST cohort arm, NOT a validated edge — do not promote
    to baseline on its basis alone.

    Self-directing (always long). The seasonal effect is a small drift over a few hours, so
    the exit should be TIME-BASED — run it with a short max_hold and wide TP/SL (trailing off)
    so the timeout, not the ATR bracket, captures the window. Modest confidence → half-bucket
    sizing (honest under-sizing for a marginal signal).
    """
    if not candles:
        return None
    c = candles[-1]
    hour = datetime.datetime.fromtimestamp(c.ts / 1000, datetime.timezone.utc).hour
    start = params.seasonal_entry_hour_start
    window = params.seasonal_entry_window_hours
    # Fire only inside [start, start+window) on a 24h wrap.
    if (hour - start) % 24 >= window:
        return None

    return PatternResult(
        pattern=PatternType.SESSION_SEASONAL,
        direction=Direction.LONG,
        confidence=0.65,  # half-conviction band — marginal, maker-only signal
        details={"utc_hour": hour, "window_start": start, "window_hours": window},
    )
