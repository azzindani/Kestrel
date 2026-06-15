"""
Layer 2 — REST-polling market-data feed (ccxt.async_support).

Alternative to the WebSocket ``MarketFeed`` for HIGH timeframes. A 4h candle
close is a single event every four hours; the ccxt.pro WS rollover push can be
sparse on thin pairs or missed during a reconnect, silently losing the close
(observed 2026-06-14: a 4h lab made zero trades for a day because the WS never
emitted the rollover). Polling ``fetch_ohlcv`` on a fixed interval detects each
newly-CLOSED candle deterministically and emits it exactly once, surviving
transient errors and reconnects.

Selected via ``cfg.feed_mode == "poll"`` (see ``get_data_feed``). Implements the
FeedProtocol and shares one ccxt REST client across all subscriptions, mirroring
how ``MarketFeed`` shares its WS client. Volume is converted base→quote (× close)
to match the ccxt.pro WS feed's quote-volume scale (and the backfill), so
``volume_ratio`` stays consistent across the two transports.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from src.config import AppConfig
from src.data.candle_builder import CandleBuilder

NotifyFn = Callable[[str, str], None]
ReconnectFn = Callable[[int], None]
LogEventFn = Callable[[str, str, dict], None]

# How often to poll for a new closed candle. 60s is ample for >=1h timeframes
# (the close is detected within a minute of the boundary) and cheap on rate limits.
_POLL_INTERVAL_S = 60
# Rows to fetch each poll — a few, so a brief outage spanning >1 close still
# backfills every missed candle on recovery.
_POLL_LIMIT = 5

_TF_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def _tf_ms(timeframe: str) -> int:
    if timeframe not in _TF_MS:
        raise ValueError(f"PollingFeed: unsupported timeframe '{timeframe}'")
    return _TF_MS[timeframe]


def new_closed_rows(
    rows: Sequence[list],
    period_ms: int,
    now_ms: int,
    last_emitted: Optional[int],
) -> list[list]:
    """Pure: select the closed OHLCV rows to emit, in ascending ts order.

    A row [ts, o, h, l, c, v] is CLOSED when ts + period_ms <= now_ms (the next
    period has begun). Returns:
      * [] if nothing new has closed,
      * only the most-recent closed row when last_emitted is None (first poll with
        no bootstrap — seed without replaying history),
      * every closed row with ts > last_emitted otherwise.
    """
    closed = [r for r in rows if int(r[0]) + period_ms <= now_ms]
    if not closed:
        return []
    if last_emitted is None:
        return [closed[-1]]
    return [r for r in closed if int(r[0]) > last_emitted]


@dataclass(slots=True)
class _PollSub:
    pair: str
    timeframe: str
    builder: CandleBuilder
    on_reconnect: Optional[ReconnectFn]
    notify: Optional[NotifyFn]
    log_event: Optional[LogEventFn] = None


class PollingFeed:
    """REST-polling feed; one instance per exchange serves N subscriptions."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._subs: list[_PollSub] = []
        self._streaming = False
        self._stop_event: Optional[asyncio.Event] = None
        # Polling fetches COMPLETE closed candles via REST, so there is no
        # stale-partial-data hazard and thus no "reconnect cooldown" to apply
        # (risk Rule 6 treats None as 'never reconnected' → orders allowed).
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
        self._subs.append(_PollSub(pair, timeframe, builder, on_reconnect, notify, log_event))

    @property
    def subscriptions(self) -> list[_PollSub]:
        """Read-only view of current subscriptions (for tests/diagnostics)."""
        return list(self._subs)

    async def run(self) -> None:
        """Drive polling. Idempotent across multiple callers (like MarketFeed)."""
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
        """Signal the feed to stop after the current poll."""
        if self._stop_event is not None:
            self._stop_event.set()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _poll_all(self) -> None:
        import ccxt.async_support as accxt  # deferred import — not available in all envs

        exchange_cls = getattr(accxt, self.cfg.exchange)
        exchange = exchange_cls({"apiKey": self.cfg.api_key, "secret": self.cfg.api_secret, "enableRateLimit": True})
        if self.cfg.testnet:
            exchange.set_sandbox_mode(True)

        spawned: set[tuple[str, str]] = set()
        tasks: list[asyncio.Task] = []
        try:
            while not self._stop_event.is_set():
                # ONE poller per unique (pair, timeframe) — it feeds EVERY bot
                # subscribed to that stream. Many bots share one pair (e.g. two
                # strategies on ETH/USDT 4h); spawning per-sub would poll the pair
                # once and feed only the first-subscribed bot, silently starving
                # the rest (they would never see a candle close → never trade).
                for sub in list(self._subs):
                    key = (sub.pair, sub.timeframe)
                    if key not in spawned:
                        spawned.add(key)
                        tasks.append(
                            asyncio.create_task(
                                self._poll_key(exchange, key[0], key[1]),
                                name=f"poll:{key[0]}/{key[1]}",
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
            await exchange.close()

    def _subs_for(self, pair: str, timeframe: str) -> list[_PollSub]:
        """All subscriptions on a given (pair, timeframe) stream."""
        return [s for s in self._subs if s.pair == pair and s.timeframe == timeframe]

    def _dispatch_closed(self, pair: str, timeframe: str, row: list) -> None:
        """Feed one CLOSED OHLCV row to EVERY bot subscribed to (pair, timeframe).

        Volume is converted base→quote (× close) to match the ccxt.pro WS scale.
        Each builder gets its own list copy so no bot can mutate another's input.
        """
        ts = int(row[0])
        ohlcv = [ts, row[1], row[2], row[3], row[4], row[5] * row[4]]
        for sub in self._subs_for(pair, timeframe):
            sub.builder.process_ohlcv(list(ohlcv), is_closed=True)

    async def _poll_key(self, exchange, pair: str, timeframe: str) -> None:
        period_ms = _tf_ms(timeframe)
        # Seed from the newest bootstrapped candle across the subscribed bots so
        # we emit only genuinely new closes and never replay history (the bots
        # share one backfill, so their buffers start aligned).
        last_emitted: Optional[int] = None
        for sub in self._subs_for(pair, timeframe):
            buf = sub.builder.buffer
            if buf:
                last_emitted = buf[-1].ts if last_emitted is None else max(last_emitted, buf[-1].ts)
        while not self._stop_event.is_set():
            try:
                rows = await asyncio.wait_for(
                    exchange.fetch_ohlcv(pair, timeframe, limit=_POLL_LIMIT),
                    timeout=30,
                )
                now_ms = int(time.time() * 1000)
                for r in new_closed_rows(rows, period_ms, now_ms, last_emitted):
                    self._dispatch_closed(pair, timeframe, r)
                    last_emitted = int(r[0])
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — poll loop: log and keep polling
                msg = f"Polling feed {pair}/{timeframe} fetch failed. err={exc}"
                subs = self._subs_for(pair, timeframe)
                notify = subs[0].notify if subs else None
                log_event = subs[0].log_event if subs else None
                if notify:
                    notify("WARN", msg)
                if log_event:
                    log_event(
                        "WARN",
                        msg,
                        {
                            "pair": pair,
                            "timeframe": timeframe,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        },
                    )
            # Interruptible sleep until the next poll.
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=_POLL_INTERVAL_S)
                break
            except asyncio.TimeoutError:
                continue
