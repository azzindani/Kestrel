"""
Layer 2 — Exness market data feed (via MetaApi cloud), shared multi-subscription.

Exness has no native OHLCV WebSocket; MetaApi exposes the MT5 account's candles.
This feed polls MetaApi's historical-candles endpoint once per timeframe interval
and emits each newly-completed candle to the CandleBuilder.

Shared like the ccxt MarketFeed: ONE instance per account serves every bot, with
one poller per unique (pair, timeframe). Each closed candle fans out to all bots
subscribed to that stream — so a large hyperparameter lab on a handful of symbols
needs only one MetaApi poller per symbol, not one per bot, which keeps it inside
the single-account / free-tier limit. (Exness execution stays per-bot; in ENV=dev
fills are simulated so only this feed touches MetaApi.)

Setup (.env):
    EXCHANGE=exness
    API_KEY=<metaapi-auth-token>
    API_SECRET=<metaapi-account-id>
    TESTNET=true
    PAIR=XAUUSD                 # MT5 symbol — XAUUSD · USOIL · BTCUSD · ...
    TIMEFRAME_ENTRY=5m          # supported: 1m 5m 15m 30m 1h 4h 1d
    TIMEFRAME_REGIME=15m

Dependency (optional): pip install metaapi-cloud-sdk

Reconnection policy: matches CLAUDE.md §10 (exponential backoff, max 5 retries).

Note: uses MetaApi ``account.get_historical_candles``. The account must have
historical market data enabled (default for deployed MetaApi accounts). If a
broker/region lacks it, switch to the streaming candle-subscription variant.
"""

from __future__ import annotations

import asyncio
import datetime
import time
from dataclasses import dataclass
from typing import Callable, Optional

from src.config import AppConfig
from src.data.candle_builder import CandleBuilder
from src.data.providers import register_feed

_MAX_RETRIES = 5
_BACKOFF_BASE = 2

# Kestrel timeframe → MetaApi timeframe string. MetaApi uses the same lowercase
# tokens for the ones this bot uses; the map is explicit for safety/validation.
_TF_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

# Timeframe string → interval seconds
_INTERVAL_MAP: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def _to_metaapi_tf(timeframe: str) -> str:
    return _TF_MAP.get(timeframe, timeframe.lower())


def _to_interval(timeframe: str) -> int:
    return _INTERVAL_MAP.get(timeframe, 300)


def _candle_time_to_ms(value) -> int:
    """Convert a MetaApi candle 'time' (datetime or ISO8601 string) to Unix ms."""
    if isinstance(value, datetime.datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


@dataclass(slots=True)
class _Subscription:
    pair: str
    timeframe: str
    builder: CandleBuilder
    on_reconnect: Optional[Callable[[int], None]]
    notify: Optional[Callable[[str, str], None]]


@register_feed("exness")
class ExnessFeed:
    """Shared Exness/MetaApi candle feed; one instance per account serves N bots.

    One MetaApi historical-candle poller runs per unique (pair, timeframe); each
    completed candle fans out to every bot subscribed to that stream.
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._subscriptions: list[_Subscription] = []
        self._streaming = False
        self._stop_event: Optional[asyncio.Event] = None
        self._last_reconnect_ts: Optional[int] = None
        self._account = None

    @property
    def last_reconnect_ts(self) -> Optional[int]:
        return self._last_reconnect_ts

    def subscribe(
        self,
        pair: str,
        timeframe: str,
        builder: CandleBuilder,
        on_reconnect: Optional[Callable[[int], None]] = None,
        notify: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """Register a (pair, timeframe) stream with this shared feed."""
        self._subscriptions.append(_Subscription(pair, timeframe, builder, on_reconnect, notify))

    @property
    def subscriptions(self) -> list[_Subscription]:
        """Read-only view of current subscriptions (for tests/diagnostics)."""
        return list(self._subscriptions)

    def _subs_for(self, pair: str, timeframe: str) -> list[_Subscription]:
        return [s for s in self._subscriptions if s.pair == pair and s.timeframe == timeframe]

    async def run(self) -> None:
        """Drive polling. Idempotent across multiple callers (shared feed)."""
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        if self._streaming:
            await self._stop_event.wait()
            return
        self._streaming = True
        try:
            await self._poll_all()
        finally:
            self._stop_event.set()
            self._streaming = False

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    async def _ensure_account(self):
        if self._account is not None:
            return self._account
        try:
            from metaapi_cloud_sdk import MetaApi
        except ImportError as exc:
            raise ImportError(
                "metaapi-cloud-sdk is required for the Exness feed. Install it with: pip install metaapi-cloud-sdk"
            ) from exc
        api = MetaApi(self.cfg.api_key)
        account = await api.metatrader_account_api.get_account(self.cfg.api_secret)
        await account.wait_connected()
        self._account = account
        return account

    async def _poll_all(self) -> None:
        """Spawn one poller task per unique (pair, timeframe), observed dynamically."""
        account = await self._ensure_account()
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
                                self._poll_one(account, key[0], key[1]),
                                name=f"exness:{key[0]}/{key[1]}",
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
            self._account = None

    async def _poll_one(self, account, pair: str, timeframe: str) -> None:
        """Per-(pair, timeframe) poll loop with exponential backoff."""
        metaapi_tf = _to_metaapi_tf(timeframe)
        interval = _to_interval(timeframe)
        interval_ms = interval * 1000
        last_candle_ts: Optional[int] = None
        retry_count = 0

        while not self._stop_event.is_set():
            try:
                if retry_count > 0:
                    ts_ms = int(time.time() * 1000)
                    self._last_reconnect_ts = ts_ms
                    for sub in self._subs_for(pair, timeframe):
                        if sub.on_reconnect:
                            sub.on_reconnect(ts_ms)

                while not self._stop_event.is_set():
                    candles = await account.get_historical_candles(pair, metaapi_tf, None, 3)
                    now_ms = int(time.time() * 1000)
                    for raw in candles or []:
                        ts_ms = _candle_time_to_ms(raw.get("time"))
                        # Only emit once the candle's period has fully elapsed.
                        if ts_ms + interval_ms > now_ms:
                            continue
                        if last_candle_ts is not None and ts_ms <= last_candle_ts:
                            continue
                        ohlcv = [
                            ts_ms,
                            float(raw.get("open", 0.0)),
                            float(raw.get("high", 0.0)),
                            float(raw.get("low", 0.0)),
                            float(raw.get("close", 0.0)),
                            float(raw.get("tickVolume", raw.get("volume", 0.0)) or 0.0),
                        ]
                        for sub in self._subs_for(pair, timeframe):
                            sub.builder.process_ohlcv(ohlcv, is_closed=True)
                        last_candle_ts = ts_ms
                    await asyncio.sleep(interval * 0.9)
                retry_count = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:
                retry_count += 1
                notify = next((s.notify for s in self._subs_for(pair, timeframe) if s.notify), None)
                if retry_count > _MAX_RETRIES:
                    if notify:
                        notify(
                            "CRITICAL",
                            f"Exness feed {pair}/{timeframe} exceeded max retries ({_MAX_RETRIES}). Last error: {exc}",
                        )
                    await asyncio.sleep(60)
                    retry_count = 0
                    continue
                delay = _BACKOFF_BASE**retry_count
                if notify:
                    notify(
                        "WARN",
                        f"Exness feed {pair}/{timeframe} error "
                        f"(attempt {retry_count}/{_MAX_RETRIES}). "
                        f"Reconnecting in {delay}s. err={exc}",
                    )
                await asyncio.sleep(delay)
