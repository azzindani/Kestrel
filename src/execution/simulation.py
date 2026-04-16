"""
Layer 3 boundary — simulation execution engine.

Implements ExecutionInterface for paper trading (ENV=dev).
Models isolated margin, taker fees, slippage, and liquidation exactly
as specified in CLAUDE.md §13, §17, §29.

Injected by the daemon when ENV=dev. Never touches a real exchange.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from src.config import (
    AppConfig, Direction, Signal,
    compute_liquidation_price, round_trip_fee_pct,
)
from src.execution.interface import ExecutionError, ExecutionInterface

_TAKER_FEE_PCT = 0.04 / 100.0   # 0.04% per side
_SLIPPAGE_PCT = 0.05 / 100.0     # 0.05% per side


class SimulationExecution(ExecutionInterface):
    """In-process paper trading engine.

    Positions are tracked in memory during a session; DB is the authoritative
    store (written by the daemon, not here).
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        # pair → position dict
        self._positions: dict[str, dict[str, Any]] = {}

    # -----------------------------------------------------------------------
    # place_order
    # -----------------------------------------------------------------------

    async def place_order(self, signal: Signal) -> dict[str, Any]:
        """Simulate a market order with taker fee and slippage."""
        if signal.pair in self._positions:
            raise ExecutionError(
                f"Position already open for {signal.pair}",
                {"pair": signal.pair},
            )

        slip = _SLIPPAGE_PCT
        if signal.direction is Direction.LONG:
            fill_price = signal.entry_price * (1.0 + slip)
        else:
            fill_price = signal.entry_price * (1.0 - slip)

        notional = signal.size_usdt * self.cfg.leverage
        fee_entry = notional * _TAKER_FEE_PCT
        liq_price = compute_liquidation_price(fill_price, signal.direction, self.cfg.leverage)

        order_id = str(uuid.uuid4())[:8]
        ts_ms = int(time.time() * 1000)

        position = {
            "order_id": order_id,
            "pair": signal.pair,
            "direction": signal.direction.value,
            "entry_price": round(fill_price, 8),
            "size_usdt": signal.size_usdt,
            "tp_price": signal.tp_price,
            "sl_price": signal.sl_price,
            "leverage": self.cfg.leverage,
            "ts": ts_ms,
            "fee_usdt": round(fee_entry, 6),
            "notional_usdt": round(notional, 4),
            "liquidation_price": round(liq_price, 8),
        }
        self._positions[signal.pair] = position
        return position

    # -----------------------------------------------------------------------
    # cancel_order
    # -----------------------------------------------------------------------

    async def cancel_order(self, order_id: str, pair: str) -> bool:
        """In simulation, orders fill immediately — nothing to cancel."""
        return False

    # -----------------------------------------------------------------------
    # get_position
    # -----------------------------------------------------------------------

    async def get_position(self, pair: str) -> Optional[dict[str, Any]]:
        return self._positions.get(pair)

    # -----------------------------------------------------------------------
    # close_position
    # -----------------------------------------------------------------------

    async def close_position(self, pair: str, reason: str) -> dict[str, Any]:
        """Close simulated position. Caller must provide current market price via
        update_price() before calling, or the entry price is used as a fallback."""
        pos = self._positions.get(pair)
        if pos is None:
            raise ExecutionError(f"No open position for {pair}", {"pair": pair})

        # Use the last recorded price for the pair (set externally by daemon)
        exit_price = self._prices.get(pair, pos["entry_price"])
        direction = pos["direction"]
        entry = pos["entry_price"]
        size = pos["size_usdt"]
        leverage = pos["leverage"]
        notional = pos["notional_usdt"]

        slip = _SLIPPAGE_PCT
        if direction == "long":
            fill_exit = exit_price * (1.0 - slip)
        else:
            fill_exit = exit_price * (1.0 + slip)

        fee_exit = notional * _TAKER_FEE_PCT

        if direction == "long":
            pnl_gross = (fill_exit - entry) / entry * notional
        else:
            pnl_gross = (entry - fill_exit) / entry * notional

        total_fee = pos["fee_usdt"] + fee_exit
        pnl_net = pnl_gross - total_fee
        pnl_pct = pnl_net / size * 100.0

        del self._positions[pair]

        return {
            "exit_price": round(fill_exit, 8),
            "pnl_gross_usdt": round(pnl_gross, 6),
            "fee_exit_usdt": round(fee_exit, 6),
            "pnl_net_usdt": round(pnl_net, 6),
            "pnl_pct": round(pnl_pct, 4),
            "ts": int(time.time() * 1000),
        }

    # -----------------------------------------------------------------------
    # reconcile
    # -----------------------------------------------------------------------

    async def reconcile(self) -> list[dict[str, Any]]:
        """Return all in-memory open positions (simulation: nothing persists across restarts)."""
        return list(self._positions.values())

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def __init__(self, cfg: AppConfig) -> None:  # noqa: F811
        self.cfg = cfg
        self._positions: dict[str, dict[str, Any]] = {}
        self._prices: dict[str, float] = {}

    def update_price(self, pair: str, price: float) -> None:
        """Record current market price for simulated TP/SL/close calculations."""
        self._prices[pair] = price

    def check_exits(self, pair: str) -> Optional[str]:
        """Check if TP or SL has been hit for an open position.

        Returns:
            'take_profit' | 'stop_loss' | 'liquidated' | None
        """
        pos = self._positions.get(pair)
        if pos is None:
            return None

        price = self._prices.get(pair)
        if price is None:
            return None

        direction = pos["direction"]
        liq = pos["liquidation_price"]

        if direction == "long":
            if price >= pos["tp_price"]:
                return "take_profit"
            if price <= pos["sl_price"]:
                return "stop_loss"
            if price <= liq:
                return "liquidated"
        else:
            if price <= pos["tp_price"]:
                return "take_profit"
            if price >= pos["sl_price"]:
                return "stop_loss"
            if price >= liq:
                return "liquidated"

        return None
