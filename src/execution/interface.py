"""
Layer 3 boundary — execution interface contract.

Defines the abstract interface that both LiveExecution and SimulationExecution
must implement. Dependency injection at startup selects which implementation
is active (CLAUDE.md §14).

Public API (CLAUDE.md §8):
    place_order(signal) -> dict
    cancel_order(order_id, pair) -> bool
    get_position(pair) -> dict | None
    close_position(pair, reason) -> dict
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from src.config import Signal


class ExecutionInterface(ABC):
    """Abstract base for execution backends."""

    @abstractmethod
    async def place_order(self, signal: Signal) -> dict[str, Any]:
        """Place an order for the given signal.

        Returns an order dict with at minimum:
            {
                "order_id": str,
                "pair": str,
                "direction": str,
                "entry_price": float,
                "size_usdt": float,
                "tp_price": float,
                "sl_price": float,
                "leverage": int,
                "ts": int,          # unix ms fill time
                "fee_usdt": float,
                "notional_usdt": float,
                "liquidation_price": float,
            }
        Raises ExecutionError on failure.
        """

    @abstractmethod
    async def cancel_order(self, order_id: str, pair: str) -> bool:
        """Cancel a pending order. Returns True if cancelled, False if not found."""

    @abstractmethod
    async def get_position(self, pair: str) -> Optional[dict[str, Any]]:
        """Return current position for pair, or None if flat."""

    @abstractmethod
    async def close_position(self, pair: str, reason: str) -> dict[str, Any]:
        """Close an open position at market price.

        Returns:
            {
                "exit_price": float,
                "pnl_gross_usdt": float,
                "fee_exit_usdt": float,
                "pnl_net_usdt": float,
                "pnl_pct": float,
                "ts": int,
            }
        """

    @abstractmethod
    async def reconcile(self) -> list[dict[str, Any]]:
        """Return all currently open positions from the exchange/simulator.

        Used at startup to reconcile DB state vs actual state.
        Returns a list of position dicts (same schema as get_position).
        """


class ExecutionError(Exception):
    """Raised when an execution operation fails."""

    def __init__(self, message: str, payload: Optional[dict] = None) -> None:
        super().__init__(message)
        self.payload = payload or {}
