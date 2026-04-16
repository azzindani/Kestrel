"""
Layer 3 boundary — live execution engine.

⚠  HUMAN-ONLY MODULE — agent must NOT modify this file after initial creation.
   Controls real capital. All changes require human review (CLAUDE.md §3, §25).

Implements ExecutionInterface for production trading (ENV=prod).
Uses ccxt to place real orders on the configured exchange.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import ccxt.async_support as ccxt

from src.config import AppConfig, Direction, Signal, compute_liquidation_price
from src.execution.interface import ExecutionError, ExecutionInterface

_TAKER_FEE_PCT = 0.04 / 100.0


class LiveExecution(ExecutionInterface):
    """Executes real orders on the configured exchange via ccxt.

    Spot isolated margin — uses the exchange's margin order API.
    All orders are market orders (taker). Idempotency is ensured by
    passing a client order ID derived from signal.ts + pair.
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        exchange_cls = getattr(ccxt, cfg.exchange)
        self._exchange = exchange_cls(
            {
                "apiKey": cfg.api_key,
                "secret": cfg.api_secret,
                "options": {"defaultType": "margin"},
            }
        )
        if cfg.testnet:
            self._exchange.set_sandbox_mode(True)

    # -----------------------------------------------------------------------
    # place_order
    # -----------------------------------------------------------------------

    async def place_order(self, signal: Signal) -> dict[str, Any]:
        """Place a leveraged isolated margin market order."""
        side = "buy" if signal.direction is Direction.LONG else "sell"
        # Compute quantity in base currency from USDT notional
        ticker = await self._exchange.fetch_ticker(signal.pair)
        price = ticker["last"]
        notional = signal.size_usdt * self.cfg.leverage
        qty = notional / price

        client_order_id = f"kestrel-{signal.ts}-{signal.pair}"

        try:
            order = await self._exchange.create_order(
                symbol=signal.pair,
                type="market",
                side=side,
                amount=qty,
                params={
                    "clientOrderId": client_order_id,
                    "isIsolated": True,
                    "leverage": self.cfg.leverage,
                },
            )
        except ccxt.BaseError as exc:
            raise ExecutionError(str(exc), {"pair": signal.pair, "side": side}) from exc

        fill_price = float(order.get("average") or order.get("price") or price)
        fee_usdt = notional * _TAKER_FEE_PCT
        liq_price = compute_liquidation_price(fill_price, signal.direction, self.cfg.leverage)

        return {
            "order_id": order["id"],
            "pair": signal.pair,
            "direction": signal.direction.value,
            "entry_price": fill_price,
            "size_usdt": signal.size_usdt,
            "tp_price": signal.tp_price,
            "sl_price": signal.sl_price,
            "leverage": self.cfg.leverage,
            "ts": int(time.time() * 1000),
            "fee_usdt": round(fee_usdt, 6),
            "notional_usdt": round(notional, 4),
            "liquidation_price": round(liq_price, 8),
        }

    # -----------------------------------------------------------------------
    # cancel_order
    # -----------------------------------------------------------------------

    async def cancel_order(self, order_id: str, pair: str) -> bool:
        try:
            await self._exchange.cancel_order(order_id, pair)
            return True
        except ccxt.OrderNotFound:
            return False
        except ccxt.BaseError as exc:
            raise ExecutionError(str(exc), {"order_id": order_id, "pair": pair}) from exc

    # -----------------------------------------------------------------------
    # get_position
    # -----------------------------------------------------------------------

    async def get_position(self, pair: str) -> Optional[dict[str, Any]]:
        try:
            positions = await self._exchange.fetch_positions([pair])
            for p in positions:
                if float(p.get("contracts", 0) or 0) > 0:
                    return {
                        "pair": pair,
                        "direction": "long" if p["side"] == "long" else "short",
                        "entry_price": float(p["entryPrice"]),
                        "size_usdt": float(p["initialMargin"]),
                        "notional_usdt": float(p["notional"]),
                        "leverage": int(p["leverage"]),
                        "liquidation_price": float(p["liquidationPrice"] or 0),
                        "unrealised_pnl": float(p["unrealizedPnl"] or 0),
                    }
            return None
        except ccxt.BaseError as exc:
            raise ExecutionError(str(exc), {"pair": pair}) from exc

    # -----------------------------------------------------------------------
    # close_position
    # -----------------------------------------------------------------------

    async def close_position(self, pair: str, reason: str) -> dict[str, Any]:
        pos = await self.get_position(pair)
        if pos is None:
            raise ExecutionError(f"No open position for {pair}", {"pair": pair})

        close_side = "sell" if pos["direction"] == "long" else "buy"
        ticker = await self._exchange.fetch_ticker(pair)
        exit_price = float(ticker["last"])
        qty = pos["notional_usdt"] / exit_price

        try:
            order = await self._exchange.create_order(
                symbol=pair,
                type="market",
                side=close_side,
                amount=qty,
                params={"isIsolated": True, "reduceOnly": True},
            )
        except ccxt.BaseError as exc:
            raise ExecutionError(str(exc), {"pair": pair, "reason": reason}) from exc

        fill_exit = float(order.get("average") or order.get("price") or exit_price)
        notional = pos["notional_usdt"]
        entry = pos["entry_price"]
        fee_exit = notional * _TAKER_FEE_PCT

        if pos["direction"] == "long":
            pnl_gross = (fill_exit - entry) / entry * notional
        else:
            pnl_gross = (entry - fill_exit) / entry * notional

        pnl_net = pnl_gross - fee_exit
        pnl_pct = pnl_net / pos["size_usdt"] * 100.0

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
        """Fetch all open positions for the configured pair."""
        pos = await self.get_position(self.cfg.pair)
        return [pos] if pos else []

    # -----------------------------------------------------------------------
    # cleanup
    # -----------------------------------------------------------------------

    async def close(self) -> None:
        """Close the ccxt exchange connection."""
        await self._exchange.close()
