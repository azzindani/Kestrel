"""
Layer 2 — Alpaca market data feed.

Streams live crypto or stock bars via the alpaca-py WebSocket data client.
Bar events are emitted on candle close (Alpaca publishes bars at the minute/
second boundary, not during the bar).

Setup (.env):
    EXCHANGE=alpaca
    API_KEY=<APCA-API-KEY-ID>
    API_SECRET=<APCA-API-SECRET-KEY>
    TESTNET=true                  # paper feed vs live feed
    PAIR=BTC/USD                  # Alpaca symbol format
    TIMEFRAME_ENTRY=1m            # Alpaca bar subscription granularity

Supported timeframes: 1m, 5m, 15m, 1h, 1d (Alpaca bar granularities)

Reconnection policy: exponential backoff per CLAUDE.md §10.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from src.config import AppConfig
from src.data.candle_builder import CandleBuilder
from src.data.providers import register_feed

_MAX_RETRIES = 5
_BACKOFF_BASE = 2


def _alpaca_symbol(pair: str) -> str:
    """Convert Kestrel pair format to Alpaca symbol (BTC/USD → BTCUSD)."""
    return pair.replace("/", "")


@register_feed("alpaca")
class AlpacaFeed:
    """Alpaca crypto/stock bar WebSocket feed.

    Subscribes to minute (or other granularity) bars and forwards them to
    the CandleBuilder.  Alpaca publishes a bar event on close, so
    is_closed=True is always set.
    """

    def __init__(
        self,
        cfg: AppConfig,
        pair: str,
        timeframe: str,
        builder: CandleBuilder,
        on_reconnect: Optional[Callable[[int], None]] = None,
        notify: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.pair = pair
        self.timeframe = timeframe
        self.builder = builder
        self._on_reconnect = on_reconnect
        self._notify = notify
        self._running = False
        self._last_reconnect_ts: Optional[int] = None
        self._symbol = _alpaca_symbol(pair)

    @property
    def last_reconnect_ts(self) -> Optional[int]:
        return self._last_reconnect_ts

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """WebSocket streaming loop with exponential backoff on failure."""
        self._running = True
        retry_count = 0

        while self._running:
            try:
                await self._stream(retry_count)
                retry_count = 0
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as exc:
                retry_count += 1
                if retry_count > _MAX_RETRIES:
                    msg = (
                        f"Alpaca feed {self.pair}/{self.timeframe} exceeded max retries "
                        f"({_MAX_RETRIES}). Last error: {exc}"
                    )
                    if self._notify:
                        self._notify("CRITICAL", msg)
                    await asyncio.sleep(60)
                    retry_count = 0
                    continue

                delay = _BACKOFF_BASE**retry_count
                if self._notify:
                    self._notify(
                        "WARN",
                        f"Alpaca feed {self.pair}/{self.timeframe} disconnected "
                        f"(attempt {retry_count}/{_MAX_RETRIES}). "
                        f"Reconnecting in {delay}s. err={exc}",
                    )
                await asyncio.sleep(delay)

    async def _stream(self, retry_count: int) -> None:
        """Connect to Alpaca WebSocket and stream bars."""
        try:
            from alpaca.data.live import CryptoDataStream, StockDataStream
        except ImportError as exc:
            raise ImportError("alpaca-py is required for the Alpaca feed. Install with: pip install alpaca-py") from exc

        if retry_count > 0:
            ts_ms = int(time.time() * 1000)
            self._last_reconnect_ts = ts_ms
            if self._on_reconnect:
                self._on_reconnect(ts_ms)

        # Select crypto vs equity stream based on pair format
        is_crypto = "/" in self.pair
        StreamClass = CryptoDataStream if is_crypto else StockDataStream

        stream = StreamClass(
            api_key=self.cfg.api_key,
            secret_key=self.cfg.api_secret,
            feed="us" if not is_crypto else None,
        )

        async def _on_bar(bar) -> None:
            if not self._running:
                return
            ts_ms = int(bar.timestamp.timestamp() * 1000)
            ohlcv = [
                ts_ms,
                float(bar.open),
                float(bar.high),
                float(bar.low),
                float(bar.close),
                float(bar.volume),
            ]
            self.builder.process_ohlcv(ohlcv, is_closed=True)

        stream.subscribe_bars(_on_bar, self._symbol)

        try:
            await asyncio.wait_for(stream._run_forever(), timeout=None)
        except asyncio.CancelledError:
            self._running = False
        finally:
            try:
                await stream.close()
            except Exception:
                pass
