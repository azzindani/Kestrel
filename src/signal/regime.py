"""
Layer 1 — regime classification.

Public function:
    classify_regime(candles, params) -> RegimeResult | Rejection
"""

from __future__ import annotations

from typing import Sequence, Union

from src.config import Candle, Params, Regime, RegimeResult, Rejection
from src.signal.indicators import compute_adx, compute_atr


def classify_regime(candles: Sequence[Candle], params: Params) -> Union[RegimeResult, Rejection]:
    """Classify market regime from candle history.

    Returns RegimeResult on success, or Rejection with reason 'quiet_regime'
    when the QUIET regime is detected (all signals blocked).

    Regime precedence (CLAUDE.md §22):
        QUIET     — ATR14 < ATR50 × atr_quiet_multiplier  OR vol_ratio < 0.7
        VOLATILE  — ATR14 > ATR50 × atr_volatile_multiplier AND ADX > 15
        TRENDING  — ADX > adx_trend_min AND EMA spread > ema_spread_threshold
        RANGING   — default
    """
    if len(candles) < 2:
        return Rejection(stage="regime", reason="insufficient_candles")

    latest = candles[-1]

    # Use stored indicators when available; recompute when not
    atr14: float = latest.atr14 if latest.atr14 is not None else compute_atr(candles, 14)
    adx: float = latest.adx if latest.adx is not None else compute_adx(candles, 14)
    ema9: float = latest.ema9 if latest.ema9 is not None else _ema_from_closes(candles, 9)
    ema21: float = latest.ema21 if latest.ema21 is not None else _ema_from_closes(candles, 21)

    # ATR(50) requires at least 51 candles; degrade gracefully
    atr50 = compute_atr(candles, 50) if len(candles) >= 51 else atr14

    ema_spread = abs(ema9 - ema21) / ema21 if ema21 != 0.0 else 0.0

    # --- QUIET --- no signals should fire
    vol_ratio = latest.volume_ratio if latest.volume_ratio is not None else 1.0
    if atr14 < atr50 * params.atr_quiet_multiplier or vol_ratio < 0.7:
        return Rejection(stage="regime", reason="quiet_regime")

    # --- VOLATILE ---
    if atr14 > atr50 * params.atr_volatile_multiplier and adx > 15.0:
        regime = Regime.VOLATILE
    # --- TRENDING ---
    elif adx > params.adx_trend_min and ema_spread > params.ema_spread_threshold:
        regime = Regime.TRENDING
    # --- RANGING ---
    else:
        regime = Regime.RANGING

    return RegimeResult(
        regime=regime,
        adx=adx,
        ema_spread=ema_spread,
        atr14=atr14,
        atr50=atr50,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ema_from_closes(candles: Sequence[Candle], period: int) -> float:
    from src.signal.indicators import compute_ema

    closes = [c.close for c in candles]
    return compute_ema(closes, period)


def regime_permits_pattern(regime: Regime, pattern: str) -> bool:
    """Return True if the regime allows the given pattern (CLAUDE.md §22)."""
    _allowed: dict[Regime, frozenset[str]] = {
        Regime.TRENDING: frozenset({"impulse_retracement", "momentum_continuation"}),
        Regime.VOLATILE: frozenset({"compression_breakout", "anomaly_fade"}),
        Regime.RANGING: frozenset({"wick_rejection", "anomaly_fade"}),
        Regime.QUIET: frozenset(),
    }
    return pattern in _allowed.get(regime, frozenset())
