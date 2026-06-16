"""
Layer 3 boundary — simulation execution engine.

Implements ExecutionInterface for paper trading (ENV=dev).
Models isolated margin, fees, slippage, and liquidation exactly as specified in
CLAUDE.md §13, §17, §29. Two cost models, selected by cfg.maker_execution:
taker (default) = market fills with taker fee + slippage; maker = post-only limit
fills (entry + take-profit) at the set price with the maker fee and no slippage.

Injected by the daemon when ENV=dev. Never touches a real exchange.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from src.config import (
    AppConfig,
    Direction,
    Params,
    Signal,
    compute_liquidation_price,
)
from src.execution.interface import ExecutionError, ExecutionInterface

_TAKER_FEE_PCT = 0.04 / 100.0  # 0.04% per side (market fills)
_MAKER_FEE_PCT = 0.02 / 100.0  # 0.02% per side (post-only limit fills; see cfg.maker_execution)
_SLIPPAGE_PCT = 0.05 / 100.0  # 0.05% per side (market fills only — maker limit fills have none)
_DEFAULT_MAX_HOLD_CANDLES = 4  # fallback when params not supplied (CLAUDE.md §22)


class SimulationExecution(ExecutionInterface):
    """In-process paper trading engine.

    Positions are tracked in memory during a session; DB is the authoritative
    store (written by the daemon, not here).
    """

    def __init__(self, cfg: AppConfig, params: Optional[Params] = None) -> None:
        self.cfg = cfg
        self._max_hold_candles = params.max_hold_candles if params is not None else _DEFAULT_MAX_HOLD_CANDLES
        # Trailing-close config (CLAUDE.md §17 capital model — let winners run).
        self._trailing_enabled = params.trailing_enabled if params is not None else False
        self._trail_activation_r = params.trail_activation_r if params is not None else 1.0
        self._trail_distance_r = params.trail_distance_r if params is not None else 0.8
        self._positions: dict[str, dict[str, Any]] = {}
        self._prices: dict[str, float] = {}

    # -----------------------------------------------------------------------
    # place_order
    # -----------------------------------------------------------------------

    async def place_order(self, signal: Signal) -> dict[str, Any]:
        """Simulate the entry fill. Taker (default) = market fill with slippage +
        taker fee; maker (cfg.maker_execution) = post-only limit fill at the signal
        price with NO slippage + the lower maker fee."""
        if signal.pair in self._positions:
            raise ExecutionError(
                f"Position already open for {signal.pair}",
                {"pair": signal.pair},
            )

        notional = signal.size_usdt * self.cfg.leverage
        if self.cfg.maker_execution:
            # Post-only limit: fills at the price you set — no slippage, maker fee.
            fill_price = signal.entry_price
            fee_entry = notional * _MAKER_FEE_PCT
        else:
            slip = _SLIPPAGE_PCT
            if signal.direction is Direction.LONG:
                fill_price = signal.entry_price * (1.0 + slip)
            else:
                fill_price = signal.entry_price * (1.0 - slip)
            fee_entry = notional * _TAKER_FEE_PCT
        liq_price = compute_liquidation_price(fill_price, signal.direction, self.cfg.leverage)

        order_id = str(uuid.uuid4())[:8]
        ts_ms = int(time.time() * 1000)

        # Trailing-close geometry: the trail is measured in units of the initial
        # stop distance R (= |entry − SL| = sl_atr_multiplier × ATR), so it scales
        # with each pair's volatility without plumbing ATR through this layer.
        r_unit = abs(round(fill_price, 8) - signal.sl_price)

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
            # Incremented by check_exits each candle; powers the timeout branch
            # so positions don't hang indefinitely when TP/SL never trigger.
            "candles_held": 0,
            # Trailing-close state (only consulted when trailing_enabled).
            "trailing_enabled": self._trailing_enabled,
            "peak_price": round(fill_price, 8),
            "trail_stop": None,
            "trail_activation_dist": round(self._trail_activation_r * r_unit, 8),
            "trail_distance_dist": round(self._trail_distance_r * r_unit, 8),
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
        notional = pos["notional_usdt"]

        # Maker treatment applies ONLY to take-profit exits (a resting limit at the
        # target fills with no slippage + maker fee). Stops, timeouts and
        # liquidations market out — they pay taker fee + slippage even in maker mode,
        # because you cannot post-only your way out of an adverse move. This is what
        # makes maker lift the WIN side more than the loss side.
        if self.cfg.maker_execution and reason == "take_profit":
            fill_exit = exit_price
            fee_exit = notional * _MAKER_FEE_PCT
        else:
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
        hold_candles = pos.get("candles_held", 0)

        del self._positions[pair]

        # Signed price move captured ("points"): favourable = positive.
        points = (fill_exit - entry) if direction == "long" else (entry - fill_exit)

        return {
            "exit_price": round(fill_exit, 8),
            "entry_price": entry,
            "direction": direction,
            "leverage": pos["leverage"],
            "notional_usdt": notional,
            "points": round(points, 8),
            "pnl_gross_usdt": round(pnl_gross, 6),
            "fee_exit_usdt": round(fee_exit, 6),
            "pnl_net_usdt": round(pnl_net, 6),
            "pnl_pct": round(pnl_pct, 4),
            "ts": int(time.time() * 1000),
            "hold_candles": hold_candles,
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

    def update_price(self, pair: str, price: float) -> None:
        """Record current market price for simulated TP/SL/close calculations."""
        self._prices[pair] = price

    def unrealized_pnl(self) -> float:
        """Gross mark-to-market PnL (USDT) of all open positions at the last seen
        price. Used by the portfolio guard to aggregate across bots; NOT part of
        ExecutionInterface (sim-only — the daemon narrows the type before calling)."""
        total = 0.0
        for pair, pos in self._positions.items():
            entry = pos["entry_price"]
            price = self._prices.get(pair, entry)
            notional = pos["notional_usdt"]
            if entry <= 0.0:
                continue
            if pos["direction"] == "long":
                total += (price - entry) / entry * notional
            else:
                total += (entry - price) / entry * notional
        return total

    @staticmethod
    def _advance_trail(pos: dict[str, Any], price: float) -> None:
        """Ratchet the trailing stop toward price as unrealised profit grows.

        Arms once favourable excursion ≥ trail_activation_dist, then holds the
        stop trail_distance_dist below the running peak (above it for shorts).
        The stop only ever tightens — it never loosens — and is floored at the
        initial SL so trailing can't widen risk.
        """
        direction = pos["direction"]
        entry = pos["entry_price"]
        activation = pos["trail_activation_dist"]
        distance = pos["trail_distance_dist"]
        if direction == "long":
            peak = max(pos["peak_price"], price)
            pos["peak_price"] = peak
            if peak - entry >= activation:
                candidate = max(peak - distance, pos["sl_price"])
                cur = pos["trail_stop"]
                pos["trail_stop"] = candidate if cur is None else max(cur, candidate)
        else:
            trough = min(pos["peak_price"], price)
            pos["peak_price"] = trough
            if entry - trough >= activation:
                candidate = min(trough + distance, pos["sl_price"])
                cur = pos["trail_stop"]
                pos["trail_stop"] = candidate if cur is None else min(cur, candidate)

    def check_exits(self, pair: str) -> Optional[str]:
        """Check whether the position should close on this candle.

        Called once per candle by the daemon. Returns the exit reason if any
        of trailing-stop, TP, SL, liquidation, or max_hold_candles timeout has
        hit; None to keep the position open.

        When trailing is enabled the fixed take-profit is dropped — the trailing
        stop governs the upside so winners ride until price reverses by
        trail_distance into the move (CLAUDE.md §17).

        Returns:
            'take_profit' | 'trailing_stop' | 'stop_loss' | 'liquidated' | 'timeout' | None
        """
        pos = self._positions.get(pair)
        if pos is None:
            return None

        # Each check_exits call corresponds to one closed candle since entry.
        pos["candles_held"] += 1

        price = self._prices.get(pair)
        if price is None:
            return None

        direction = pos["direction"]
        liq = pos["liquidation_price"]
        trailing = pos["trailing_enabled"]

        # Price-based exits take priority over timeout (a hit on the timeout
        # candle itself should record the real reason).
        if direction == "long":
            if trailing:
                ts = pos["trail_stop"]
                if ts is not None and price <= ts:
                    return "trailing_stop"
            elif price >= pos["tp_price"]:
                return "take_profit"
            if price <= pos["sl_price"]:
                return "stop_loss"
            if price <= liq:
                return "liquidated"
        else:
            if trailing:
                ts = pos["trail_stop"]
                if ts is not None and price >= ts:
                    return "trailing_stop"
            elif price <= pos["tp_price"]:
                return "take_profit"
            if price >= pos["sl_price"]:
                return "stop_loss"
            if price >= liq:
                return "liquidated"

        # No exit this candle — advance the trail for the next one (done after
        # the exit check so the current candle can't both raise and trip the stop).
        if trailing:
            self._advance_trail(pos, price)

        # CLAUDE.md §16 / §22: force-close after max_hold_candles to prevent
        # positions hanging indefinitely when price stays inside the band.
        if pos["candles_held"] >= self._max_hold_candles:
            return "timeout"

        return None
