"""Unit tests for LiveExecution's maker path (owner-authorized 2026-07-15).

The maker path must mirror SimulationExecution's validated semantics
(CLAUDE.md §13): post-only limit entry at the signal price (maker fee, no
slippage) with an unfilled entry SKIPPED not chased; a resting post-only
reduce-only take-profit placed on fill; stops/timeouts market out at taker.
The whole points-program edge is maker-dependent (RESEARCH_LOOP iter 56), so
these fee/fill mechanics are load-bearing, not cosmetic.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

import src.execution.live as live_mod
from src.execution.interface import ExecutionError
from src.execution.live import LiveExecution
from tests.helpers.factories import make_app_config, make_signal


class _FakeExchange:
    """Minimal async ccxt stand-in with scriptable order lifecycle."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self.tickers: dict[str, float] = {}
        self.positions: list[dict[str, Any]] = []
        self.open_orders: list[dict[str, Any]] = []
        self._next_id = 0
        self.entry_fill_mode = "immediate"  # immediate | never

    async def fetch_ticker(self, pair: str) -> dict[str, Any]:
        return {"last": self.tickers.get(pair, 100.0)}

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> dict[str, Any]:
        self._next_id += 1
        oid = f"o{self._next_id}"
        record = {
            "id": oid,
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params or {},
            "status": "open",
            "filled": 0.0,
            "average": None,
        }
        is_tp = "-tp" in str((params or {}).get("clientOrderId", ""))
        if type == "market":
            record["status"] = "closed"
            record["filled"] = amount
            record["average"] = self.tickers.get(symbol, 100.0)
        elif not is_tp and self.entry_fill_mode == "immediate":
            record["status"] = "closed"
            record["filled"] = amount
            record["average"] = price
        self.created.append(record)
        self.orders[oid] = record
        return record

    async def fetch_order(self, order_id: str, pair: str) -> dict[str, Any]:
        return self.orders[order_id]

    async def cancel_order(self, order_id: str, pair: str) -> None:
        self.cancelled.append(order_id)
        if order_id in self.orders and self.orders[order_id]["status"] == "open":
            self.orders[order_id]["status"] = "canceled"

    async def fetch_positions(self, pairs: list[str]) -> list[dict[str, Any]]:
        return self.positions

    async def fetch_open_orders(self, pair: str) -> list[dict[str, Any]]:
        return self.open_orders

    async def close(self) -> None:
        return None


def _make_live(maker: bool) -> tuple[LiveExecution, _FakeExchange]:
    ex = _FakeExchange()
    live = object.__new__(LiveExecution)
    live.cfg = make_app_config(maker_execution=maker)
    live._exchange = ex
    live._tp_orders = {}
    live._entry_meta = {}
    return live, ex


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch):
    monkeypatch.setattr(live_mod, "_ENTRY_FILL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(live_mod, "_FILL_POLL_S", 0.01)


class TestMakerEntry:
    async def test_filled_entry_uses_maker_fee_and_signal_price(self):
        live, _ex = _make_live(maker=True)
        sig = make_signal(entry=100.0, tp_offset=0.6, sl_offset=1.2)
        result = await live.place_order(sig)
        assert result["entry_price"] == 100.0  # signal price, no slippage
        expected_notional = sig.size_usdt * live.cfg.leverage
        assert result["fee_usdt"] == pytest.approx(expected_notional * 0.0002)

    async def test_filled_entry_places_resting_post_only_tp(self):
        live, ex = _make_live(maker=True)
        sig = make_signal(entry=100.0, tp_offset=0.6, sl_offset=1.2)
        await live.place_order(sig)
        tp_orders = [o for o in ex.created if o["params"].get("reduceOnly")]
        assert len(tp_orders) == 1
        assert tp_orders[0]["params"].get("postOnly") is True
        assert tp_orders[0]["price"] == sig.tp_price
        assert live._tp_orders[sig.pair] == tp_orders[0]["id"]

    async def test_unfilled_entry_cancels_and_raises_skips_trade(self):
        live, ex = _make_live(maker=True)
        ex.entry_fill_mode = "never"
        sig = make_signal(entry=100.0, tp_offset=0.6, sl_offset=1.2)
        with pytest.raises(ExecutionError) as excinfo:
            await live.place_order(sig)
        assert "entry_unfilled" in str(excinfo.value)
        assert len(ex.cancelled) == 1  # remainder cancelled, never chased
        assert not [o for o in ex.created if o["type"] == "market"]


class TestMakerClose:
    async def test_tp_filled_settles_at_maker_fee_from_entry_meta(self):
        live, ex = _make_live(maker=True)
        sig = make_signal(entry=100.0, tp_offset=0.6, sl_offset=1.2)
        entry = await live.place_order(sig)
        tp_id = live._tp_orders[sig.pair]
        ex.orders[tp_id]["status"] = "closed"
        ex.orders[tp_id]["average"] = sig.tp_price

        result = await live.close_position(sig.pair, "take_profit")

        assert result["exit_price"] == pytest.approx(sig.tp_price)
        notional = entry["notional_usdt"]
        assert result["fee_exit_usdt"] == pytest.approx(notional * 0.0002)
        expected_gross = (sig.tp_price - 100.0) / 100.0 * notional
        assert result["pnl_gross_usdt"] == pytest.approx(expected_gross, rel=1e-6)
        assert sig.pair not in live._tp_orders

    async def test_tp_race_settles_as_tp_even_when_reason_says_stop(self):
        # Monitor may request stop_loss after the resting TP already filled
        # intra-candle — economics must settle from the actual TP fill.
        live, ex = _make_live(maker=True)
        sig = make_signal(entry=100.0, tp_offset=0.6, sl_offset=1.2)
        await live.place_order(sig)
        tp_id = live._tp_orders[sig.pair]
        ex.orders[tp_id]["status"] = "closed"
        ex.orders[tp_id]["average"] = sig.tp_price

        result = await live.close_position(sig.pair, "stop_loss")

        assert result["exit_price"] == pytest.approx(sig.tp_price)
        assert result["pnl_net_usdt"] > 0  # settled as the win it actually was

    async def test_adverse_close_cancels_tp_and_markets_out_at_taker(self):
        live, ex = _make_live(maker=True)
        sig = make_signal(entry=100.0, tp_offset=0.6, sl_offset=1.2)
        entry = await live.place_order(sig)
        tp_id = live._tp_orders[sig.pair]
        ex.tickers[sig.pair] = 98.8  # below SL for the long
        ex.positions = [
            {
                "contracts": 1.0,
                "side": "long",
                "entryPrice": 100.0,
                "initialMargin": entry["size_usdt"],
                "notional": entry["notional_usdt"],
                "leverage": live.cfg.leverage,
                "liquidationPrice": 95.0,
                "unrealizedPnl": -1.0,
            }
        ]

        result = await live.close_position(sig.pair, "stop_loss")

        assert tp_id in ex.cancelled
        market_orders = [o for o in ex.created if o["type"] == "market"]
        assert len(market_orders) == 1
        assert result["fee_exit_usdt"] == pytest.approx(entry["notional_usdt"] * 0.0004)
        assert sig.pair not in live._tp_orders
        assert sig.pair not in live._entry_meta


class TestTakerPathUnchanged:
    async def test_default_taker_entry_is_market_order(self):
        live, ex = _make_live(maker=False)
        sig = make_signal(entry=100.0, tp_offset=0.6, sl_offset=1.2)
        result = await live.place_order(sig)
        assert ex.created[0]["type"] == "market"
        notional = sig.size_usdt * live.cfg.leverage
        assert result["fee_usdt"] == pytest.approx(notional * 0.0004)
        assert not live._tp_orders  # no resting TP on the taker path


class TestReconcileRehydration:
    async def test_reconcile_rehydrates_entry_meta_and_resting_tp(self):
        live, ex = _make_live(maker=True)
        ex.positions = [
            {
                "contracts": 1.0,
                "side": "long",
                "entryPrice": 100.0,
                "initialMargin": 0.5,
                "notional": 10.0,
                "leverage": live.cfg.leverage,
                "liquidationPrice": 95.0,
                "unrealizedPnl": 0.1,
            }
        ]
        ex.open_orders = [{"id": "tp-restored", "type": "limit", "reduceOnly": True, "info": {}}]

        positions = await live.reconcile()

        assert len(positions) == 1
        assert live._tp_orders[live.cfg.pair] == "tp-restored"
        assert live._entry_meta[live.cfg.pair]["entry_price"] == 100.0
