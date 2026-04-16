"""
Layer 1 — backtest runner.

Simulates the full signal pipeline on historical OHLCV data.
Applies fees + slippage identically to the live simulation engine.

Walk-forward protocol (CLAUDE.md §30):
    train_frac = 0.60  (in-sample)
    test_frac  = 0.40  (out-of-sample)

Usage:
    results = run_backtest(candles, params, cfg)
    metrics = compute_metrics(results["trades"])
"""

from __future__ import annotations

from typing import Any, Sequence

from src.config import (
    AppConfig,
    BucketState,
    Candle,
    Direction,
    Params,
    compute_liquidation_price,
)
from src.risk.manager import validate
from src.signal.detector import evaluate

_TAKER_FEE_PCT = 0.04 / 100.0
_SLIPPAGE_PCT = 0.05 / 100.0
_MAINTENANCE_MARGIN_RATE = 0.005


def run_backtest(
    candles: Sequence[Candle],
    params: Params,
    cfg: AppConfig,
    bot_id: str = "backtest-bot",
    session_id: str = "backtest-session",
    min_candles_warmup: int = 60,
) -> dict[str, Any]:
    """Run a full simulation backtest on a candle series.

    Args:
        candles:            Full historical candle series (with indicators).
        params:             Strategy parameters.
        cfg:                App config (for leverage, bucket sizes etc).
        bot_id:             Bot identifier string.
        session_id:         Session identifier string.
        min_candles_warmup: Number of leading candles to skip (indicator warm-up).

    Returns:
        {
            "trades": list[dict],   # all closed trade dicts
            "signals": list[dict],  # all signal evaluations
            "equity_curve": list[float],
        }
    """
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    equity = 0.0
    equity_curve = [0.0]

    open_trade: dict[str, Any] | None = None
    candle_hold_count = 0
    session_pnl = 0.0
    session_reset_ts = _utc_midnight_ms(candles[0].ts) if candles else 0

    for i in range(min_candles_warmup, len(candles)):
        candle = candles[i]
        window = list(candles[max(0, i - 119) : i + 1])

        # Reset daily session PnL at UTC midnight
        midnight = _utc_midnight_ms(candle.ts)
        if midnight > session_reset_ts:
            session_pnl = 0.0
            session_reset_ts = midnight

        # --- Monitor open position ---
        if open_trade is not None:
            candle_hold_count += 1
            exit_reason = _check_exit(open_trade, candle)
            timeout = candle_hold_count >= params.max_hold_candles

            if exit_reason or timeout:
                reason = exit_reason or "timeout"
                close_result = _simulate_close(open_trade, candle, reason)
                session_pnl += close_result["pnl_net_usdt"]
                equity += close_result["pnl_net_usdt"]
                equity_curve.append(equity)

                closed = {**open_trade, **close_result, "close_reason": reason}
                trades.append(closed)
                open_trade = None
                candle_hold_count = 0
            else:
                equity_curve.append(equity)
                continue

        # --- Signal evaluation ---
        state = BucketState(
            active_positions=1 if open_trade else 0,
            last_ws_reconnect_ts=None,
            session_net_pnl=session_pnl,
            current_ts=candle.ts,
        )

        signal, rejection = evaluate(window, params, bot_id, session_id, cfg.env.value)

        if rejection is not None:
            signals.append(
                {
                    "ts": candle.ts,
                    "outcome": "rejected",
                    "reason": rejection.reason,
                    "stage": rejection.stage,
                }
            )
            equity_curve.append(equity)
            continue

        assert signal is not None  # guaranteed: evaluate returns exactly one of signal/rejection
        validation = validate(signal, state, cfg)
        if not validation.passed:
            signals.append(
                {
                    "ts": candle.ts,
                    "outcome": "rejected",
                    "reason": validation.reason,
                    "stage": "risk",
                }
            )
            equity_curve.append(equity)
            continue

        # --- Open position ---
        slip = _SLIPPAGE_PCT
        if signal.direction is Direction.LONG:
            fill_price = signal.entry_price * (1.0 + slip)
        else:
            fill_price = signal.entry_price * (1.0 - slip)

        notional = signal.size_usdt * cfg.leverage
        fee_entry = notional * _TAKER_FEE_PCT
        liq_price = compute_liquidation_price(fill_price, signal.direction, cfg.leverage)

        open_trade = {
            "bot_id": bot_id,
            "session_id": session_id,
            "env": cfg.env.value,
            "pair": signal.pair,
            "timeframe": signal.timeframe,
            "direction": signal.direction.value,
            "pattern": signal.pattern,
            "entry_ts": candle.ts,
            "entry_price": fill_price,
            "tp_price": signal.tp_price,
            "sl_price": signal.sl_price,
            "liquidation_price": liq_price,
            "bucket_id": 1,
            "size_usdt": signal.size_usdt,
            "leverage": cfg.leverage,
            "notional_usdt": notional,
            "fee_entry_usdt": fee_entry,
            "bucket_balance_before": 10.0,
        }
        candle_hold_count = 0

        signals.append(
            {
                "ts": candle.ts,
                "outcome": "fired",
                "pattern": signal.pattern,
                "direction": signal.direction.value,
                "confidence": signal.confidence,
            }
        )
        equity_curve.append(equity)

    # Close any remaining open trade at last candle
    if open_trade and len(candles) > min_candles_warmup:
        last = candles[-1]
        close_result = _simulate_close(open_trade, last, "timeout")
        equity += close_result["pnl_net_usdt"]
        trades.append({**open_trade, **close_result, "close_reason": "timeout"})

    return {
        "trades": trades,
        "signals": signals,
        "equity_curve": equity_curve,
    }


def walk_forward(
    candles: Sequence[Candle],
    params: Params,
    cfg: AppConfig,
    train_frac: float = 0.60,
) -> dict[str, Any]:
    """Run walk-forward validation: train on first 60%, test on remaining 40%.

    Returns:
        {
            "in_sample":  metrics dict (training window)
            "out_sample": metrics dict (test window)
            "trades_out": list of trade dicts from test window
        }
    """
    from src.backtest.metrics import compute_metrics

    split = int(len(candles) * train_frac)
    train_candles = candles[:split]
    test_candles = candles[split:]

    train_result = run_backtest(train_candles, params, cfg)
    test_result = run_backtest(test_candles, params, cfg)

    return {
        "in_sample": compute_metrics(train_result["trades"]),
        "out_sample": compute_metrics(test_result["trades"]),
        "trades_out": test_result["trades"],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_exit(trade: dict, candle: Candle) -> str | None:
    """Return close reason if TP/SL/liquidation is hit, else None."""
    direction = trade["direction"]
    high = candle.high
    low = candle.low

    if direction == "long":
        if high >= trade["tp_price"]:
            return "take_profit"
        if low <= trade["sl_price"]:
            return "stop_loss"
        if low <= trade["liquidation_price"]:
            return "liquidated"
    else:
        if low <= trade["tp_price"]:
            return "take_profit"
        if high >= trade["sl_price"]:
            return "stop_loss"
        if high >= trade["liquidation_price"]:
            return "liquidated"
    return None


def _simulate_close(trade: dict, candle: Candle, reason: str) -> dict:
    """Compute close result for a simulated trade."""
    direction = trade["direction"]
    entry = trade["entry_price"]
    notional = trade["notional_usdt"]
    size = trade["size_usdt"]

    # Use TP/SL price if hit, otherwise candle close with slippage
    if reason == "take_profit":
        raw_exit = trade["tp_price"]
    elif reason in ("stop_loss", "liquidated"):
        raw_exit = trade["sl_price"] if reason == "stop_loss" else trade["liquidation_price"]
    else:
        raw_exit = candle.close

    slip = _SLIPPAGE_PCT
    if direction == "long":
        exit_price = raw_exit * (1.0 - slip)
        pnl_gross = (exit_price - entry) / entry * notional
    else:
        exit_price = raw_exit * (1.0 + slip)
        pnl_gross = (entry - exit_price) / entry * notional

    fee_exit = notional * _TAKER_FEE_PCT
    total_fee = trade["fee_entry_usdt"] + fee_exit
    pnl_net = pnl_gross - total_fee
    pnl_pct = pnl_net / size * 100.0

    hold_candles = int(
        (candle.ts - trade["entry_ts"]) // (300_000 if "5m" in trade.get("timeframe", "5m") else 900_000)
    )

    return {
        "exit_ts": candle.ts,
        "exit_price": round(exit_price, 8),
        "hold_candles": max(hold_candles, 1),
        "pnl_gross_usdt": round(pnl_gross, 6),
        "fee_exit_usdt": round(fee_exit, 6),
        "pnl_net_usdt": round(pnl_net, 6),
        "pnl_pct": round(pnl_pct, 4),
        "bucket_balance_after": round(10.0 + pnl_net, 4),
    }


def _utc_midnight_ms(ts_ms: int) -> int:
    """Return the Unix ms timestamp of UTC midnight for the given ts."""
    day_ms = 86_400_000
    return (ts_ms // day_ms) * day_ms
