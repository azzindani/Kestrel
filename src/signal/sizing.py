"""
Layer 1 — equity-scaled position sizing (capital management).

Pure logic: given the current bucket equity, the signal confidence, and the
tunable sizing params, return the USDT size for the next position. Replaces the
old fixed $10/$5 bucket so positions COMPOUND with realised PnL — bet more as the
balance grows, less as it shrinks — which is what makes the account scalable.

Real-trader behaviours encoded here (CLAUDE.md §17 capital model, equity-scaled):
    1. Equity-scaled sizing   — size = current equity × confidence fraction
    2. Confidence weighting    — full vs half fraction by conviction band
    3. Drawdown de-risking     — cut size while equity is below its peak
    4. Consecutive-loss cooloff— cut size after a losing streak
    5. Min-size floor          — below it the bucket is exhausted → size 0 (stop)

A None SizingState falls back to the legacy fixed bucket (backward compatible).
"""

from __future__ import annotations

from typing import Optional

from src.config import Params, SizingState

# Legacy fixed-bucket sizes (CLAUDE.md §22) used when no equity context is supplied.
_FIXED_FULL_USDT = 10.0
_FIXED_HALF_USDT = 5.0
_FULL_CONVICTION = 0.75


def compute_position_size(
    confidence: float,
    state: Optional[SizingState],
    params: Params,
) -> float:
    """Return the position size in USDT for the next trade.

    Args:
        confidence: adjusted signal confidence (0..1).
        state:      current bucket equity state; None ⇒ legacy fixed bucket.
        params:     tunable sizing params.

    Returns:
        Size in USDT, rounded to 2 dp. 0.0 means "do not trade" (bucket exhausted).
    """
    full = confidence >= _FULL_CONVICTION

    # Legacy fixed-bucket fallback — preserves the original $10/$5 behaviour.
    if state is None:
        return _FIXED_FULL_USDT if full else _FIXED_HALF_USDT

    equity = max(state.equity_usdt, 0.0)
    if equity <= 0.0:
        return 0.0

    # 1+2: equity-scaled, confidence-weighted base size.
    frac = params.size_fraction_full if full else params.size_fraction_half
    size = equity * frac

    # 3: drawdown de-risking — shrink while below the equity peak.
    if state.peak_equity_usdt > 0.0:
        drawdown = (state.peak_equity_usdt - equity) / state.peak_equity_usdt
        if drawdown >= params.drawdown_derisk_threshold:
            size *= params.drawdown_derisk_factor

    # 4: consecutive-loss cool-off.
    if state.consec_losses >= params.consec_loss_cooloff:
        size *= params.consec_loss_factor

    # Never deploy more margin than the bucket holds.
    size = min(size, equity)

    # 5: min-size floor / bucket-exhaustion stop.
    if size < params.size_min_usdt:
        # Bump to the floor only if the bucket can still cover it; otherwise stop.
        return params.size_min_usdt if equity >= params.size_min_usdt else 0.0

    return round(size, 2)


def cap_size_for_risk(
    size_usdt: float,
    equity_usdt: float,
    entry: float,
    sl_price: float,
    leverage: int,
    max_loss_pct: float,
) -> float:
    """Shrink position size so a stop-out loses at most ``max_loss_pct`` of equity.

    Risk hardening (fixed-fractional risk). At leverage ``L`` a position of margin
    ``size`` carries notional ``size × L``; if the stop sits ``d = |entry-sl|/entry``
    away, hitting it loses ``notional × d = size × L × d``. Bounding that loss at
    ``max_loss_pct × equity`` gives::

        size ≤ max_loss_pct × equity / (L × d)

    This caps per-trade downside regardless of how wide the ATR stop is or how high
    the leverage — the fix for "a single stop-out costs 20%+ of the bucket" at high
    leverage. Returns the input unchanged when disabled (max_loss_pct ≤ 0) or inputs
    are degenerate. Pure function.
    """
    if max_loss_pct <= 0.0 or leverage <= 0 or entry <= 0.0 or equity_usdt <= 0.0:
        return size_usdt
    sl_dist_pct = abs(entry - sl_price) / entry
    if sl_dist_pct <= 0.0:
        return size_usdt
    cap = (max_loss_pct * equity_usdt) / (leverage * sl_dist_pct)
    return round(min(size_usdt, cap), 2)
