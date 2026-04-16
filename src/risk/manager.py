"""
Layer 1 — risk manager.

⚠  HUMAN-ONLY MODULE — agent must NOT modify this file after initial creation.
   Any change requires a human code review and CLAUDE.md update first (§4).

Public API (CLAUDE.md §8):
    validate(signal: Signal, state: BucketState) -> ValidationResult

Six hard validation rules (CLAUDE.md §24):
    1. active_positions < max_active_buckets
    2. liquidation_distance >= 1.5% from entry
    3. TP_dist / SL_dist >= 1.2
    4. expected_gross_profit > round_trip_fee × 1.5
    5. session_net_pnl > -5.00 USDT (resets 00:00 UTC)
    6. last_ws_reconnect > 60s ago (or never reconnected)
"""
from __future__ import annotations

from src.config import (
    AppConfig,
    BucketState,
    Direction,
    Signal,
    ValidationResult,
    compute_liquidation_price,
    round_trip_fee_pct,
)

# Maintenance margin rate for Binance spot margin BTC/ETH (CLAUDE.md §17)
_MAINTENANCE_MARGIN_RATE = 0.005

# Daily session loss limit in USDT (CLAUDE.md §24)
_DAILY_LOSS_LIMIT_USDT = -5.00

# Minimum R/R ratio (CLAUDE.md §24)
_MIN_RR = 1.2

# Minimum liquidation distance from entry (CLAUDE.md §24)
_MIN_LIQ_DISTANCE_PCT = 0.015  # 1.5%

# Fee viability multiplier (CLAUDE.md §24)
_FEE_VIABILITY_MULTIPLIER = 1.5

# Stale data window after WS reconnect (seconds — CLAUDE.md §16)
_WS_STALE_WINDOW_SEC = 60


def validate(signal: Signal, state: BucketState, cfg: AppConfig) -> ValidationResult:
    """
    Validate a signal against all six risk rules.

    Returns ValidationResult(passed=True, reason=None) on success.
    Returns ValidationResult(passed=False, reason=<code>) on first failure.

    All checks are applied in order; the first failure is returned immediately
    as they are all hard gates.
    """
    # --- Rule 1: bucket capacity ---
    if state.active_positions >= cfg.max_active_buckets:
        return ValidationResult(passed=False, reason="bucket_limit")

    # --- Rule 2: liquidation distance ---
    liq_price = compute_liquidation_price(
        signal.entry_price,
        signal.direction,
        cfg.leverage,
        _MAINTENANCE_MARGIN_RATE,
    )
    liq_distance = abs(signal.entry_price - liq_price) / signal.entry_price
    if liq_distance < _MIN_LIQ_DISTANCE_PCT:
        return ValidationResult(passed=False, reason="liquidation_too_close")

    # --- Rule 3: R/R ratio ---
    tp_dist = abs(signal.tp_price - signal.entry_price)
    sl_dist = abs(signal.sl_price - signal.entry_price)
    if sl_dist == 0.0:
        return ValidationResult(passed=False, reason="sl_distance_zero")
    rr = tp_dist / sl_dist
    if rr < _MIN_RR:
        return ValidationResult(passed=False, reason="rr_below_minimum")

    # --- Rule 4: fee viability ---
    tp_pct = tp_dist / signal.entry_price * 100.0
    fee_pct = round_trip_fee_pct()
    if tp_pct <= fee_pct * _FEE_VIABILITY_MULTIPLIER:
        return ValidationResult(passed=False, reason="fee_not_viable")

    # --- Rule 5: daily loss limit ---
    if state.session_net_pnl <= _DAILY_LOSS_LIMIT_USDT:
        return ValidationResult(passed=False, reason="daily_loss_limit")

    # --- Rule 6: stale data guard ---
    if state.last_ws_reconnect_ts is not None:
        elapsed_sec = (state.current_ts - state.last_ws_reconnect_ts) / 1000.0
        if elapsed_sec < _WS_STALE_WINDOW_SEC:
            return ValidationResult(passed=False, reason="stale_data")

    return ValidationResult(passed=True, reason=None)
