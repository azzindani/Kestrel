"""
Layer 2 — market-data provider registry.

Selects the correct streaming feed at startup based on cfg.exchange,
following the registration model in CLAUDE.md §9.

Public API:
    FeedProtocol                 — minimum interface every feed must satisfy
    register_feed(name)          — decorator to register a feed factory
    get_data_feed(cfg, ...)      — factory returning a feed instance

Shared vs per-bot feeds:
    Pre-registered ccxt.pro exchanges share a single MarketFeed instance per
    exchange — one ccxt client and one set of WS state serves every bot
    trading on that exchange.  Subscriptions accumulate via feed.subscribe().

    OANDA/Alpaca/IBKR providers remain per-bot (one feed instance per call):
    their venue protocols don't benefit from connection sharing the same way.

Adding a new feed provider (e.g. OANDA, Alpaca, IBKR):
    1. Create src/data/providers/<name>.py
    2. Implement a class with `async def run()` and `last_reconnect_ts: int | None`
    3. Decorate the factory function with @register_feed("name")
    4. Set EXCHANGE=<name> in .env

Crypto exchanges supported by ccxt.pro are pre-registered and route through
the shared MarketFeed without further work.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from src.config import AppConfig
from src.data.candle_builder import CandleBuilder

# ---------------------------------------------------------------------------
# Feed contract
# ---------------------------------------------------------------------------


class FeedProtocol(Protocol):
    """Minimum interface every market-data feed must implement."""

    last_reconnect_ts: Optional[int]

    async def run(self) -> None: ...

    def stop(self) -> None: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

NotifyFn = Callable[[str, str], None]
ReconnectFn = Callable[[int], None]
LogEventFn = Callable[[str, str, dict], None]
FeedFactory = Callable[..., FeedProtocol]

_REGISTRY: dict[str, FeedFactory] = {}
_SHARED_EXCHANGES: set[str] = set()
_SHARED_INSTANCES: dict[str, FeedProtocol] = {}


def register_feed(name: str) -> Callable[[FeedFactory], FeedFactory]:
    """Decorator: register a per-bot feed factory under the given exchange name."""

    def wrap(factory: FeedFactory) -> FeedFactory:
        _REGISTRY[name.lower()] = factory
        return factory

    return wrap


def registered_feeds() -> list[str]:
    return sorted(_REGISTRY.keys())


def reset_shared_feeds() -> None:
    """Drop any cached shared feed instances (tests / restart helper)."""
    _SHARED_INSTANCES.clear()


def get_data_feed(
    cfg: AppConfig,
    pair: str,
    timeframe: str,
    builder: CandleBuilder,
    on_reconnect: Optional[ReconnectFn] = None,
    notify: Optional[NotifyFn] = None,
    log_event: Optional[LogEventFn] = None,
) -> FeedProtocol:
    """Resolve the streaming feed for cfg.exchange.

    For shared (ccxt) exchanges, the same MarketFeed instance is returned for
    every call with the same exchange name; ``feed.subscribe(...)`` is invoked
    on the caller's behalf to register this (pair, timeframe).  The first
    ``feed.run()`` caller drives streaming; the rest no-op until stop.
    """
    name = cfg.exchange.lower()
    if name not in _REGISTRY:
        raise ValueError(
            f"Unknown feed provider: '{cfg.exchange}'. "
            f"Registered feeds: {registered_feeds()}. "
            f"Add a new file under src/data/providers/ that calls "
            f"@register_feed('{cfg.exchange}') to add support."
        )

    if name in _SHARED_EXCHANGES:
        feed = _SHARED_INSTANCES.get(name)
        if feed is None:
            feed = _REGISTRY[name](cfg=cfg)
            _SHARED_INSTANCES[name] = feed
        feed.subscribe(pair, timeframe, builder, on_reconnect, notify, log_event)
        return feed

    return _REGISTRY[name](
        cfg=cfg,
        pair=pair,
        timeframe=timeframe,
        builder=builder,
        on_reconnect=on_reconnect,
        notify=notify,
    )


# ---------------------------------------------------------------------------
# Pre-registration: ccxt.pro-supported crypto exchanges (shared instances)
# ---------------------------------------------------------------------------


def _ccxt_feed_factory(cfg: AppConfig, **_: Any) -> FeedProtocol:
    from src.data.feed import MarketFeed

    return MarketFeed(cfg)


for _ccxt_name in (
    "bybit",
    "binance",
    "okx",
    "kucoin",
    "kraken",
    "gate",
    "mexc",
    "bitget",
    "bingx",
    "huobi",
):
    _REGISTRY[_ccxt_name] = _ccxt_feed_factory
    _SHARED_EXCHANGES.add(_ccxt_name)

# Exness (MetaApi) is also a shared feed — see src/data/providers/exness.py. Its
# factory is registered by the submodule's @register_feed("exness") decorator
# (auto-imported below); marking it shared makes get_data_feed fan one poller out
# to every subscribed bot instead of opening one MetaApi connection per bot, which
# is what lets a large hyperparameter lab run on the single Exness account.
_SHARED_EXCHANGES.add("exness")


# ---------------------------------------------------------------------------
# Auto-import submodules so @register_feed decorators run at package load
# ---------------------------------------------------------------------------

_pkg_path = Path(__file__).parent
for _mod in pkgutil.iter_modules([str(_pkg_path)]):
    if _mod.name.startswith("_"):
        continue
    importlib.import_module(f"{__name__}.{_mod.name}")
