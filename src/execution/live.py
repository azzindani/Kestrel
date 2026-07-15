"""
Layer 3 boundary — live execution engine.

⚠  HUMAN-ONLY MODULE — agent must NOT modify this file without explicit owner
   authorization. Controls real capital. All changes require human review
   (CLAUDE.md §3, §25).

   MAKER EXECUTION added 2026-07-15 under explicit owner directive ("we should
   have it. but i need you to prepare the codebases for prod") following the
   prod-readiness audit that identified taker-only live execution as the #1
   go-live blocker: the validated points-program edge is maker-dependent
   (RESEARCH_LOOP iter 56 — taker fees collapse 15/16 validated cells), and
   CLAUDE.md §13 already specifies the MAKER_EXECUTION=true path the simulator
   has implemented all along. This change brings live execution into parity
   with the spec and with src/execution/simulation.py, which is the semantic
   reference:

     entry                    — post-only limit AT the signal price (no
                                slippage, maker fee). Unfilled after the
                                timeout ⇒ cancel + ExecutionError
                                ("entry_unfilled") ⇒ the daemon logs
                                order_placement_failed and skips the trade
                                (a missed entry is never chased with a
                                market order).
     take-profit              — a resting post-only reduce-only limit at
                                tp_price placed immediately after the entry
                                fills (maker fee, no slippage). This is what
                                makes maker lift the WIN side.
     stop/timeout/manual/liq  — market out (taker fee). You cannot post-only
                                your way out of an adverse move.

   Race note: if the resting TP fills between candle closes and the monitor
   subsequently requests a close for a different reason, close_position()
   detects the filled TP and settles from its actual fill; the daemon's
   close_reason label may then say stop_loss/timeout while the economics are
   the TP fill — rare, self-correcting, and conservative in the DB (the label
   never overstates the win).

Implements ExecutionInterface for production trading (ENV=prod).
Uses ccxt to place real orders on the configured exchange.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import ccxt.async_support as ccxt

from src.config import AppConfig, Direction, Signal, compute_liquidation_price
from src.execution.interface import ExecutionError, ExecutionInterface

_TAKER_FEE_PCT = 0.04 / 100.0
_MAKER_FEE_PCT = 0.02 / 100.0  # post-only limit fills (CLAUDE.md §13 maker path)

# Entry-fill discipline for the maker path: how long a post-only entry limit may
# rest before it is cancelled (the signal is stale after that), and how often to
# poll its status. On 1m–1h candles a fill either happens near-immediately at the
# close price or the market has moved on.
_ENTRY_FILL_TIMEOUT_S = 90.0
_FILL_POLL_S = 3.0


class LiveExecution(ExecutionInterface):
    """Executes real orders on the configured exchange via ccxt.

    Spot isolated margin — uses the exchange's margin order API. Two cost
    paths, selected by cfg.maker_execution (same contract as the simulator):
    taker (default) = market orders; maker = post-only limit entry + resting
    post-only take-profit, market-out for every adverse exit. Idempotency is
    ensured by passing a client order ID derived from signal.ts + pair.
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
        # pair → resting take-profit order id (maker path). Rehydrated by
        # reconcile() on restart; the DB stays the authoritative position store.
        self._tp_orders: dict[str, str] = {}
        # pair → entry economics captured at fill time; the exchange's TP order
        # cannot tell us our entry price, so TP settlement reads it from here
        # (rehydrated from the live position by reconcile() after a restart).
        self._entry_meta: dict[str, dict[str, Any]] = {}

    # -----------------------------------------------------------------------
    # place_order
    # -----------------------------------------------------------------------

    async def place_order(self, signal: Signal) -> dict[str, Any]:
        """Place a leveraged isolated margin order (market or post-only limit)."""
        if self.cfg.maker_execution:
            return await self._place_order_maker(signal)
        return await self._place_order_taker(signal)

    async def _place_order_taker(self, signal: Signal) -> dict[str, Any]:
        """Original market-order entry (taker fee + real slippage)."""
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
        return self._entry_result(signal, order["id"], fill_price, signal.size_usdt, notional, fee_usdt)

    async def _place_order_maker(self, signal: Signal) -> dict[str, Any]:
        """Post-only limit entry at the signal price; resting TP on fill.

        Mirrors SimulationExecution's maker semantics: the entry fills at the
        price the signal computed (the candle close) with the maker fee and no
        slippage — or it does not fill at all and the trade is skipped.
        """
        side = "buy" if signal.direction is Direction.LONG else "sell"
        entry_price = signal.entry_price
        notional = signal.size_usdt * self.cfg.leverage
        qty = notional / entry_price

        client_order_id = f"kestrel-{signal.ts}-{signal.pair}"

        try:
            order = await self._exchange.create_order(
                symbol=signal.pair,
                type="limit",
                side=side,
                amount=qty,
                price=entry_price,
                params={
                    "clientOrderId": client_order_id,
                    "isIsolated": True,
                    "leverage": self.cfg.leverage,
                    "postOnly": True,
                },
            )
        except ccxt.BaseError as exc:
            raise ExecutionError(str(exc), {"pair": signal.pair, "side": side}) from exc

        order_id = order["id"]
        filled_qty, avg_price, final_status = await self._await_entry_fill(order_id, signal.pair)

        if filled_qty <= 0.0:
            raise ExecutionError(
                "entry_unfilled",
                {"pair": signal.pair, "side": side, "order_id": order_id, "status": final_status},
            )

        # Partial fill at timeout: the remainder is already cancelled by
        # _await_entry_fill — trade proceeds with the filled fraction only.
        fill_price = avg_price or entry_price
        filled_notional = filled_qty * fill_price
        size_usdt = filled_notional / self.cfg.leverage
        fee_usdt = filled_notional * _MAKER_FEE_PCT

        result = self._entry_result(signal, order_id, fill_price, size_usdt, filled_notional, fee_usdt)

        # Resting post-only take-profit for the filled quantity. Failure to
        # rest the TP is NOT a failed trade — the position monitor still
        # market-outs at the TP level (worse fee, same protection).
        tp_side = "sell" if signal.direction is Direction.LONG else "buy"
        try:
            tp_order = await self._exchange.create_order(
                symbol=signal.pair,
                type="limit",
                side=tp_side,
                amount=filled_qty,
                price=signal.tp_price,
                params={
                    "clientOrderId": f"{client_order_id}-tp",
                    "isIsolated": True,
                    "reduceOnly": True,
                    "postOnly": True,
                },
            )
            self._tp_orders[signal.pair] = tp_order["id"]
        except ccxt.BaseError:
            self._tp_orders.pop(signal.pair, None)

        return result

    async def _await_entry_fill(self, order_id: str, pair: str) -> tuple[float, Optional[float], str]:
        """Poll a resting entry order until filled, cancelled, or timed out.

        Returns (filled_qty, average_fill_price, final_status). On timeout the
        remainder is cancelled before returning whatever quantity has filled.
        """
        deadline = time.monotonic() + _ENTRY_FILL_TIMEOUT_S
        status = "open"
        while time.monotonic() < deadline:
            try:
                order = await self._exchange.fetch_order(order_id, pair)
            except ccxt.BaseError as exc:
                raise ExecutionError(str(exc), {"order_id": order_id, "pair": pair}) from exc
            status = str(order.get("status") or "open")
            filled = float(order.get("filled") or 0.0)
            if status == "closed":
                return filled, _avg_price(order), status
            if status in ("canceled", "rejected", "expired"):
                # Post-only orders that would cross are cancelled by the venue.
                return filled, _avg_price(order), status
            await asyncio.sleep(_FILL_POLL_S)

        # Timeout: cancel the remainder, then read the final fill state.
        try:
            await self._exchange.cancel_order(order_id, pair)
        except ccxt.OrderNotFound:
            pass
        except ccxt.BaseError as exc:
            raise ExecutionError(str(exc), {"order_id": order_id, "pair": pair}) from exc
        try:
            order = await self._exchange.fetch_order(order_id, pair)
            return float(order.get("filled") or 0.0), _avg_price(order), "timeout"
        except ccxt.BaseError:
            return 0.0, None, "timeout"

    def _entry_result(
        self,
        signal: Signal,
        order_id: str,
        fill_price: float,
        size_usdt: float,
        notional: float,
        fee_usdt: float,
    ) -> dict[str, Any]:
        liq_price = compute_liquidation_price(fill_price, signal.direction, self.cfg.leverage)
        self._entry_meta[signal.pair] = {
            "direction": signal.direction.value,
            "entry_price": fill_price,
            "notional_usdt": notional,
            "size_usdt": size_usdt,
        }
        return {
            "order_id": order_id,
            "pair": signal.pair,
            "direction": signal.direction.value,
            "entry_price": fill_price,
            "size_usdt": size_usdt,
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
        # Maker path: the resting TP may already have closed the position —
        # settle from its actual fill if so (maker fee, no slippage).
        tp_id = self._tp_orders.get(pair)
        if tp_id is not None:
            settled = await self._settle_if_tp_filled(pair, tp_id)
            if settled is not None:
                return settled
            # TP still resting: cancel it before the market close so the
            # reduce-only orders never race each other.
            try:
                await self._exchange.cancel_order(tp_id, pair)
            except ccxt.OrderNotFound:
                pass
            except ccxt.BaseError as exc:
                raise ExecutionError(str(exc), {"pair": pair, "reason": reason}) from exc
            self._tp_orders.pop(pair, None)

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
        self._entry_meta.pop(pair, None)
        return self._close_result(pos, fill_exit, fee_pct=_TAKER_FEE_PCT)

    async def _settle_if_tp_filled(self, pair: str, tp_id: str) -> Optional[dict[str, Any]]:
        """If the resting TP order has filled, settle the close from it."""
        try:
            tp_order = await self._exchange.fetch_order(tp_id, pair)
        except ccxt.OrderNotFound:
            self._tp_orders.pop(pair, None)
            return None
        except ccxt.BaseError as exc:
            raise ExecutionError(str(exc), {"pair": pair, "tp_order_id": tp_id}) from exc

        if str(tp_order.get("status")) != "closed":
            return None

        self._tp_orders.pop(pair, None)
        fill_exit = _avg_price(tp_order) or float(tp_order.get("price") or 0.0)
        meta = self._entry_meta.pop(pair, None)
        if meta is not None:
            pos = dict(meta)
        else:
            # Entry meta lost (restart between TP fill and settlement) — best
            # effort from the TP order itself; a sell TP closes a long. The DB
            # trade row keeps the authoritative entry for offline correction.
            qty = float(tp_order.get("filled") or tp_order.get("amount") or 0.0)
            notional = qty * fill_exit
            pos = {
                "direction": "long" if str(tp_order.get("side")) == "sell" else "short",
                "entry_price": 0.0,
                "notional_usdt": notional,
                "size_usdt": notional / max(self.cfg.leverage, 1),
            }
        return self._close_result(pos, fill_exit, fee_pct=_MAKER_FEE_PCT)

    def _close_result(self, pos: dict[str, Any], fill_exit: float, fee_pct: float) -> dict[str, Any]:
        notional = pos["notional_usdt"]
        entry = pos["entry_price"]
        fee_exit = notional * fee_pct

        if entry > 0.0:
            if pos["direction"] == "long":
                pnl_gross = (fill_exit - entry) / entry * notional
            else:
                pnl_gross = (entry - fill_exit) / entry * notional
        else:
            # Entry economics unavailable (TP settled after a restart without
            # reconcile context) — record fees only; the DB trade row keeps the
            # authoritative entry for offline correction.
            pnl_gross = 0.0

        pnl_net = pnl_gross - fee_exit
        size = pos.get("size_usdt") or 0.0
        pnl_pct = (pnl_net / size * 100.0) if size > 0 else 0.0

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
        """Fetch open positions and rehydrate resting-TP awareness on restart."""
        pos = await self.get_position(self.cfg.pair)
        if pos is not None and self.cfg.maker_execution:
            self._entry_meta[self.cfg.pair] = {
                "direction": pos["direction"],
                "entry_price": pos["entry_price"],
                "notional_usdt": pos["notional_usdt"],
                "size_usdt": pos["size_usdt"],
            }
            try:
                open_orders = await self._exchange.fetch_open_orders(self.cfg.pair)
                for o in open_orders:
                    params_reduce = o.get("reduceOnly") or o.get("info", {}).get("reduceOnly")
                    if str(o.get("type")) == "limit" and params_reduce:
                        self._tp_orders[self.cfg.pair] = o["id"]
                        break
            except ccxt.BaseError:
                pass  # position monitoring still protects the position
        return [pos] if pos else []

    # -----------------------------------------------------------------------
    # cleanup
    # -----------------------------------------------------------------------

    async def close(self) -> None:
        """Close the ccxt exchange connection."""
        await self._exchange.close()


def _avg_price(order: dict[str, Any]) -> Optional[float]:
    avg = order.get("average") or order.get("price")
    return float(avg) if avg else None
