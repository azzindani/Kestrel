"""
Layer 2 — WebSocket market data feed.

Connects to the exchange via ccxt.pro, subscribes to OHLCV streams, and
forwards completed candles to registered CandleBuilder instances.

Reconnection policy (CLAUDE.md §10, §16):
    - Exponential backoff: 2s → 4s → 8s → 16s → 32s
    - Max 5 retries before sending CRITICAL Telegram alert and waiting
    - Tracks last reconnect timestamp for the stale-data guard in risk/manager
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from src.config import AppConfig
from src.data.candle_builder import CandleBuilder

# Notify callback type: (level, message) → coroutine
NotifyFn = Callable[[str, str], None]

_MAX_RETRIES = 5
_BACKOFF_BASE = 2  # seconds


class MarketFeed:
    """Manages the WebSocket connection and dispatches candle data.

    One MarketFeed per (pair, timeframe) combination.
    """

    def __init__(
        self,
        cfg: AppConfig,
        pair: str,
        timeframe: str,
        builder: CandleBuilder,
        on_reconnect: Optional[Callable[[int], None]] = None,
        notify: Optional[NotifyFn] = None,
    ) -> None:
        self.cfg = cfg
        self.pair = pair
        self.timeframe = timeframe
        self.builder = builder
        self._on_reconnect = on_reconnect  # callback(ts_ms) when reconnect succeeds
        self._notify = notify
        self._running = False
        self._last_reconnect_ts: Optional[int] = None

    @property
    def last_reconnect_ts(self) -> Optional[int]:
        return self._last_reconnect_ts

    async def run(self) -> None:
        """Start streaming. Runs until stopped or unrecoverable failure."""
        self._running = True
        retry_count = 0

        while self._running:
            try:
                await self._stream(retry_count)
                retry_count = 0  # reset on clean exit
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as exc:
                retry_count += 1
                if retry_count > _MAX_RETRIES:
                    msg = (
                        f"WS feed {self.pair}/{self.timeframe} exceeded max retries ({_MAX_RETRIES}). Last error: {exc}"
                    )
                    if self._notify:
                        self._notify("CRITICAL", msg)
                    # Wait 60s then reset counter and try again
                    await asyncio.sleep(60)
                    retry_count = 0
                    continue

                delay = _BACKOFF_BASE**retry_count
                if self._notify:
                    self._notify(
                        "WARN",
                        f"WS feed {self.pair}/{self.timeframe} disconnected "
                        f"(attempt {retry_count}/{_MAX_RETRIES}). Reconnecting in {delay}s. err={exc}",
                    )
                await asyncio.sleep(delay)

    async def _stream(self, retry_count: int) -> None:
        """Inner streaming loop using ccxt.pro."""
        import ccxt.pro as ccxtpro  # deferred import — not available in all envs

        exchange_cls = getattr(ccxtpro, self.cfg.exchange)
        exchange = exchange_cls(
            {
                "apiKey": self.cfg.api_key,
                "secret": self.cfg.api_secret,
            }
        )

        if self.cfg.testnet:
            exchange.set_sandbox_mode(True)

        try:
            if retry_count > 0:
                ts_ms = int(time.time() * 1000)
                self._last_reconnect_ts = ts_ms
                if self._on_reconnect:
                    self._on_reconnect(ts_ms)

            while self._running:
                ohlcvs = await asyncio.wait_for(
                    exchange.watch_ohlcv(self.pair, self.timeframe),
                    timeout=90,
                )
                for ohlcv in ohlcvs:
                    # ccxt.pro watch_ohlcv: last entry may be the current partial candle.
                    # We detect close by checking if a new candle has started.
                    # Pass is_closed=True only for the previous candle when a new one begins.
                    # Note: ccxt.pro includes all candles that changed since last call;
                    # all but the last are guaranteed closed.
                    is_last = ohlcv is ohlcvs[-1]
                    self.builder.process_ohlcv(ohlcv, is_closed=not is_last)

                # The last candle may be closed on next tick; mark previous ones as closed
                if len(ohlcvs) >= 2:
                    self.builder.process_ohlcv(ohlcvs[-2], is_closed=True)
        finally:
            await exchange.close()

    def stop(self) -> None:
        """Signal the feed to stop after the current iteration."""
        self._running = False
