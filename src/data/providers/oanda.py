"""
Layer 2 — OANDA market data feed.

Polls the OANDA v20 Instruments/Candles REST endpoint to supply OHLCV data to
the CandleBuilder.  OANDA does not provide a native OHLCV WebSocket; the REST
poll matches the candle timeframe interval so each completed candle is delivered
within one interval period.

Setup (.env):
    EXCHANGE=oanda
    API_KEY=<v20-access-token>
    API_SECRET=<oanda-account-id>
    TESTNET=true
    PAIR=EUR_USD
    TIMEFRAME_ENTRY=5m          # supported: M1 M5 M15 M30 H1 H4 D
    TIMEFRAME_REGIME=15m

Reconnection policy: matches CLAUDE.md §10 (exponential backoff, max 5 retries).
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

# Timeframe string → interval seconds
_INTERVAL_MAP: dict[str, int] = {
    "M1": 60, "1m": 60,
    "M5": 300, "5m": 300,
    "M15": 900, "15m": 900,
    "M30": 1800, "30m": 1800,
    "H1": 3600, "1h": 3600,
    "H4": 14400, "4h": 14400,
    "D": 86400, "1d": 86400,
}

# Kestrel timeframe string → OANDA granularity string
_GRANULARITY_MAP: dict[str, str] = {
    "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
    "1h": "H1", "4h": "H4", "1d": "D",
}


def _to_granularity(timeframe: str) -> str:
    return _GRANULARITY_MAP.get(timeframe, timeframe.upper())


def _to_interval(timeframe: str) -> int:
    return _INTERVAL_MAP.get(timeframe, _INTERVAL_MAP.get(timeframe.upper(), 300))


@register_feed("oanda")
class OandaFeed:
    """OANDA candle feed via REST polling.

    Fetches the last 2 completed candles every ``interval`` seconds.
    The most recently completed candle is emitted as is_closed=True.
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
        self._last_candle_ts: Optional[int] = None
        self._granularity = _to_granularity(timeframe)
        self._interval = _to_interval(timeframe)

    @property
    def last_reconnect_ts(self) -> Optional[int]:
        return self._last_reconnect_ts

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Poll loop with exponential backoff on failure."""
        self._running = True
        retry_count = 0

        while self._running:
            try:
                await self._poll_loop(retry_count)
                retry_count = 0
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as exc:
                retry_count += 1
                if retry_count > _MAX_RETRIES:
                    msg = (
                        f"OANDA feed {self.pair}/{self.timeframe} exceeded max retries "
                        f"({_MAX_RETRIES}). Last error: {exc}"
                    )
                    if self._notify:
                        self._notify("CRITICAL", msg)
                    await asyncio.sleep(60)
                    retry_count = 0
                    continue

                delay = _BACKOFF_BASE ** retry_count
                if self._notify:
                    self._notify(
                        "WARN",
                        f"OANDA feed {self.pair}/{self.timeframe} error "
                        f"(attempt {retry_count}/{_MAX_RETRIES}). "
                        f"Reconnecting in {delay}s. err={exc}",
                    )
                await asyncio.sleep(delay)

    async def _poll_loop(self, retry_count: int) -> None:
        """Inner polling loop."""
        try:
            from oandapyV20 import API
            from oandapyV20.endpoints.instruments import InstrumentsCandles
        except ImportError as exc:
            raise ImportError(
                "oandapyV20 is required for the OANDA feed. "
                "Install with: pip install oandapyV20"
            ) from exc

        environment = "practice" if self.cfg.testnet else "live"
        client = API(access_token=self.cfg.api_key, environment=environment)

        if retry_count > 0:
            ts_ms = int(time.time() * 1000)
            self._last_reconnect_ts = ts_ms
            if self._on_reconnect:
                self._on_reconnect(ts_ms)

        while self._running:
            params = {
                "count": 3,
                "granularity": self._granularity,
                "price": "M",  # midpoint
            }
            req = InstrumentsCandles(self.pair, params=params)
            try:
                resp = client.request(req)
            except Exception as exc:
                raise RuntimeError(f"OANDA candles request failed: {exc}") from exc

            candles = resp.get("candles", [])
            for raw in candles:
                if not raw.get("complete", False):
                    continue
                ts_ms = int(raw["time"][:19].replace("-", "").replace("T", "").replace(":", "")) * 1000
                # Parse ISO8601 → epoch ms properly
                ts_ms = _parse_oanda_ts(raw["time"])
                if self._last_candle_ts is not None and ts_ms <= self._last_candle_ts:
                    continue
                mid = raw.get("mid", {})
                ohlcv = [
                    ts_ms,
                    float(mid.get("o", 0)),
                    float(mid.get("h", 0)),
                    float(mid.get("l", 0)),
                    float(mid.get("c", 0)),
                    float(raw.get("volume", 0)),
                ]
                self.builder.process_ohlcv(ohlcv, is_closed=True)
                self._last_candle_ts = ts_ms

            await asyncio.sleep(self._interval * 0.9)


def _parse_oanda_ts(iso: str) -> int:
    """Parse OANDA ISO8601 timestamp string to Unix milliseconds.

    OANDA format: "2025-04-16T14:30:00.000000000Z"
    """
    import datetime
    # Strip nanoseconds beyond microseconds precision
    trimmed = iso[:26].rstrip("Z").rstrip("0").rstrip(".")
    if "." not in trimmed:
        trimmed += ""
    dt = datetime.datetime.fromisoformat(trimmed).replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)
