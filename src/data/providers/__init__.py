"""
Layer 2 — market-data provider registry.

Selects the correct streaming feed at startup based on cfg.exchange,
following the registration model in CLAUDE.md §9.

Public API:
    FeedProtocol                 — minimum interface every feed must satisfy
    register_feed(name)          — decorator to register a feed factory
    get_data_feed(cfg, ...)      — factory returning a feed instance

Adding a new feed provider (e.g. OANDA, Alpaca, IBKR):
    1. Create src/data/providers/<name>.py
    2. Implement a class with `async def run()` and `last_reconnect_ts: int | None`
    3. Decorate the factory function with @register_feed("name")
    4. Set EXCHANGE=<name> in .env

Crypto exchanges supported by ccxt.pro are pre-registered and route through
the existing MarketFeed without further work.
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
FeedFactory = Callable[..., FeedProtocol]

_REGISTRY: dict[str, FeedFactory] = {}


def register_feed(name: str) -> Callable[[FeedFactory], FeedFactory]:
    """Decorator: register a feed factory under the given exchange name."""

    def wrap(factory: FeedFactory) -> FeedFactory:
        _REGISTRY[name.lower()] = factory
        return factory

    return wrap


def registered_feeds() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_data_feed(
    cfg: AppConfig,
    pair: str,
    timeframe: str,
    builder: CandleBuilder,
    on_reconnect: Optional[ReconnectFn] = None,
    notify: Optional[NotifyFn] = None,
) -> FeedProtocol:
    """Resolve the streaming feed for cfg.exchange."""
    name = cfg.exchange.lower()
    if name not in _REGISTRY:
        raise ValueError(
            f"Unknown feed provider: '{cfg.exchange}'. "
            f"Registered feeds: {registered_feeds()}. "
            f"Add a new file under src/data/providers/ that calls "
            f"@register_feed('{cfg.exchange}') to add support."
        )
    return _REGISTRY[name](
        cfg=cfg,
        pair=pair,
        timeframe=timeframe,
        builder=builder,
        on_reconnect=on_reconnect,
        notify=notify,
    )


# ---------------------------------------------------------------------------
# Pre-registration: ccxt.pro-supported crypto exchanges
# ---------------------------------------------------------------------------


def _ccxt_feed_factory(**kwargs: Any) -> FeedProtocol:
    from src.data.feed import MarketFeed

    return MarketFeed(**kwargs)


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


# ---------------------------------------------------------------------------
# Auto-import submodules so @register_feed decorators run at package load
# ---------------------------------------------------------------------------

_pkg_path = Path(__file__).parent
for _mod in pkgutil.iter_modules([str(_pkg_path)]):
    if _mod.name.startswith("_"):
        continue
    importlib.import_module(f"{__name__}.{_mod.name}")
