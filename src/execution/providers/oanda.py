"""
Layer 3 boundary — OANDA execution provider.

Implements ExecutionInterface for OANDA v20 REST API.
Supports forex, index CFDs, and commodity CFDs.

Setup (.env):
    EXCHANGE=oanda
    API_KEY=<v20-access-token>        # from OANDA account → Manage Funds → API Access
    API_SECRET=<oanda-account-id>     # e.g. "101-001-1234567-001"
    TESTNET=true                      # true = fxTrade Practice, false = fxTrade Live
    PAIR=EUR_USD                      # OANDA instrument format (underscored)

Pair format examples:
    Forex  : EUR_USD · GBP_JPY · USD_JPY
    Crypto : BTC_USD · ETH_USD  (note: OANDA uses BTC_USD not BTCUSDT)
    Index  : SPX500_USD · NAS100_USD

Leverage and margin:
    OANDA enforces its own regulated margin rates; the cfg.leverage value is used
    only for notional / unit-size calculations — it does not override OANDA's
    platform margin requirements.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from src.config import AppConfig, Direction, Signal, compute_liquidation_price
from src.execution.interface import ExecutionError, ExecutionInterface
from src.execution.providers import register_execution

_TAKER_FEE_PCT = 0.00013  # ~0.013% OANDA spread approximation as fee
_SLIPPAGE_PCT = 0.0001    # 0.01% slippage estimate for market orders


def _oanda_client(cfg: AppConfig):
    """Construct an oandapyV20 API client. Deferred import — optional dependency."""
    try:
        from oandapyV20 import API
    except ImportError as exc:
        raise ImportError(
            "oandapyV20 is required for the OANDA provider. "
            "Install it with: pip install oandapyV20"
        ) from exc

    environment = "practice" if cfg.testnet else "live"
    return API(access_token=cfg.api_key, environment=environment)


def _units(size_usdt: float, leverage: int, price: float, direction: Direction) -> str:
    """Convert USDT bucket size → OANDA units string (positive=long, negative=short).

    OANDA accepts fractional units for crypto and some instruments.
    Units are rounded to 2 decimal places to stay within OANDA precision limits.
    For forex, the caller should ensure the resulting notional makes sense for the
    account's minimum trade size.
    """
    notional = size_usdt * leverage
    raw_units = round(notional / price, 2)
    units = raw_units if direction is Direction.LONG else -raw_units
    return str(units)


@register_execution("oanda")
class OandaExecution(ExecutionInterface):
    """OANDA v20 execution backend.

    Uses API_SECRET as the account_id (OANDA v20 convention).
    Market orders with embedded TP (takeProfitOnFill) and SL (stopLossOnFill).
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._account_id = cfg.api_secret  # OANDA account ID passed as api_secret
        self._client = _oanda_client(cfg)

    # ------------------------------------------------------------------
    # place_order
    # ------------------------------------------------------------------

    async def place_order(self, signal: Signal) -> dict[str, Any]:
        """Place a market order with bracket TP/SL via OANDA v20 Orders endpoint."""
        try:
            from oandapyV20.endpoints.orders import OrderCreate
            from oandapyV20.contrib.requests import (
                MarketOrderRequest,
                TakeProfitDetails,
                StopLossDetails,
            )
        except ImportError as exc:
            raise ImportError("oandapyV20 required: pip install oandapyV20") from exc

        slip = _SLIPPAGE_PCT
        if signal.direction is Direction.LONG:
            fill_price = signal.entry_price * (1.0 + slip)
        else:
            fill_price = signal.entry_price * (1.0 - slip)

        units = _units(signal.size_usdt, self.cfg.leverage, signal.entry_price, signal.direction)
        tp_price = str(round(signal.tp_price, 5))
        sl_price = str(round(signal.sl_price, 5))

        order_body = MarketOrderRequest(
            instrument=signal.pair,
            units=units,
            takeProfitOnFill=TakeProfitDetails(price=tp_price).data,
            stopLossOnFill=StopLossDetails(price=sl_price).data,
        )

        req = OrderCreate(self._account_id, data=order_body.data)
        try:
            resp = self._client.request(req)
        except Exception as exc:
            raise ExecutionError(f"OANDA order failed: {exc}", {"pair": signal.pair}) from exc

        fill = resp.get("orderFillTransaction", {})
        order_id = fill.get("id", fill.get("orderID", "unknown"))
        actual_price = float(fill.get("price", fill_price))
        notional = signal.size_usdt * self.cfg.leverage
        fee = notional * _TAKER_FEE_PCT
        liq = compute_liquidation_price(actual_price, signal.direction, self.cfg.leverage)

        return {
            "order_id": str(order_id),
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
        """Cancel a pending order via OANDA v20 Orders endpoint."""
        try:
            from oandapyV20.endpoints.orders import OrderCancel
        except ImportError as exc:
            raise ImportError("oandapyV20 required: pip install oandapyV20") from exc

        req = OrderCancel(self._account_id, order_id)
        try:
            self._client.request(req)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # get_position
    # ------------------------------------------------------------------

    async def get_position(self, pair: str) -> Optional[dict[str, Any]]:
        """Fetch open position for instrument from OANDA."""
        try:
            from oandapyV20.endpoints.positions import PositionDetails
        except ImportError as exc:
            raise ImportError("oandapyV20 required: pip install oandapyV20") from exc

        req = PositionDetails(self._account_id, pair)
        try:
            resp = self._client.request(req)
        except Exception:
            return None

        pos = resp.get("position", {})
        long_units = int(pos.get("long", {}).get("units", 0))
        short_units = int(pos.get("short", {}).get("units", 0))

        if long_units == 0 and short_units == 0:
            return None

        direction = "long" if long_units > 0 else "short"
        side = pos.get("long" if direction == "long" else "short", {})
        avg_price = float(side.get("averagePrice", 0.0))
        unrealized_pnl = float(pos.get("unrealizedPL", 0.0))

        return {
            "order_id": pos.get("instrument", pair),
            "pair": pair,
            "direction": direction,
            "entry_price": avg_price,
            "size_usdt": self.cfg.bucket_size_usdt,
            "tp_price": 0.0,
            "sl_price": 0.0,
            "leverage": self.cfg.leverage,
            "ts": int(time.time() * 1000),
            "fee_usdt": 0.0,
            "notional_usdt": abs(long_units or short_units) * avg_price,
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
        """Close all units of the OANDA position at market."""
        try:
            from oandapyV20.endpoints.positions import PositionClose
        except ImportError as exc:
            raise ImportError("oandapyV20 required: pip install oandapyV20") from exc

        pos = await self.get_position(pair)
        if pos is None:
            raise ExecutionError(f"No open position for {pair}", {"pair": pair})

        body = (
            {"longUnits": "ALL"} if pos["direction"] == "long" else {"shortUnits": "ALL"}
        )
        req = PositionClose(self._account_id, pair, data=body)
        try:
            resp = self._client.request(req)
        except Exception as exc:
            raise ExecutionError(f"OANDA close failed: {exc}", {"pair": pair}) from exc

        fill = resp.get(
            "longOrderFillTransaction",
            resp.get("shortOrderFillTransaction", {}),
        )
        exit_price = float(fill.get("price", pos["entry_price"]))
        pnl_gross = float(fill.get("pl", 0.0))
        fee_exit = pos["notional_usdt"] * _TAKER_FEE_PCT
        pnl_net = pnl_gross - fee_exit
        pnl_pct = pnl_net / pos["size_usdt"] * 100.0

        return {
            "exit_price": round(exit_price, 8),
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
        """Return all open OANDA positions for reconciliation."""
        try:
            from oandapyV20.endpoints.positions import OpenPositions
        except ImportError as exc:
            raise ImportError("oandapyV20 required: pip install oandapyV20") from exc

        req = OpenPositions(self._account_id)
        try:
            resp = self._client.request(req)
        except Exception as exc:
            raise ExecutionError(f"OANDA reconcile failed: {exc}") from exc

        result = []
        for pos in resp.get("positions", []):
            instrument = pos.get("instrument", "")
            position = await self.get_position(instrument)
            if position is not None:
                result.append(position)
        return result
