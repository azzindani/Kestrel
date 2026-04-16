"""
Layer 2 — candle builder.

Receives raw OHLCV data (from ccxt WebSocket) and emits completed Candle
objects with precomputed geometry and indicators.

Maintains an in-memory rolling buffer (bootstrapped from DB on startup) used
for indicator computation. The authoritative store is always the DB.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Callable, Optional, Sequence

from src.config import (
    AppConfig, Candle, Params, compute_candle_geometry
)
from src.signal.indicators import compute_all_indicators

# Minimum candle history for reliable indicator computation
_BUFFER_SIZE = 120
# Emitter type: async callback called with each completed Candle
CandleEmitter = Callable[[Candle], None]


class CandleBuilder:
    """Assembles OHLCV data into completed Candle objects.

    On candle close:
        1. Compute geometry (body, wicks, etc.)
        2. Compute all indicators from rolling buffer
        3. Build Candle domain object
        4. Append to buffer
        5. Call the registered emitter

    The emitter is typically the daemon's candle_processor which writes to DB
    and runs the signal pipeline.
    """

    def __init__(
        self,
        bot_id: str,
        pair: str,
        timeframe: str,
        params: Params,
    ) -> None:
        self.bot_id = bot_id
        self.pair = pair
        self.timeframe = timeframe
        self.params = params
        self._buffer: deque[Candle] = deque(maxlen=_BUFFER_SIZE)
        self._emitter: Optional[CandleEmitter] = None
        self._last_ts: Optional[int] = None  # ts of last known candle

    def set_emitter(self, emitter: CandleEmitter) -> None:
        """Register the callback invoked when a candle completes."""
        self._emitter = emitter

    def bootstrap(self, historical: Sequence[Candle]) -> None:
        """Pre-populate the buffer from DB history (called at daemon startup)."""
        for c in historical[-_BUFFER_SIZE:]:
            self._buffer.append(c)
        if self._buffer:
            self._last_ts = self._buffer[-1].ts

    def process_ohlcv(self, ohlcv: list, is_closed: bool) -> None:
        """Process a single OHLCV row from the WebSocket feed.

        Args:
            ohlcv:     [timestamp_ms, open, high, low, close, volume]
            is_closed: True when this candle is finalised (no more updates).
        """
        ts, open_, high, low, close, volume = ohlcv

        if not is_closed:
            return  # ignore live/partial candles

        # Deduplicate — may receive the same closed candle multiple times
        if ts == self._last_ts:
            return

        geom = compute_candle_geometry(open_, high, low, close)

        # Build a preliminary Candle (indicators computed from buffer below)
        raw_candle = Candle(
            bot_id=self.bot_id,
            ts=int(ts),
            pair=self.pair,
            timeframe=self.timeframe,
            open=float(open_),
            high=float(high),
            low=float(low),
            close=float(close),
            volume=float(volume),
            body_size=geom["body_size"],
            total_range=geom["total_range"],
            body_ratio=geom["body_ratio"],
            upper_wick=geom["upper_wick"],
            lower_wick=geom["lower_wick"],
            direction=geom["direction"],
        )

        # Append to buffer temporarily to include current candle in computation
        self._buffer.append(raw_candle)
        indicators = compute_all_indicators(
            list(self._buffer),
            ema_fast=self.params.ema_fast,
            ema_slow=self.params.ema_slow,
        )

        # Rebuild with indicators
        candle = Candle(
            bot_id=self.bot_id,
            ts=raw_candle.ts,
            pair=self.pair,
            timeframe=self.timeframe,
            open=raw_candle.open,
            high=raw_candle.high,
            low=raw_candle.low,
            close=raw_candle.close,
            volume=raw_candle.volume,
            ema9=indicators.get("ema9"),
            ema21=indicators.get("ema21"),
            rsi14=indicators.get("rsi14"),
            atr14=indicators.get("atr14"),
            bb_upper=indicators.get("bb_upper"),
            bb_lower=indicators.get("bb_lower"),
            bb_width=indicators.get("bb_width"),
            adx=indicators.get("adx"),
            volume_ma20=indicators.get("volume_ma20"),
            volume_ratio=indicators.get("volume_ratio"),
            body_size=raw_candle.body_size,
            total_range=raw_candle.total_range,
            body_ratio=raw_candle.body_ratio,
            upper_wick=raw_candle.upper_wick,
            lower_wick=raw_candle.lower_wick,
            direction=raw_candle.direction,
        )

        # Replace the preliminary candle with the indicator-enriched one
        self._buffer[-1] = candle
        self._last_ts = ts

        if self._emitter is not None:
            self._emitter(candle)

    @property
    def buffer(self) -> list[Candle]:
        """Return a copy of the current candle buffer."""
        return list(self._buffer)
