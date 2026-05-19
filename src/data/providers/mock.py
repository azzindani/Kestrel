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
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass(slots=True)
class _Subscription:
    pair: str
    timeframe: str
    builder: CandleBuilder
    on_reconnect: Optional[ReconnectFn]
    notify: Optional[NotifyFn]
    # Per-subscription random walk + regime state
    price: float = 0.0
    rng: random.Random = field(default_factory=random.Random)
    # Regime simulation: each subscription cycles through phases that produce
    # candles matching one of TRENDING / RANGING / VOLATILE structurally, so
    # the signal pipeline has material to detect.
    phase: str = "range"  # "trend_up" | "trend_down" | "range" | "volatile"
    phase_candles_left: int = 0
    # Inside trend phases, alternate impulse and retracement to produce the
    # trigger+retrace structure impulse_retracement looks for, and to keep RSI
    # out of the overbought/oversold bands so the trend filter accepts.
    candles_since_retrace: int = 0
    last_was_impulse: bool = False


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
        self._seconds_per_candle = float(os.environ.get("MOCK_SECONDS_PER_CANDLE", "1.0"))
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
                    tasks.append(asyncio.create_task(self._stream_one(sub), name=f"mock:{key[0]}/{key[1]}"))
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
        """Generate one synthetic OHLCV row for sub at timestamp ts_ms.

        Walks a regime-state machine: each subscription cycles between
        trending, ranging, and volatile phases. Inside trend phases, an
        impulse → retracement sub-structure alternates every 3-4 candles —
        this both keeps RSI inside the trend-filter band (45 ≤ RSI ≤ 70)
        and creates the exact trigger-then-retrace shape the
        impulse_retracement pattern detects.

        Phase distribution and shapes are tuned against CLAUDE.md §22 gates:
            - trend_up / trend_down (≈70%): 15-30 candles, drift ±0.30%,
              with retracement every 3-4 candles → builds ADX>25 + EMA
              spread + clean impulse/retrace pairs.
            - range (≈25%): 5-12 candles, drift ~0%, lower body_ratio,
              moderate volatility (so quiet_regime gate doesn't trip).
            - volatile (≈5%): 1-3 single-candle spikes with 4× normal range
              and 3× volume — feeds VOLATILE + anomaly_fade.
        """
        # Phase transitions. Heavily favour range so the volume MA stays low,
        # then short trend bursts produce trigger/retrace pairs with ratios
        # well above volume_ratio_min when measured against the range-dominated
        # MA(20).
        if sub.phase_candles_left <= 0:
            r = sub.rng.random()
            if r < 0.12:
                sub.phase = "trend_up"
                sub.phase_candles_left = sub.rng.randint(8, 14)
            elif r < 0.24:
                sub.phase = "trend_down"
                sub.phase_candles_left = sub.rng.randint(8, 14)
            elif r < 0.94:
                sub.phase = "range"
                sub.phase_candles_left = sub.rng.randint(20, 40)
            else:
                sub.phase = "volatile"
                sub.phase_candles_left = sub.rng.randint(1, 3)
            sub.candles_since_retrace = 0
            sub.last_was_impulse = False
        sub.phase_candles_left -= 1

        open_price = sub.price
        in_trend = sub.phase in ("trend_up", "trend_down")
        trend_sign = 1 if sub.phase == "trend_up" else (-1 if sub.phase == "trend_down" else 0)

        # Decide impulse vs retrace inside trend phases.
        is_retrace = False
        if in_trend:
            sub.candles_since_retrace += 1
            # After 2-3 impulses, do a retracement candle. Forced after 4.
            if sub.last_was_impulse and (
                sub.candles_since_retrace >= 4 or (sub.candles_since_retrace >= 2 and sub.rng.random() < 0.45)
            ):
                is_retrace = True

        if in_trend and not is_retrace:
            # Impulse candle: large body in trend direction, high volume —
            # well above the range-dominated MA(20).
            drift_pct = trend_sign * sub.rng.uniform(0.0035, 0.0060)
            body_frac = sub.rng.uniform(0.72, 0.90)
            vol_mean, vol_sigma = 6.5, 0.25  # ~6× range volume
        elif in_trend and is_retrace:
            # Retracement candle: small opposite-direction move (~35% of
            # impulse range), volume lower than trigger but still ~2.5× the
            # range baseline so volume_confirm passes after impulse_retracement.
            drift_pct = -trend_sign * sub.rng.uniform(0.0010, 0.0022)
            body_frac = sub.rng.uniform(0.40, 0.65)
            vol_mean, vol_sigma = 5.6, 0.25
            sub.candles_since_retrace = 0
        elif sub.phase == "volatile":
            direction = 1 if sub.rng.random() < 0.5 else -1
            drift_pct = direction * sub.rng.uniform(0.010, 0.020)
            body_frac = sub.rng.uniform(0.5, 0.75)
            vol_mean, vol_sigma = 6.8, 0.4
        else:  # range — low body_ratio, low volume; dominates the buffer
            drift_pct = sub.rng.gauss(0.0, 0.0010)
            body_frac = sub.rng.uniform(0.15, 0.40)
            vol_mean, vol_sigma = 4.7, 0.30

        sub.last_was_impulse = in_trend and not is_retrace

        close_price = max(open_price * (1.0 + drift_pct), open_price * 0.5)

        # Construct high/low so |close-open| / (high-low) ≈ body_frac.
        body = abs(close_price - open_price)
        if body == 0.0:
            body = open_price * 0.0001
        total_range = body / max(body_frac, 0.05)
        wick_total = total_range - body
        # Split wicks asymmetrically — for trend_up impulses weight the
        # *upper* wick small (close near high); for trend_down, lower wick small.
        if trend_sign >= 0 or (sub.phase == "volatile" and drift_pct > 0):
            upper_wick = wick_total * sub.rng.uniform(0.1, 0.4)
            lower_wick = wick_total - upper_wick
        else:
            lower_wick = wick_total * sub.rng.uniform(0.1, 0.4)
            upper_wick = wick_total - lower_wick

        high = max(open_price, close_price) + upper_wick
        low = min(open_price, close_price) - lower_wick

        volume = math.exp(sub.rng.gauss(vol_mean, vol_sigma))

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
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._seconds_per_candle)
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
