"""
Layer 1 — signal detection pipeline.

Public API (CLAUDE.md §8):
    evaluate(candles, params, context) -> Signal | None

Pipeline (CLAUDE.md §22):
    candle_close
      → regime_filter  → RegimeResult | Rejection
      → trend_filter   → TrendResult  | Rejection
      → pattern_scan   → PatternResult | Rejection
      → volume_confirm → VolumeResult  | Rejection
      → build_signal   → Signal

Each stage returns a typed result or a typed Rejection.
Rejections are returned to the caller for logging; no exceptions for flow control.
"""
from __future__ import annotations

import time
from typing import Optional, Sequence

from src.config import (
    BucketState, Candle, Direction, Params, PatternType, Rejection,
    RegimeResult, Signal, TradingSession, TrendResult, VolumeResult,
    get_trading_session, session_confidence_multiplier, session_volume_multiplier,
)
from src.signal.indicators import compute_ema, compute_rsi
from src.signal.memory import adjust_confidence, should_suppress
from src.signal.patterns import registry
from src.signal.regime import classify_regime, regime_permits_pattern


# ---------------------------------------------------------------------------
# Pipeline stage: trend filter
# ---------------------------------------------------------------------------

def _trend_filter(
    candles: Sequence[Candle], params: Params
) -> TrendResult | Rejection:
    """EMA cross + RSI check. Returns direction of the trend or Rejection."""
    if len(candles) < params.ema_slow + 1:
        return Rejection(stage="trend", reason="insufficient_candles")

    latest = candles[-1]
    closes = [c.close for c in candles]

    ema_fast = latest.ema9 if latest.ema9 is not None else compute_ema(closes, params.ema_fast)
    ema_slow = latest.ema21 if latest.ema21 is not None else compute_ema(closes, params.ema_slow)
    rsi = latest.rsi14 if latest.rsi14 is not None else compute_rsi(closes, 14)

    if ema_fast > ema_slow and rsi >= params.rsi_low:
        direction = Direction.LONG
    elif ema_fast < ema_slow and rsi <= params.rsi_high:
        direction = Direction.SHORT
    else:
        return Rejection(stage="trend", reason="no_trend_alignment")

    return TrendResult(direction=direction, ema_fast=ema_fast, ema_slow=ema_slow, rsi=rsi)


# ---------------------------------------------------------------------------
# Pipeline stage: volume confirm
# ---------------------------------------------------------------------------

def _volume_confirm(
    candle: Candle, params: Params, session_vol_multiplier: float
) -> VolumeResult | Rejection:
    """Volume must exceed the session-adjusted threshold."""
    vol_ratio = candle.volume_ratio
    vol_ma20 = candle.volume_ma20

    if vol_ratio is None or vol_ma20 is None:
        return Rejection(stage="volume", reason="volume_indicators_missing")

    threshold = params.volume_ratio_min * session_vol_multiplier
    if vol_ratio < threshold:
        return Rejection(
            stage="volume",
            reason=f"volume_ratio_below_threshold:{vol_ratio:.3f}<{threshold:.3f}",
        )

    return VolumeResult(volume_ratio=vol_ratio, volume_ma20=vol_ma20)


# ---------------------------------------------------------------------------
# Pipeline stage: pattern scan
# ---------------------------------------------------------------------------

def _pattern_scan(
    candles: Sequence[Candle],
    params: Params,
    permitted_patterns: frozenset[str],
    trend_direction: Direction,
    pattern_memories: dict[str, dict | None],
    session: TradingSession,
    session_conf_multiplier: float,
) -> tuple["PatternResult", float] | Rejection:
    """
    Run all permitted patterns through the registry. Return (PatternResult, confidence)
    for the highest-confidence match that aligns with trend_direction, or Rejection if
    nothing fires.
    """
    candidates = []
    for name, fn in registry.items():
        if name not in permitted_patterns:
            continue
        result = fn(candles, params)
        if result is None:
            continue
        if result.direction != trend_direction:
            continue

        mem = pattern_memories.get(f"{name}:{trend_direction.value}")
        session_str = session.value
        regime_str = candles[-1].regime or "UNKNOWN"

        if should_suppress(name, trend_direction.value, session_str, regime_str, mem):
            continue

        raw_conf = result.confidence
        adjusted = adjust_confidence(raw_conf, mem)
        final_conf = round(adjusted / session_conf_multiplier, 3)  # penalise harder sessions

        candidates.append((final_conf, result))

    if not candidates:
        return Rejection(stage="pattern", reason="no_pattern_fired")

    # Return highest-confidence pattern
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1], candidates[0][0]  # (PatternResult, adjusted_confidence)


# ---------------------------------------------------------------------------
# Public: evaluate
# ---------------------------------------------------------------------------

def evaluate(
    candles: Sequence[Candle],
    params: Params,
    bot_id: str,
    session_id: str,
    env: str,
    pattern_memories: Optional[dict[str, dict | None]] = None,
) -> tuple[Signal, None] | tuple[None, Rejection]:
    """
    Run the full signal pipeline on a completed candle list.

    Args:
        candles:         Recent candle history. Last element is the just-closed candle.
        params:          Tunable parameters.
        bot_id:          Bot identity string.
        session_id:      Current daemon session identifier.
        env:             'dev' | 'prod'
        pattern_memories: Pre-loaded pattern memory rows keyed 'pattern:direction'.
                          Pass None or {} if unavailable.

    Returns:
        (Signal, None)     — pipeline passed; signal ready for risk validation
        (None, Rejection)  — pipeline rejected; caller logs and records
    """
    if not candles:
        return None, Rejection(stage="regime", reason="no_candles")

    latest = candles[-1]
    ts_now = int(time.time() * 1000)
    session = get_trading_session(ts_now)
    session_vol_mult = session_volume_multiplier(session)
    session_conf_mult = session_confidence_multiplier(session)

    # Overlap session: only compression_breakout allowed (CLAUDE.md §22)
    if session is TradingSession.OVERLAP:
        session_permitted = frozenset({"compression_breakout"})
    else:
        session_permitted = None  # determined by regime below

    # --- Stage 1: Regime ---
    regime_result = classify_regime(candles, params)
    if isinstance(regime_result, Rejection):
        return None, regime_result

    layer_regime = 1

    # Combine session and regime pattern restrictions
    regime_patterns = frozenset(
        p for p in registry
        if regime_permits_pattern(regime_result.regime, p)
    )
    permitted = (
        session_permitted & regime_patterns
        if session_permitted is not None
        else regime_patterns
    )

    # --- Stage 2: Trend ---
    trend_result = _trend_filter(candles, params)
    if isinstance(trend_result, Rejection):
        return None, trend_result

    layer_trend = 1

    # --- Stage 3: Pattern scan ---
    scan_result = _pattern_scan(
        candles, params, permitted,
        trend_result.direction,
        pattern_memories or {},
        session, session_conf_mult,
    )
    if isinstance(scan_result, Rejection):
        return None, scan_result

    pattern_result, adjusted_confidence = scan_result

    # Minimum confidence gate (session-adjusted)
    min_conf = params.min_confidence * session_conf_mult
    if adjusted_confidence < min_conf:
        return None, Rejection(
            stage="pattern",
            reason=f"confidence_below_min:{adjusted_confidence:.3f}<{min_conf:.3f}",
        )

    layer_momentum = 1

    # --- Stage 4: Volume confirm ---
    vol_result = _volume_confirm(latest, params, session_vol_mult)
    if isinstance(vol_result, Rejection):
        return None, vol_result

    layer_volume = 1

    # --- Stage 5: Build signal ---
    atr = latest.atr14
    if atr is None or atr == 0.0:
        return None, Rejection(stage="pattern", reason="atr_unavailable")

    direction = pattern_result.direction
    entry = latest.close

    if direction is Direction.LONG:
        tp_price = entry + atr * params.tp_atr_multiplier
        sl_price = entry - atr * params.sl_atr_multiplier
    else:
        tp_price = entry - atr * params.tp_atr_multiplier
        sl_price = entry + atr * params.sl_atr_multiplier

    # Size from confidence band (CLAUDE.md §22):
    #   ≥ 0.75 → full bucket ($10) · 0.55–0.74 → half bucket ($5)
    size_usdt = 10.0 if adjusted_confidence >= 0.75 else 5.0

    signal = Signal(
        bot_id=bot_id,
        session_id=session_id,
        env=env,
        ts=ts_now,
        pair=latest.pair,
        timeframe=latest.timeframe,
        candle_ts=latest.ts,
        pattern=pattern_result.pattern.value,
        direction=direction,
        confidence=adjusted_confidence,
        regime=regime_result.regime.value,
        layer_regime=layer_regime,
        layer_trend=layer_trend,
        layer_momentum=layer_momentum,
        layer_volume=layer_volume,
        layers_passed=4,
        entry_price=round(entry, 8),
        tp_price=round(tp_price, 8),
        sl_price=round(sl_price, 8),
        size_usdt=size_usdt,
    )

    return signal, None
