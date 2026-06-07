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

import math
from typing import Optional, Sequence

from src.config import (
    Candle,
    Direction,
    Params,
    PatternResult,
    Rejection,
    Signal,
    SizingState,
    TradingSession,
    TrendResult,
    VolumeResult,
    get_trading_session,
    session_confidence_multiplier,
    session_volume_multiplier,
)
from src.signal.indicators import compute_ema, compute_rsi
from src.signal.memory import adjust_confidence, should_suppress
from src.signal.patterns import COUNTER_TREND_PATTERNS, registry
from src.signal.regime import classify_regime, regime_permits_pattern
from src.signal.sizing import cap_size_for_risk, compute_position_size


def _round_price(price: float) -> float:
    """Round a price keeping ~8 significant figures (and never fewer than 8
    decimal places). Fixed 8-dp rounding is fine for normal prices but quantizes
    sub-cent instruments (e.g. PEPE ~$1e-5) so coarsely that ATR-based TP/SL
    distances — and the TP/SL distance ratio risk Rule 3 checks — get corrupted.
    Adds precision below $1; a no-op at or above it."""
    if price <= 0.0:
        return round(price, 8)
    decimals = 7 - math.floor(math.log10(price))
    return round(price, max(decimals, 8))


# Keep a fixed-percent stop within this fraction of the liquidation distance
# (~1/leverage) so liquidation — a larger, uncontrolled loss — can never trigger
# before the intended stop. The 30% headroom also absorbs exit slippage.
_LIQ_SAFETY_FRACTION = 0.7


def compute_exit_prices(
    entry: float,
    direction: Direction,
    atr: Optional[float],
    params: Params,
    leverage: int,
) -> Optional[tuple[float, float]]:
    """Pure: return (tp_price, sl_price) for a candidate entry, or None if inputs
    are degenerate.

    Two modes (``params.tp_sl_pct_enabled``):
      * pct  — fixed reward:risk at ``tp_pct`` / ``sl_pct`` of entry price. The stop
        fraction is clamped to ``_LIQ_SAFETY_FRACTION / leverage`` so it always sits
        inside the liquidation distance (the 'mindful risk management' guard — a 5%
        stop at 20x would otherwise liquidate at ~4.5% first).
      * atr  — ATR-multiple distances (the original behaviour); requires a usable ATR.
    """
    if entry <= 0.0:
        return None

    if params.tp_sl_pct_enabled:
        sl_pct = params.sl_pct
        if leverage > 0:
            sl_pct = min(sl_pct, _LIQ_SAFETY_FRACTION / leverage)
        if sl_pct <= 0.0 or params.tp_pct <= 0.0:
            return None
        tp_dist = entry * params.tp_pct
        sl_dist = entry * sl_pct
    else:
        if atr is None or atr == 0.0:
            return None
        tp_dist = atr * params.tp_atr_multiplier
        sl_dist = atr * params.sl_atr_multiplier

    if direction is Direction.LONG:
        return entry + tp_dist, entry - sl_dist
    return entry - tp_dist, entry + sl_dist


# ---------------------------------------------------------------------------
# Pipeline stage: trend filter
# ---------------------------------------------------------------------------


def _trend_filter(candles: Sequence[Candle], params: Params) -> TrendResult | Rejection:
    """EMA cross + RSI band + EMA streak. Returns direction of the trend or Rejection."""
    if len(candles) < params.ema_slow + 1:
        return Rejection(stage="trend", reason="insufficient_candles")

    latest = candles[-1]
    closes = [c.close for c in candles]

    ema_fast = latest.ema9 if latest.ema9 is not None else compute_ema(closes, params.ema_fast)
    ema_slow = latest.ema21 if latest.ema21 is not None else compute_ema(closes, params.ema_slow)
    rsi = latest.rsi14 if latest.rsi14 is not None else compute_rsi(closes, 14)

    # RSI must be in momentum zone: not overbought for LONG, not oversold for SHORT
    if ema_fast > ema_slow and params.rsi_low <= rsi <= params.rsi_long_max:
        direction = Direction.LONG
    elif ema_fast < ema_slow and params.rsi_short_min <= rsi <= params.rsi_high:
        direction = Direction.SHORT
    else:
        return Rejection(stage="trend", reason="no_trend_alignment")

    # EMA trend must be established for at least 3 prior candles (prevents fresh-cross entries)
    if len(candles) >= 4:
        recent = candles[-4:-1]
        ema_pairs = [(c.ema9, c.ema21) for c in recent if c.ema9 is not None and c.ema21 is not None]
        if len(ema_pairs) >= 2:
            if direction is Direction.LONG and not all(e9 > e21 for e9, e21 in ema_pairs):
                return Rejection(stage="trend", reason="no_trend_streak")
            if direction is Direction.SHORT and not all(e9 < e21 for e9, e21 in ema_pairs):
                return Rejection(stage="trend", reason="no_trend_streak")

    return TrendResult(direction=direction, ema_fast=ema_fast, ema_slow=ema_slow, rsi=rsi)


# ---------------------------------------------------------------------------
# Pipeline stage: volume confirm
# ---------------------------------------------------------------------------


def _volume_confirm(candle: Candle, params: Params, session_vol_multiplier: float) -> VolumeResult | Rejection:
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
    trend_direction: Optional[Direction],
    pattern_memories: dict[str, dict | None],
    session: TradingSession,
    session_conf_multiplier: float,
) -> tuple[PatternResult, float] | Rejection:
    """
    Run all permitted patterns through the registry. Return (PatternResult, confidence)
    for the highest-confidence match, or Rejection if nothing fires.

    Trend-following patterns must agree with `trend_direction` (and are skipped when
    it is None, i.e. the trend filter rejected). Counter-trend patterns
    (COUNTER_TREND_PATTERNS) set their own direction and ignore the trend filter.
    """
    candidates = []
    for name, fn in registry.items():
        if name not in permitted_patterns:
            continue
        result = fn(candles, params)
        if result is None:
            continue
        if name not in COUNTER_TREND_PATTERNS:
            if trend_direction is None or result.direction != trend_direction:
                continue

        # Memory is keyed by the actual trade direction — for counter-trend
        # patterns that is the pattern's own (faded) direction, not the EMA trend.
        trade_dir = result.direction.value
        mem = pattern_memories.get(f"{name}:{trade_dir}")
        session_str = session.value
        regime_str = candles[-1].regime or "UNKNOWN"

        if should_suppress(name, trade_dir, session_str, regime_str, mem):
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
    enabled_patterns: Optional[Sequence[str]] = None,
    sizing_state: Optional[SizingState] = None,
    leverage: int = 0,
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
    # Use the candle's own timestamp for session detection so backtests
    # evaluate each historical candle in its correct trading session rather
    # than pinning every evaluation to the wall-clock session at run time.
    ts_now = latest.ts
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
    regime_patterns = frozenset(p for p in registry if regime_permits_pattern(regime_result.regime, p))
    permitted = session_permitted & regime_patterns if session_permitted is not None else regime_patterns

    # Per-bot strategy: narrow to this bot's enabled patterns (multi-bot bake-off)
    if enabled_patterns is not None:
        permitted = permitted & frozenset(enabled_patterns)

    # --- Stage 2: Trend (soft gate when a counter-trend pattern is enabled) ---
    trend_result = _trend_filter(candles, params)
    if isinstance(trend_result, Rejection):
        # Counter-trend patterns (e.g. wave_flip) deliberately trade against the
        # EMA trend, so a 'no trend alignment' rejection must not block them.
        if not (permitted & COUNTER_TREND_PATTERNS):
            return None, trend_result
        trend_direction: Optional[Direction] = None
        layer_trend = 0
    else:
        trend_direction = trend_result.direction
        layer_trend = 1

    # --- Stage 3: Pattern scan ---
    scan_result = _pattern_scan(
        candles,
        params,
        permitted,
        trend_direction,
        pattern_memories or {},
        session,
        session_conf_mult,
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
    direction = pattern_result.direction
    entry = latest.close

    # TP/SL: ATR-multiple or fixed-percent reward:risk (params.tp_sl_pct_enabled),
    # with the pct stop clamped inside the liquidation distance. Pure helper.
    exits = compute_exit_prices(entry, direction, latest.atr14, params, leverage)
    if exits is None:
        return None, Rejection(stage="pattern", reason="exit_prices_unavailable")
    tp_price, sl_price = exits

    # Position size — equity-scaled so positions compound with realised PnL
    # (signal/sizing.py). Confidence still sets the conviction band; sizing_state
    # supplies live bucket equity. None ⇒ legacy fixed $10/$5 bucket.
    # A size of 0 means the bucket is exhausted (bled below the viable floor).
    size_usdt = compute_position_size(adjusted_confidence, sizing_state, params)
    # Risk hardening: cap per-trade downside to a fixed fraction of bucket equity
    # so a single stop-out can't blow a large hole at high leverage (the "20% cut
    # loss" failure mode). Only active when the caller supplies leverage and
    # params.max_loss_pct_per_trade > 0; otherwise sizing is unchanged.
    if leverage > 0 and params.max_loss_pct_per_trade > 0.0:
        equity_ref = sizing_state.equity_usdt if sizing_state is not None else size_usdt
        size_usdt = cap_size_for_risk(
            size_usdt, equity_ref, entry, sl_price, leverage, params.max_loss_pct_per_trade
        )
    if size_usdt <= 0.0:
        return None, Rejection(stage="pattern", reason="bucket_exhausted")

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
        entry_price=_round_price(entry),
        tp_price=_round_price(tp_price),
        sl_price=_round_price(sl_price),
        size_usdt=size_usdt,
    )

    return signal, None
