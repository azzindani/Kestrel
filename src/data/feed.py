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

_MAX_RETRIES = 5
_BACKOFF_BASE = 2


@dataclass(slots=True)
class _Subscription:
    pair: str
    timeframe: str
    builder: CandleBuilder
    on_reconnect: Optional[ReconnectFn]
    notify: Optional[NotifyFn]


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
    ) -> None:
        """Register a (pair, timeframe) stream with this shared feed."""
        self._subscriptions.append(
            _Subscription(pair, timeframe, builder, on_reconnect, notify)
        )

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
        import ccxt.pro as ccxtpro  # deferred import — not available in all envs

        exchange_cls = getattr(ccxtpro, self.cfg.exchange)
        exchange = exchange_cls(
            {"apiKey": self.cfg.api_key, "secret": self.cfg.api_secret}
        )
        if self.cfg.testnet:
            exchange.set_sandbox_mode(True)

        try:
            tasks = [
                asyncio.create_task(
                    self._stream_one(exchange, sub),
                    name=f"ws:{sub.pair}/{sub.timeframe}",
                )
                for sub in self._subscriptions
            ]
            stop_wait = asyncio.create_task(self._stop_event.wait(), name="ws:stop")
            done, pending = await asyncio.wait(
                [stop_wait, *tasks], return_when=asyncio.FIRST_COMPLETED
            )
            for t in tasks:
                t.cancel()
            stop_wait.cancel()
            await asyncio.gather(*tasks, stop_wait, return_exceptions=True)
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
                    if sub.notify:
                        sub.notify(
                            "CRITICAL",
                            f"WS feed {sub.pair}/{sub.timeframe} exceeded max retries "
                            f"({_MAX_RETRIES}). Last error: {exc}",
                        )
                    await asyncio.sleep(60)
                    retry_count = 0
                    continue
                delay = _BACKOFF_BASE**retry_count
                if sub.notify:
                    sub.notify(
                        "WARN",
                        f"WS feed {sub.pair}/{sub.timeframe} disconnected "
                        f"(attempt {retry_count}/{_MAX_RETRIES}). "
                        f"Reconnecting in {delay}s. err={exc}",
                    )
                await asyncio.sleep(delay)

    async def _stream_inner(self, exchange, sub: _Subscription, retry_count: int) -> None:
        if retry_count > 0:
            ts_ms = int(time.time() * 1000)
            self._last_reconnect_ts = ts_ms
            if sub.on_reconnect:
                sub.on_reconnect(ts_ms)

        while not self._stop_event.is_set():
            ohlcvs = await asyncio.wait_for(
                exchange.watch_ohlcv(sub.pair, sub.timeframe),
                timeout=90,
            )
            for ohlcv in ohlcvs:
                is_last = ohlcv is ohlcvs[-1]
                sub.builder.process_ohlcv(ohlcv, is_closed=not is_last)
            if len(ohlcvs) >= 2:
                sub.builder.process_ohlcv(ohlcvs[-2], is_closed=True)
