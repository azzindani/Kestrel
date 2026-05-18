"""
Layer 2 — mock market data feed.

Generates synthetic OHLCV candles via a deterministic random walk.  Used for:
    - Development on geo-restricted VPS where real exchange REST/WS endpoints
      are blocked.
    - Algorithm refinement where deterministic data is preferable to live
      market noise.
    - Verifying the full daemon → DB → dashboard pipeline without any real
      exchange dependency.

Usage:
    .env:
        EXCHANGE=mock
        API_KEY=<any non-empty placeholder>
        API_SECRET=<any non-empty placeholder>
        PAIR=BTC/USDT          # any string; the mock honours TIMEFRAME_ENTRY
        MOCK_SECONDS_PER_CANDLE=1   # optional, default 1 (accelerated)
        MOCK_SEED=42                # optional random seed for reproducibility
        MOCK_START_PRICE=77000      # optional starting price

Each `subscribe()` registers a synthetic stream; each emits one closed
candle every ``MOCK_SECONDS_PER_CANDLE`` seconds.  The candle's timestamp
follows real wall-clock time aligned to the timeframe (so historical
candles look correct on the dashboard).
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.config import AppConfig
from src.data.candle_builder import CandleBuilder
from src.data.providers import register_feed

NotifyFn = Callable[[str, str], None]
ReconnectFn = Callable[[int], None]

# Map timeframe strings → seconds
_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}


@dataclass(slots=True)
class _Subscription:
    pair: str
    timeframe: str
    builder: CandleBuilder
    on_reconnect: Optional[ReconnectFn]
    notify: Optional[NotifyFn]
    # Per-subscription random walk state
    price: float = 0.0
    rng: random.Random = field(default_factory=random.Random)


@register_feed("mock")
class MockFeed:
    """Synthetic OHLCV feed for development and algorithm refinement.

    Multiple bots can share one MockFeed via subscribe(); each gets its own
    deterministic random-walk state seeded from MOCK_SEED + pair name so
    each pair behaves consistently across restarts.
    """

    last_reconnect_ts: Optional[int]

    def __init__(
        self,
        cfg: AppConfig,
        pair: Optional[str] = None,
        timeframe: Optional[str] = None,
        builder: Optional[CandleBuilder] = None,
        on_reconnect: Optional[ReconnectFn] = None,
        notify: Optional[NotifyFn] = None,
    ) -> None:
        self.cfg = cfg
        self._subscriptions: list[_Subscription] = []
        self._running = False
        self._stop_event: Optional[asyncio.Event] = None
        self.last_reconnect_ts = None

        # Pacing
        self._seconds_per_candle = float(
            os.environ.get("MOCK_SECONDS_PER_CANDLE", "1.0")
        )
        self._base_seed = int(os.environ.get("MOCK_SEED", "42"))
        self._start_price = float(os.environ.get("MOCK_START_PRICE", "77000.0"))

        # Allow legacy single-shot construction; emulate subscribe immediately.
        if pair is not None and builder is not None and timeframe is not None:
            self.subscribe(pair, timeframe, builder, on_reconnect, notify)

    def subscribe(
        self,
        pair: str,
        timeframe: str,
        builder: CandleBuilder,
        on_reconnect: Optional[ReconnectFn] = None,
        notify: Optional[NotifyFn] = None,
    ) -> None:
        seed = hash((self._base_seed, pair)) & 0x7FFFFFFF
        rng = random.Random(seed)
        # Per-pair start price has a small offset so multiple bots show
        # distinct charts.
        offset = rng.uniform(0.95, 1.05)
        self._subscriptions.append(
            _Subscription(
                pair=pair,
                timeframe=timeframe,
                builder=builder,
                on_reconnect=on_reconnect,
                notify=notify,
                price=self._start_price * offset,
                rng=rng,
            )
        )

    @property
    def subscriptions(self) -> list[_Subscription]:
        return list(self._subscriptions)

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    async def run(self) -> None:
        """Drive all subscriptions.  Idempotent."""
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        if self._running:
            await self._stop_event.wait()
            return
        self._running = True
        try:
            await self._drive()
        finally:
            self._stop_event.set()
            self._running = False

    async def _drive(self) -> None:
        """Emit one candle per subscription per tick until stopped."""
        spawned: set[tuple[str, str]] = set()
        tasks: list[asyncio.Task] = []
        try:
            while not self._stop_event.is_set():
                for sub in list(self._subscriptions):
                    key = (sub.pair, sub.timeframe)
                    if key in spawned:
                        continue
                    spawned.add(key)
                    tasks.append(
                        asyncio.create_task(
                            self._stream_one(sub), name=f"mock:{key[0]}/{key[1]}"
                        )
                    )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=0.5)
                    break
                except asyncio.TimeoutError:
                    continue
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            return

    def _next_candle(self, sub: _Subscription, ts_ms: int) -> list:
        """Generate one synthetic OHLCV row for sub at timestamp ts_ms."""
        open_price = sub.price
        # Random walk with occasional momentum bursts (~5% are "anomalies").
        drift = sub.rng.gauss(0.0, 1.0) * open_price * 0.0015
        if sub.rng.random() < 0.05:
            drift *= 4
        close_price = max(open_price + drift, open_price * 0.5)
        high = max(open_price, close_price) * (1.0 + abs(sub.rng.gauss(0.0, 0.0005)))
        low = min(open_price, close_price) * (1.0 - abs(sub.rng.gauss(0.0, 0.0005)))
        volume = math.exp(sub.rng.gauss(5.0, 0.6))
        sub.price = close_price
        return [ts_ms, open_price, high, low, close_price, volume]

    async def _stream_one(self, sub: _Subscription) -> None:
        """Emit synthetic candles for this subscription.

        Phase 1 (backfill): emit MOCK_BACKFILL_CANDLES candles whose timestamps
        cover the recent past up to the current timeframe boundary, paced at
        MOCK_SECONDS_PER_CANDLE so the dashboard fills in quickly on startup.

        Phase 2 (live): emit one candle per *real* timeframe interval at real
        wall-clock timestamps so the dashboard's NOW()-based filters work
        correctly.
        """
        tf_seconds = _TIMEFRAME_SECONDS.get(sub.timeframe, 300)
        tf_ms = tf_seconds * 1000
        backfill_count = int(os.environ.get("MOCK_BACKFILL_CANDLES", "288"))  # 24h @ 5m

        # Align to the most recent COMPLETED candle boundary.
        now_ms = int(time.time() * 1000)
        next_close_ms = (now_ms // tf_ms) * tf_ms  # most recent boundary <= now

        # Phase 1: emit backfill_count candles ending at next_close_ms.
        backfill_start_ms = next_close_ms - backfill_count * tf_ms
        for i in range(backfill_count):
            if self._stop_event.is_set():
                return
            ts = backfill_start_ms + (i + 1) * tf_ms
            ohlcv = self._next_candle(sub, ts)
            sub.builder.process_ohlcv(ohlcv, is_closed=True)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._seconds_per_candle
                )
                return
            except asyncio.TimeoutError:
                pass

        # Phase 2: real-time pacing. Each iteration waits until the next
        # candle boundary in wall-clock time, then emits one candle for that
        # boundary.
        last_emitted = next_close_ms
        while not self._stop_event.is_set():
            now_ms = int(time.time() * 1000)
            next_close = ((now_ms // tf_ms) + 1) * tf_ms
            sleep_s = max(0.0, (next_close - now_ms) / 1000.0)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_s)
                return
            except asyncio.TimeoutError:
                pass
            # Emit the candle that just closed.
            if next_close > last_emitted:
                ohlcv = self._next_candle(sub, next_close)
                sub.builder.process_ohlcv(ohlcv, is_closed=True)
                last_emitted = next_close
