"""
Layer 3 boundary — Exness execution provider (via MetaApi cloud).

Exness has no native REST/WebSocket trading API; automated trading goes through
MetaTrader 5.  MetaApi (https://metaapi.cloud) exposes any MT4/MT5 account over a
cloud REST/WebSocket API with an async Python SDK, which lets Kestrel keep its
asyncio daemon on Linux without running an MT5 terminal locally.

Everything on Exness is a CFD (no ownership of the underlying); the instrument
with a real tangible underlying that fits this bot is a commodity/metal/energy
or BTC/ETH symbol (see CLAUDE.md project notes).

Setup (.env — set by the operator; agent does not touch .env):
    EXCHANGE=exness
    API_KEY=<metaapi-auth-token>        # MetaApi token (Account → API access)
    API_SECRET=<metaapi-account-id>     # the provisioned MT5 account id in MetaApi
    TESTNET=true                        # informational: provision a DEMO MT5 account
    PAIR=XAUUSD                         # MT5 symbol — XAUUSD · USOIL · BTCUSD · ...
    LEVERAGE=50                         # used for notional/lot sizing only (see below)
    BUCKET_SIZE_USDT=...                # per-trade margin; min-lot interplay matters (below)

Dependency (optional — install only when EXCHANGE=exness):
    pip install metaapi-cloud-sdk

Leverage / sizing note:
    MT5 leverage is an *account-level* setting at the broker; cfg.leverage is used
    here only to size the notional (size_usdt × leverage), identical to the OANDA
    and ccxt providers.  The notional is then converted to MT5 *lots* using the
    symbol's contractSize.  If the resulting lot size is below the broker minimum
    (minVolume), the order is REJECTED rather than silently inflated — a $10 bucket
    at 50× cannot open the 0.01-lot minimum on some symbols (e.g. gold), so either
    raise the bucket or pick a symbol whose min-lot notional fits.
"""

from __future__ import annotations

import math
import time
from typing import Any, Optional

from src.config import AppConfig, Direction, Signal, compute_liquidation_price
from src.execution.interface import ExecutionError, ExecutionInterface
from src.execution.providers import register_execution

# Recorded per-trade cost estimate for PnL accounting. Exness Standard accounts
# are spread-only (no commission); this is a placeholder to CALIBRATE against real
# demo fills. The risk fee-viability gate uses round_trip_fee_pct() from §17, not
# this value, so it only affects recorded PnL, not whether a trade is allowed.
_FEE_PCT = 0.0002  # ~0.02% per side — calibrate from demo fills


def _round_volume(raw_lots: float, volume_step: float, min_volume: float) -> float:
    """Floor raw lot size to the broker's volume step; 0.0 if below min volume.

    Pure function. Returns 0.0 (→ caller rejects the order) when the sized lot is
    smaller than the broker minimum, rather than rounding *up* and over-leveraging
    the bucket.
    """
    if volume_step > 0:
        stepped = math.floor(raw_lots / volume_step) * volume_step
    else:
        stepped = raw_lots
    if stepped + 1e-12 < min_volume:
        return 0.0
    return round(stepped, 8)


def _compute_volume(
    size_usdt: float,
    leverage: int,
    price: float,
    contract_size: float,
    volume_step: float,
    min_volume: float,
) -> float:
    """Convert a USDT bucket → MT5 lots for the given symbol. Pure function.

    notional = size_usdt × leverage
    units    = notional / price            (units of the base asset)
    lots     = units / contractSize        (MT5 lots)
    """
    if price <= 0 or contract_size <= 0:
        return 0.0
    notional = size_usdt * leverage
    raw_lots = notional / (price * contract_size)
    return _round_volume(raw_lots, volume_step, min_volume)


@register_execution("exness")
class ExnessExecution(ExecutionInterface):
    """Exness MT5 execution backend via the MetaApi cloud RPC connection.

    Uses API_KEY as the MetaApi token and API_SECRET as the MetaApi account id.
    Places MT5 market orders with attached SL/TP. Connection is established lazily
    on first use (MetaApi setup is async, so it cannot run in __init__).
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._token = cfg.api_key
        self._account_id = cfg.api_secret
        self._account = None
        self._connection = None
        self._spec_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Lazy async connection
    # ------------------------------------------------------------------

    async def _ensure_connected(self) -> None:
        if self._connection is not None:
            return
        try:
            from metaapi_cloud_sdk import MetaApi
        except ImportError as exc:
            raise ImportError(
                "metaapi-cloud-sdk is required for the Exness provider. "
                "Install it with: pip install metaapi-cloud-sdk"
            ) from exc

        try:
            api = MetaApi(self._token)
            account = await api.metatrader_account_api.get_account(self._account_id)
            await account.wait_connected()
            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized()
        except Exception as exc:  # MetaApi raises bare exceptions
            raise ExecutionError(
                f"MetaApi/Exness connection failed: {exc}",
                {"account_id": self._account_id},
            ) from exc

        self._account = account
        self._connection = connection

    async def _spec(self, symbol: str) -> dict:
        """Return (cached) MT5 symbol specification: contractSize, minVolume, ..."""
        if symbol not in self._spec_cache:
            self._spec_cache[symbol] = await self._connection.get_symbol_specification(symbol)
        return self._spec_cache[symbol]

    # ------------------------------------------------------------------
    # place_order
    # ------------------------------------------------------------------

    async def place_order(self, signal: Signal) -> dict[str, Any]:
        """Place an MT5 market order with attached SL/TP via MetaApi."""
        await self._ensure_connected()
        conn = self._connection

        spec = await self._spec(signal.pair)
        price_info = await conn.get_symbol_price(signal.pair)
        is_long = signal.direction is Direction.LONG
        ref_price = float(price_info["ask"] if is_long else price_info["bid"])

        contract_size = float(spec.get("contractSize", 1.0))
        min_volume = float(spec.get("minVolume", 0.01))
        volume_step = float(spec.get("volumeStep", 0.01))
        digits = int(spec.get("digits", 5))

        volume = _compute_volume(
            signal.size_usdt,
            self.cfg.leverage,
            ref_price,
            contract_size,
            volume_step,
            min_volume,
        )
        if volume <= 0.0:
            raise ExecutionError(
                f"Sized lot for {signal.pair} below broker minimum "
                f"(min={min_volume}). Raise bucket size or pick a smaller-contract "
                f"symbol. size_usdt={signal.size_usdt} leverage={self.cfg.leverage} "
                f"price={ref_price} contract_size={contract_size}",
                {"pair": signal.pair},
            )

        sl_price = round(signal.sl_price, digits)
        tp_price = round(signal.tp_price, digits)
        options = {"comment": "kestrel"}

        try:
            if is_long:
                await conn.create_market_buy_order(signal.pair, volume, sl_price, tp_price, options)
            else:
                await conn.create_market_sell_order(signal.pair, volume, sl_price, tp_price, options)
        except Exception as exc:
            raise ExecutionError(
                f"Exness market order failed: {exc}",
                {"pair": signal.pair, "volume": volume},
            ) from exc

        # Read the actual fill from the resulting position (MetaApi market-order
        # results do not always carry the fill price).
        pos = await self.get_position(signal.pair)
        fill_price = pos["entry_price"] if pos else ref_price
        notional = volume * contract_size * fill_price
        fee = notional * _FEE_PCT
        liq = compute_liquidation_price(fill_price, signal.direction, self.cfg.leverage)

        return {
            "order_id": pos["order_id"] if pos else f"kestrel-{signal.ts}",
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
        """Cancel a pending MT5 order. Market orders fill immediately, so this is
        a best-effort no-op that returns False when there is nothing to cancel."""
        await self._ensure_connected()
        try:
            await self._connection.cancel_order(order_id)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # get_position
    # ------------------------------------------------------------------

    async def get_position(self, pair: str) -> Optional[dict[str, Any]]:
        """Return the open MT5 position for ``pair``, or None if flat."""
        await self._ensure_connected()
        try:
            positions = await self._connection.get_positions()
        except Exception as exc:
            raise ExecutionError(f"Exness get_positions failed: {exc}", {"pair": pair}) from exc

        for p in positions:
            if p.get("symbol") != pair:
                continue
            direction = "long" if p.get("type") == "POSITION_TYPE_BUY" else "short"
            entry = float(p.get("openPrice", 0.0))
            volume = float(p.get("volume", 0.0))
            spec = await self._spec(pair)
            contract_size = float(spec.get("contractSize", 1.0))
            notional = volume * contract_size * entry
            return {
                "order_id": str(p.get("id", pair)),
                "pair": pair,
                "direction": direction,
                "entry_price": entry,
                "size_usdt": self.cfg.bucket_size_usdt,
                "tp_price": float(p.get("takeProfit", 0.0) or 0.0),
                "sl_price": float(p.get("stopLoss", 0.0) or 0.0),
                "leverage": self.cfg.leverage,
                "ts": int(time.time() * 1000),
                "fee_usdt": 0.0,
                "notional_usdt": round(notional, 4),
                "liquidation_price": compute_liquidation_price(
                    entry, Direction(direction), self.cfg.leverage
                ),
                "unrealised_pnl": float(p.get("profit", 0.0) or 0.0),
            }
        return None

    # ------------------------------------------------------------------
    # close_position
    # ------------------------------------------------------------------

    async def close_position(self, pair: str, reason: str) -> dict[str, Any]:
        """Close the open MT5 position at market via MetaApi."""
        await self._ensure_connected()
        pos = await self.get_position(pair)
        if pos is None:
            raise ExecutionError(f"No open position for {pair}", {"pair": pair})

        conn = self._connection
        price_info = await conn.get_symbol_price(pair)
        exit_price = float(price_info["bid"] if pos["direction"] == "long" else price_info["ask"])

        try:
            await conn.close_position(pos["order_id"])
        except Exception as exc:
            raise ExecutionError(
                f"Exness close failed: {exc}", {"pair": pair, "reason": reason}
            ) from exc

        notional = pos["notional_usdt"]
        entry = pos["entry_price"]
        if pos["direction"] == "long":
            pnl_gross = (exit_price - entry) / entry * notional if entry else 0.0
        else:
            pnl_gross = (entry - exit_price) / entry * notional if entry else 0.0

        fee_exit = notional * _FEE_PCT
        pnl_net = pnl_gross - fee_exit
        pnl_pct = pnl_net / pos["size_usdt"] * 100.0 if pos["size_usdt"] else 0.0

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
        """Return all open MT5 positions for startup reconciliation."""
        await self._ensure_connected()
        try:
            positions = await self._connection.get_positions()
        except Exception as exc:
            raise ExecutionError(f"Exness reconcile failed: {exc}") from exc

        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for p in positions:
            symbol = p.get("symbol")
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            position = await self.get_position(symbol)
            if position is not None:
                result.append(position)
        return result

    # ------------------------------------------------------------------
    # cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the MetaApi connection if open."""
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                pass
