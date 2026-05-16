"""
Layer 3 boundary — Alpaca execution provider.

Implements ExecutionInterface for Alpaca Markets via the alpaca-py SDK.
Supports US equities and crypto (BTC/USD, ETH/USD, etc.).

Setup (.env):
    EXCHANGE=alpaca
    API_KEY=<APCA-API-KEY-ID>
    API_SECRET=<APCA-API-SECRET-KEY>
    TESTNET=true                       # true = paper trading, false = live
    PAIR=BTC/USD                       # Alpaca symbol format

Symbol format examples:
    Crypto : BTC/USD · ETH/USD · SOL/USD
    Stocks : AAPL · TSLA · SPY

Notes:
    - Alpaca crypto supports fractional positions and 24/7 trading
    - Stocks require market hours; extend-hours requires extra flags
    - Bracket orders handle TP and SL natively via take_profit / stop_loss params
    - Paper trading endpoint: https://paper-api.alpaca.markets
    - Live trading endpoint:  https://api.alpaca.markets
"""

from __future__ import annotations

import time
from typing import Any, Optional

from src.config import AppConfig, Direction, Signal, compute_liquidation_price
from src.execution.interface import ExecutionError, ExecutionInterface
from src.execution.providers import register_execution

_TAKER_FEE_PCT = 0.0015  # Alpaca crypto taker fee ~0.15%; stocks = 0


def _trading_client(cfg: AppConfig):
    """Construct an Alpaca TradingClient. Deferred import."""
    try:
        from alpaca.trading.client import TradingClient
    except ImportError as exc:
        raise ImportError(
            "alpaca-py is required for the Alpaca provider. "
            "Install with: pip install alpaca-py"
        ) from exc

    return TradingClient(
        api_key=cfg.api_key,
        secret_key=cfg.api_secret,
        paper=cfg.testnet,
    )


def _qty(size_usdt: float, leverage: int, price: float) -> str:
    """Convert USDT size → Alpaca quantity string (fractional OK for crypto)."""
    notional = size_usdt * leverage
    qty = notional / price
    # Round to 8 decimal places; Alpaca accepts fractional crypto quantities
    return f"{qty:.8f}"


@register_execution("alpaca")
class AlpacaExecution(ExecutionInterface):
    """Alpaca execution backend.

    Uses bracket orders (market entry with take-profit limit and stop-loss stop).
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._client = _trading_client(cfg)

    # ------------------------------------------------------------------
    # place_order
    # ------------------------------------------------------------------

    async def place_order(self, signal: Signal) -> dict[str, Any]:
        """Place a bracket market order on Alpaca."""
        try:
            from alpaca.trading.requests import (
                MarketOrderRequest,
                TakeProfitRequest,
                StopLossRequest,
            )
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
        except ImportError as exc:
            raise ImportError("alpaca-py required: pip install alpaca-py") from exc

        side = OrderSide.BUY if signal.direction is Direction.LONG else OrderSide.SELL
        qty = _qty(signal.size_usdt, self.cfg.leverage, signal.entry_price)

        slip = 0.0001
        fill_price = (
            signal.entry_price * (1.0 + slip)
            if signal.direction is Direction.LONG
            else signal.entry_price * (1.0 - slip)
        )

        order_request = MarketOrderRequest(
            symbol=signal.pair.replace("/", ""),
            qty=qty,
            side=side,
            time_in_force=TimeInForce.IOC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(signal.tp_price, 5)),
            stop_loss=StopLossRequest(stop_price=round(signal.sl_price, 5)),
        )

        try:
            order = self._client.submit_order(order_request)
        except Exception as exc:
            raise ExecutionError(f"Alpaca order failed: {exc}", {"pair": signal.pair}) from exc

        notional = signal.size_usdt * self.cfg.leverage
        fee = notional * _TAKER_FEE_PCT
        liq = compute_liquidation_price(fill_price, signal.direction, self.cfg.leverage)

        return {
            "order_id": str(order.id),
            "pair": signal.pair,
            "direction": signal.direction.value,
            "entry_price": round(fill_price, 8),
            "size_usdt": signal.size_usdt,
            "tp_price": signal.tp_price,
            "sl_price": signal.sl_price,
            "leverage": self.cfg.leverage,
            "ts": int(time.time() * 1000),
            "fee_usdt": round(fee, 6),
            "notional_usdt": round(notional, 4),
            "liquidation_price": round(liq, 8),
        }

    # ------------------------------------------------------------------
    # cancel_order
    # ------------------------------------------------------------------

    async def cancel_order(self, order_id: str, pair: str) -> bool:
        """Cancel a pending Alpaca order."""
        try:
            import uuid
            self._client.cancel_order_by_id(uuid.UUID(order_id))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # get_position
    # ------------------------------------------------------------------

    async def get_position(self, pair: str) -> Optional[dict[str, Any]]:
        """Fetch Alpaca position for the given symbol."""
        symbol = pair.replace("/", "")
        try:
            pos = self._client.get_open_position(symbol)
        except Exception:
            return None

        if pos is None:
            return None

        qty = float(pos.qty)
        if qty == 0:
            return None

        direction = "long" if qty > 0 else "short"
        avg_price = float(pos.avg_entry_price)
        notional = abs(qty) * avg_price
        unrealized_pnl = float(pos.unrealized_pl)

        return {
            "order_id": str(pos.asset_id),
            "pair": pair,
            "direction": direction,
            "entry_price": avg_price,
            "size_usdt": self.cfg.bucket_size_usdt,
            "tp_price": 0.0,
            "sl_price": 0.0,
            "leverage": self.cfg.leverage,
            "ts": int(time.time() * 1000),
            "fee_usdt": 0.0,
            "notional_usdt": round(notional, 4),
            "liquidation_price": compute_liquidation_price(
                avg_price,
                Direction(direction),
                self.cfg.leverage,
            ),
            "unrealized_pnl": unrealized_pnl,
        }

    # ------------------------------------------------------------------
    # close_position
    # ------------------------------------------------------------------

    async def close_position(self, pair: str, reason: str) -> dict[str, Any]:
        """Close all units of an Alpaca position at market."""
        pos = await self.get_position(pair)
        if pos is None:
            raise ExecutionError(f"No open position for {pair}", {"pair": pair})

        symbol = pair.replace("/", "")
        try:
            resp = self._client.close_position(symbol)
        except Exception as exc:
            raise ExecutionError(f"Alpaca close failed: {exc}", {"pair": pair}) from exc

        exit_price = float(getattr(resp, "filled_avg_price", None) or pos["entry_price"])

        direction = pos["direction"]
        entry = pos["entry_price"]
        notional = pos["notional_usdt"]
        slip = 0.0001
        exit_price_slipped = exit_price * (1.0 - slip) if direction == "long" else exit_price * (1.0 + slip)

        if direction == "long":
            pnl_gross = (exit_price_slipped - entry) / entry * notional
        else:
            pnl_gross = (entry - exit_price_slipped) / entry * notional

        fee_exit = notional * _TAKER_FEE_PCT
        pnl_net = pnl_gross - fee_exit
        pnl_pct = pnl_net / pos["size_usdt"] * 100.0

        return {
            "exit_price": round(exit_price_slipped, 8),
            "pnl_gross_usdt": round(pnl_gross, 6),
            "fee_exit_usdt": round(fee_exit, 6),
            "pnl_net_usdt": round(pnl_net, 6),
            "pnl_pct": round(pnl_pct, 4),
            "ts": int(time.time() * 1000),
        }

    # ------------------------------------------------------------------
    # reconcile
    # ------------------------------------------------------------------

    async def reconcile(self) -> list[dict[str, Any]]:
        """Return all open Alpaca positions."""
        try:
            positions = self._client.get_all_positions()
        except Exception as exc:
            raise ExecutionError(f"Alpaca reconcile failed: {exc}") from exc

        result = []
        for pos in positions:
            qty = float(pos.qty)
            if qty == 0:
                continue
            direction = "long" if qty > 0 else "short"
            avg_price = float(pos.avg_entry_price)
            notional = abs(qty) * avg_price
            result.append({
                "order_id": str(pos.asset_id),
                "pair": pos.symbol,
                "direction": direction,
                "entry_price": avg_price,
                "size_usdt": self.cfg.bucket_size_usdt,
                "tp_price": 0.0,
                "sl_price": 0.0,
                "leverage": self.cfg.leverage,
                "ts": int(time.time() * 1000),
                "fee_usdt": 0.0,
                "notional_usdt": round(notional, 4),
                "liquidation_price": compute_liquidation_price(
                    avg_price, Direction(direction), self.cfg.leverage
                ),
                "unrealized_pnl": float(pos.unrealized_pl),
            })
        return result
