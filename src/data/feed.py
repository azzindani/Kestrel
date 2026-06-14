"""
Layer 2 — WebSocket market data feed (ccxt.pro).

Multi-subscription shared client.  One MarketFeed instance per exchange is
shared across every bot trading on that exchange; each bot registers its
(pair, timeframe, builder, callbacks) via ``subscribe()``.  The single
ccxt.pro exchange client and connection state serve all subscriptions,
which is the main RAM/WS-connection win when running many bots.

Reconnection policy (CLAUDE.md §10, §16):
    - Per-stream exponential backoff: 2s → 4s → 8s → 16s → 32s
    - Max 5 retries before sending CRITICAL Telegram alert and waiting
    - Tracks last reconnect timestamp for the stale-data guard in risk/manager

Lifecycle:
    - ``run()`` is idempotent: only the first caller drives streaming;
      subsequent callers await the same stop event and return.  This lets
      each Daemon spawn ``feed.run()`` as one of its tasks without
      double-streaming when the feed is shared.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Optional

from src.config import AppConfig
from src.data.candle_builder import CandleBuilder

NotifyFn = Callable[[str, str], None]
ReconnectFn = Callable[[int], None]
# (level, message, payload) — routed by the daemon to the events table so WS
# health is observable in the DB/dashboard even when Telegram is disabled.
LogEventFn = Callable[[str, str, dict], None]

_MAX_RETRIES = 5
_BACKOFF_BASE = 2


@dataclass(slots=True)
class _Subscription:
    pair: str
    timeframe: str
    builder: CandleBuilder
    on_reconnect: Optional[ReconnectFn]
    notify: Optional[NotifyFn]
    log_event: Optional[LogEventFn] = None


class MarketFeed:
    """Shared ccxt.pro feed; one instance per exchange serves N subscriptions."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._subscriptions: list[_Subscription] = []
        self._streaming = False
        self._stop_event: Optional[asyncio.Event] = None
        self._last_reconnect_ts: Optional[int] = None

    @property
    def last_reconnect_ts(self) -> Optional[int]:
        return self._last_reconnect_ts

    def subscribe(
        self,
        pair: str,
        timeframe: str,
        builder: CandleBuilder,
        on_reconnect: Optional[ReconnectFn] = None,
        notify: Optional[NotifyFn] = None,
        log_event: Optional[LogEventFn] = None,
    ) -> None:
        """Register a (pair, timeframe) stream with this shared feed."""
        self._subscriptions.append(_Subscription(pair, timeframe, builder, on_reconnect, notify, log_event))

    @property
    def subscriptions(self) -> list[_Subscription]:
        """Read-only view of current subscriptions (for tests/diagnostics)."""
        return list(self._subscriptions)

    async def run(self) -> None:
        """Drive streaming.  Idempotent across multiple callers.

        First caller spins up the shared ccxt.pro client and spawns one stream
        task per subscription; subsequent callers await the same stop event.
        """
        # Lazy-init in the running loop so the Event binds to the right loop
        if self._stop_event is None:
            self._stop_event = asyncio.Event()

        if self._streaming:
            await self._stop_event.wait()
            return

        self._streaming = True
        try:
            await self._stream_all()
        finally:
            self._stop_event.set()
            self._streaming = False

    def stop(self) -> None:
        """Signal the feed to stop after the current iteration."""
        if self._stop_event is not None:
            self._stop_event.set()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _stream_all(self) -> None:
        """Run streaming for all registered subscriptions on one shared client.

        Subscriptions are observed dynamically: when multiple daemons in the
        same process each call ``feed.subscribe()`` from inside their own
        ``start()`` coroutines, the first caller of ``feed.run()`` may begin
        before later daemons have registered.  We poll ``self._subscriptions``
        until ``_stop_event`` fires, spawning a stream task for any
        (pair, timeframe) pair we haven't started yet.
        """
        import ccxt.pro as ccxtpro  # deferred import — not available in all envs

        exchange_cls = getattr(ccxtpro, self.cfg.exchange)
        exchange = exchange_cls({"apiKey": self.cfg.api_key, "secret": self.cfg.api_secret})
        if self.cfg.testnet:
            exchange.set_sandbox_mode(True)

        spawned: set[tuple[str, str]] = set()
        tasks: list[asyncio.Task] = []
        try:
            while not self._stop_event.is_set():
                for sub in list(self._subscriptions):
                    key = (sub.pair, sub.timeframe)
                    if key not in spawned:
                        spawned.add(key)
                        tasks.append(
                            asyncio.create_task(
                                self._stream_one(exchange, sub),
                                name=f"ws:{sub.pair}/{sub.timeframe}",
                            )
                        )
                # Wait a short tick before re-checking; exit immediately if stop fires.
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=0.5)
                    break
                except asyncio.TimeoutError:
                    continue
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await exchange.close()

    async def _stream_one(self, exchange, sub: _Subscription) -> None:
        retry_count = 0
        while not self._stop_event.is_set():
            try:
                await self._stream_inner(exchange, sub, retry_count)
                retry_count = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:
                retry_count += 1
                if retry_count > _MAX_RETRIES:
                    msg = f"WS feed {sub.pair}/{sub.timeframe} exceeded max retries ({_MAX_RETRIES}). Last error: {exc}"
                    if sub.notify:
                        sub.notify("CRITICAL", msg)
                    if sub.log_event:
                        sub.log_event(
                            "CRITICAL",
                            msg,
                            {
                                "pair": sub.pair,
                                "timeframe": sub.timeframe,
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                                "retries": _MAX_RETRIES,
                            },
                        )
                    await asyncio.sleep(60)
                    retry_count = 0
                    continue
                delay = _BACKOFF_BASE**retry_count
                msg = (
                    f"WS feed {sub.pair}/{sub.timeframe} disconnected "
                    f"(attempt {retry_count}/{_MAX_RETRIES}). Reconnecting in {delay}s. err={exc}"
                )
                if sub.notify:
                    sub.notify("WARN", msg)
                if sub.log_event:
                    sub.log_event(
                        "WARN",
                        msg,
                        {
                            "pair": sub.pair,
                            "timeframe": sub.timeframe,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "attempt": retry_count,
                        },
                    )
                await asyncio.sleep(delay)

    async def _stream_inner(self, exchange, sub: _Subscription, retry_count: int) -> None:
        """Per-subscription streaming loop.

        ccxt.pro exchanges differ in how they push candles:
            - Some (binance, bybit) return batches where the final entry is the
              partial/open candle and earlier entries are already-closed.
            - Others (okx) push single-row updates of the *current* open candle
              as ticks land; the close event is implicit in the timestamp
              rolling forward to the next period.

        Handle both: any entry that is NOT the most recent timestamp we have
        seen is closed; the most recent is tracked locally and only emitted
        as closed when its timestamp is superseded by a later one.
        """
        if retry_count > 0:
            ts_ms = int(time.time() * 1000)
            self._last_reconnect_ts = ts_ms
            if sub.on_reconnect:
                sub.on_reconnect(ts_ms)

        last_open: Optional[list] = None
        while not self._stop_event.is_set():
            ohlcvs = await asyncio.wait_for(
                exchange.watch_ohlcv(sub.pair, sub.timeframe),
                timeout=90,
            )
            if not ohlcvs:
                continue
            for ohlcv in ohlcvs:
                is_latest = ohlcv is ohlcvs[-1]
                if not is_latest:
                    # Older entries in a multi-row batch are guaranteed closed.
                    sub.builder.process_ohlcv(ohlcv, is_closed=True)
                    continue
                # ohlcv is the most recent entry in this batch.
                if last_open is not None and ohlcv[0] > last_open[0]:
                    # Timestamp advanced → the previous one we were tracking
                    # has now closed (a new period began).
                    sub.builder.process_ohlcv(last_open, is_closed=True)
                last_open = ohlcv
