"""
Layer 2 — Interactive Brokers market data feed.

Streams live OHLCV bars via ib_insync's real-time bar subscription
(IB reqRealTimeBars API: 5-second bars aggregated to the requested timeframe).

Prerequisites:
    - IB Gateway or TWS running with API enabled
    - ib_insync installed: pip install ib_insync

Setup (.env):
    EXCHANGE=ibkr
    API_KEY=127.0.0.1:7497          # TWS/Gateway host:port
    API_SECRET=2                     # IB client ID (must differ from execution client ID)
    TESTNET=true
    PAIR=BTC.USD                     # IBKR contract format

Reconnection policy: exponential backoff per CLAUDE.md §10.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

from src.config import AppConfig
from src.data.candle_builder import CandleBuilder
from src.data.providers import register_feed

_MAX_RETRIES = 5
_BACKOFF_BASE = 2

# Timeframe → seconds per bar for aggregation
_INTERVAL_MAP: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# Timeframe → IB barSizeSetting string
_IB_BARSIZE: dict[str, str] = {
    "1m": "1 min",
    "5m": "5 mins",
    "15m": "15 mins",
    "30m": "30 mins",
    "1h": "1 hour",
    "4h": "4 hours",
    "1d": "1 day",
}


def _parse_host_port(api_key: str) -> tuple[str, int]:
    if ":" in api_key:
        host, port_str = api_key.rsplit(":", 1)
        return host, int(port_str)
    return api_key, 7497


def _make_contract(pair: str):
    """Build an ib_insync Contract for the given pair string."""
    try:
        from ib_insync import Crypto, Forex, Stock
    except ImportError as exc:
        raise ImportError("ib_insync required: pip install ib_insync") from exc

    if "." in pair:
        symbol, currency = pair.split(".", 1)
    else:
        symbol, currency = pair, "USD"

    crypto_symbols = {
        "BTC",
        "ETH",
        "SOL",
        "ADA",
        "XRP",
        "DOGE",
        "LTC",
        "BCH",
        "LINK",
        "DOT",
        "MATIC",
        "AVAX",
        "UNI",
        "ATOM",
    }
    if symbol.upper() in crypto_symbols:
        return Crypto(symbol.upper(), "PAXOS", currency.upper())

    forex_currencies = {
        "EUR",
        "GBP",
        "JPY",
        "CHF",
        "AUD",
        "NZD",
        "CAD",
        "SEK",
        "NOK",
        "DKK",
        "SGD",
        "HKD",
    }
    if symbol.upper() in forex_currencies:
        return Forex(symbol.upper() + currency.upper())

    return Stock(symbol.upper(), "SMART", currency.upper())


@register_feed("ibkr")
class IBKRFeed:
    """IBKR real-time bar feed via ib_insync.

    Uses reqRealTimeBars (5-second updates) internally.  The CandleBuilder
    receives completed candles at the configured timeframe boundary.

    Note: IB data feed uses a separate client ID from the execution provider
    to avoid conflicts.  Recommended: execution=1, feed=2 (set in API_SECRET).
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
        self._host, self._port = _parse_host_port(cfg.api_key)
        # Feed uses client_id + 1 to avoid collision with execution client
        raw_client_id = int(cfg.api_secret) if cfg.api_secret.isdigit() else 1
        self._client_id = raw_client_id + 1
        self._interval = _INTERVAL_MAP.get(timeframe, 300)
        self._bar_size = _IB_BARSIZE.get(timeframe, "5 mins")

    @property
    def last_reconnect_ts(self) -> Optional[int]:
        return self._last_reconnect_ts

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Connect and stream bars with exponential backoff on failure."""
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
                        f"IBKR feed {self.pair}/{self.timeframe} exceeded max retries "
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
                        f"IBKR feed {self.pair}/{self.timeframe} disconnected "
                        f"(attempt {retry_count}/{_MAX_RETRIES}). "
                        f"Reconnecting in {delay}s. err={exc}",
                    )
                await asyncio.sleep(delay)

    async def _stream(self, retry_count: int) -> None:
        """Inner stream: connect IB, subscribe to real-time bars, aggregate."""
        try:
            from ib_insync import IB
        except ImportError as exc:
            raise ImportError("ib_insync is required for the IBKR feed. Install with: pip install ib_insync") from exc

        if retry_count > 0:
            ts_ms = int(time.time() * 1000)
            self._last_reconnect_ts = ts_ms
            if self._on_reconnect:
                self._on_reconnect(ts_ms)

        ib = IB()
        await ib.connectAsync(self._host, self._port, clientId=self._client_id)

        contract = _make_contract(self.pair)
        await ib.qualifyContractsAsync(contract)

        # Accumulator for aggregating 5-second IB bars into the target timeframe
        _agg: dict[str, Any] = {}

        def _on_bar(bars, has_new_bar: bool) -> None:
            if not self._running or not bars:
                return
            bar = bars[-1]
            bar_ts_ms = int(bar.time.timestamp() * 1000)
            # Align to candle boundary
            candle_ts_ms = (bar_ts_ms // (self._interval * 1000)) * (self._interval * 1000)

            if "ts" not in _agg or _agg["ts"] != candle_ts_ms:
                # Flush completed candle
                if "ts" in _agg:
                    ohlcv = [
                        _agg["ts"],
                        _agg["open"],
                        _agg["high"],
                        _agg["low"],
                        _agg["close"],
                        _agg["volume"],
                    ]
                    self.builder.process_ohlcv(ohlcv, is_closed=True)
                # Start new candle
                _agg.clear()
                _agg.update(
                    {
                        "ts": candle_ts_ms,
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": float(bar.volume),
                    }
                )
            else:
                # Update running candle
                _agg["high"] = max(_agg["high"], float(bar.high))
                _agg["low"] = min(_agg["low"], float(bar.low))
                _agg["close"] = float(bar.close)
                _agg["volume"] = _agg.get("volume", 0.0) + float(bar.volume)

        bars = ib.reqRealTimeBars(contract, 5, "MIDPOINT", False)
        bars.updateEvent += _on_bar

        try:
            while self._running:
                await asyncio.sleep(1)
                ib.sleep(0)
        finally:
            ib.cancelRealTimeBars(bars)
            ib.disconnect()
