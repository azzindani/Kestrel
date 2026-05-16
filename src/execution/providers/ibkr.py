"""
Layer 3 boundary — Interactive Brokers (IBKR) execution provider.

Implements ExecutionInterface using ib_insync, an asyncio-native wrapper
around the official IB API.

Prerequisites:
    - IB Gateway or TWS running locally with API access enabled
    - "Enable ActiveX and Socket Clients" checked in TWS/Gateway settings
    - ib_insync installed: pip install ib_insync

Setup (.env):
    EXCHANGE=ibkr
    API_KEY=127.0.0.1:7497          # TWS host:port (7497=live, 7496=paper in TWS;
                                     # 4001=live, 4002=paper in Gateway)
    API_SECRET=1                     # IB client ID (integer, unique per connection)
    TESTNET=true                     # true = paper account, false = live
    PAIR=BTC.USD                     # IBKR contract format (see below)

Pair format examples:
    Crypto : BTC.USD · ETH.USD       (currency with dot separator)
    Forex  : EUR.USD · GBP.JPY       (pair currencies with dot)
    Stocks : AAPL.USD · TSLA.USD     (symbol.currency)

The pair format is split on '.' → symbol and currency.
If no '.', the whole string is used as symbol with currency=USD.

Notes:
    - Bracket orders are used for TP (limit) and SL (stop) management
    - IBKR requires a running TWS or Gateway process — cannot trade stand-alone
    - Reconcile returns all open positions for the connected account
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from src.config import AppConfig, Direction, Signal, compute_liquidation_price
from src.execution.interface import ExecutionError, ExecutionInterface
from src.execution.providers import register_execution

_TAKER_FEE_PCT = 0.0005  # IBKR tiered ~0.05% for crypto; 0 for stocks (PFOF)


def _parse_host_port(api_key: str) -> tuple[str, int]:
    """Parse 'host:port' from api_key string. Defaults to 127.0.0.1:7497."""
    if ":" in api_key:
        host, port_str = api_key.rsplit(":", 1)
        return host, int(port_str)
    return api_key, 7497


def _make_contract(pair: str):
    """Build an ib_insync Contract for the given pair string."""
    try:
        from ib_insync import Crypto, Forex, Stock
    except ImportError as exc:
        raise ImportError("ib_insync required: pip install ib_insync") from exc

    if "." in pair:
        symbol, currency = pair.split(".", 1)
    else:
        symbol, currency = pair, "USD"

    # Common crypto tickers
    crypto_symbols = {
        "BTC", "ETH", "SOL", "ADA", "XRP", "DOGE", "LTC",
        "BCH", "LINK", "DOT", "MATIC", "AVAX", "UNI", "ATOM",
    }
    if symbol.upper() in crypto_symbols:
        return Crypto(symbol.upper(), "PAXOS", currency.upper())

    # Forex pairs: both parts are currencies (3 chars each typical)
    forex_currencies = {
        "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD",
        "SEK", "NOK", "DKK", "SGD", "HKD", "MXN", "ZAR",
    }
    if symbol.upper() in forex_currencies:
        return Forex(symbol.upper() + currency.upper())

    # Default: equity
    return Stock(symbol.upper(), "SMART", currency.upper())


def _qty(size_usdt: float, leverage: int, price: float) -> float:
    """Convert USDT size → IB order quantity."""
    return (size_usdt * leverage) / price


@register_execution("ibkr")
class IBKRExecution(ExecutionInterface):
    """IBKR execution backend using ib_insync.

    Maintains a persistent IB connection for the lifetime of the provider.
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._host, self._port = _parse_host_port(cfg.api_key)
        self._client_id = int(cfg.api_secret) if cfg.api_secret.isdigit() else 1
        self._ib = None  # lazy-connected on first use
        self._open_orders: dict[str, Any] = {}  # pair → bracket order

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_ib(self):
        """Return a connected IB instance, connecting lazily if needed."""
        try:
            from ib_insync import IB
        except ImportError as exc:
            raise ImportError("ib_insync required: pip install ib_insync") from exc

        if self._ib is None or not self._ib.isConnected():
            ib = IB()
            ib.connect(self._host, self._port, clientId=self._client_id)
            self._ib = ib
        return self._ib

    # ------------------------------------------------------------------
    # place_order
    # ------------------------------------------------------------------

    async def place_order(self, signal: Signal) -> dict[str, Any]:
        """Place a bracket order (market entry + limit TP + stop SL) on IBKR."""
        try:
            from ib_insync import LimitOrder, StopOrder
        except ImportError as exc:
            raise ImportError("ib_insync required: pip install ib_insync") from exc

        ib = self._get_ib()
        contract = _make_contract(signal.pair)

        action = "BUY" if signal.direction is Direction.LONG else "SELL"
        close_action = "SELL" if signal.direction is Direction.LONG else "BUY"
        qty = _qty(signal.size_usdt, self.cfg.leverage, signal.entry_price)
        qty = round(qty, 8)

        slip = 0.0001
        fill_price = (
            signal.entry_price * (1.0 + slip)
            if signal.direction is Direction.LONG
            else signal.entry_price * (1.0 - slip)
        )

        try:
            from ib_insync import MarketOrder
            parent = MarketOrder(action, qty)
            parent.transmit = False

            tp_order = LimitOrder(
                close_action,
                qty,
                round(signal.tp_price, 5),
            )
            tp_order.parentId = parent.orderId
            tp_order.transmit = False

            sl_order = StopOrder(
                close_action,
                qty,
                round(signal.sl_price, 5),
            )
            sl_order.parentId = parent.orderId
            sl_order.transmit = True  # transmit the full bracket on last leg

            bracket = ib.placeOrder(contract, parent)
            ib.placeOrder(contract, tp_order)
            ib.placeOrder(contract, sl_order)

            # Wait for fill (up to 30s)
            for _ in range(300):
                await asyncio.sleep(0.1)
                if bracket.orderStatus.status in ("Filled", "Cancelled", "Inactive"):
                    break

            if bracket.orderStatus.status != "Filled":
                raise ExecutionError(
                    f"IBKR order not filled within 30s: status={bracket.orderStatus.status}",
                    {"pair": signal.pair},
                )

            actual_price = float(bracket.orderStatus.avgFillPrice or fill_price)
        except ExecutionError:
            raise
        except Exception as exc:
            raise ExecutionError(f"IBKR order failed: {exc}", {"pair": signal.pair}) from exc

        notional = signal.size_usdt * self.cfg.leverage
        fee = notional * _TAKER_FEE_PCT
        liq = compute_liquidation_price(actual_price, signal.direction, self.cfg.leverage)

        order_id = str(bracket.order.orderId)
        self._open_orders[signal.pair] = {
            "bracket": bracket,
            "tp_order": tp_order,
            "sl_order": sl_order,
        }

        return {
            "order_id": order_id,
            "pair": signal.pair,
            "direction": signal.direction.value,
            "entry_price": round(actual_price, 8),
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
        """Cancel a pending IB order by order ID."""
        ib = self._get_ib()
        try:
            open_orders = ib.openOrders()
            for order in open_orders:
                if str(order.orderId) == order_id:
                    ib.cancelOrder(order)
                    return True
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # get_position
    # ------------------------------------------------------------------

    async def get_position(self, pair: str) -> Optional[dict[str, Any]]:
        """Fetch open IB position for the given pair."""
        ib = self._get_ib()
        contract = _make_contract(pair)
        try:
            positions = ib.positions()
        except Exception:
            return None

        for pos in positions:
            if (
                pos.contract.symbol == contract.symbol
                and pos.contract.currency == contract.currency
            ):
                if pos.position == 0:
                    return None
                direction = "long" if pos.position > 0 else "short"
                avg_price = float(pos.avgCost)
                notional = abs(pos.position) * avg_price
                return {
                    "order_id": f"{pos.contract.symbol}_{direction}",
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
                        avg_price, Direction(direction), self.cfg.leverage
                    ),
                }
        return None

    # ------------------------------------------------------------------
    # close_position
    # ------------------------------------------------------------------

    async def close_position(self, pair: str, reason: str) -> dict[str, Any]:
        """Close an open IB position at market, cancelling bracket legs first."""
        ib = self._get_ib()
        pos = await self.get_position(pair)
        if pos is None:
            raise ExecutionError(f"No open position for {pair}", {"pair": pair})

        # Cancel any open bracket legs
        bracket_info = self._open_orders.pop(pair, None)
        if bracket_info:
            for key in ("tp_order", "sl_order"):
                try:
                    ib.cancelOrder(bracket_info[key])
                except Exception:
                    pass

        contract = _make_contract(pair)
        direction = pos["direction"]
        action = "SELL" if direction == "long" else "BUY"
        qty = (pos["notional_usdt"] / pos["entry_price"]) if pos["entry_price"] > 0 else 0
        qty = round(qty, 8)

        try:
            from ib_insync import MarketOrder
            close_order = ib.placeOrder(contract, MarketOrder(action, qty))

            for _ in range(300):
                await asyncio.sleep(0.1)
                if close_order.orderStatus.status in ("Filled", "Cancelled", "Inactive"):
                    break
        except Exception as exc:
            raise ExecutionError(f"IBKR close failed: {exc}", {"pair": pair}) from exc

        exit_price = float(close_order.orderStatus.avgFillPrice or pos["entry_price"])
        notional = pos["notional_usdt"]
        slip = 0.0001
        exit_slipped = exit_price * (1.0 - slip) if direction == "long" else exit_price * (1.0 + slip)

        entry = pos["entry_price"]
        if direction == "long":
            pnl_gross = (exit_slipped - entry) / entry * notional
        else:
            pnl_gross = (entry - exit_slipped) / entry * notional

        fee_exit = notional * _TAKER_FEE_PCT
        pnl_net = pnl_gross - fee_exit
        pnl_pct = pnl_net / pos["size_usdt"] * 100.0

        return {
            "exit_price": round(exit_slipped, 8),
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
        """Return all open IB positions."""
        ib = self._get_ib()
        try:
            positions = ib.positions()
        except Exception as exc:
            raise ExecutionError(f"IBKR reconcile failed: {exc}") from exc

        result = []
        for pos in positions:
            if pos.position == 0:
                continue
            direction = "long" if pos.position > 0 else "short"
            avg_price = float(pos.avgCost)
            notional = abs(pos.position) * avg_price
            result.append({
                "order_id": f"{pos.contract.symbol}_{direction}",
                "pair": f"{pos.contract.symbol}.{pos.contract.currency}",
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
            })
        return result
