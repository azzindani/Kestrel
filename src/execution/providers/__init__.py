"""
Layer 3 boundary — execution provider registry.

Selects the correct execution backend at startup based on cfg.exchange and cfg.env,
following the registration model in CLAUDE.md §9.

Public API:
    register_execution(name)      — decorator to register a provider factory
    get_execution_provider(cfg)   — factory returning an ExecutionInterface

Adding a new provider (e.g. OANDA forex, Alpaca stocks, IBKR multi-market):
    1. Create src/execution/providers/<name>.py
    2. Implement a class that subclasses ExecutionInterface
    3. Decorate it (or a factory function) with @register_execution("name")
    4. Set EXCHANGE=<name> in .env

Crypto exchanges supported by ccxt are pre-registered and route through the
existing LiveExecution module without further work.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Callable

from src.config import AppConfig, Env
from src.execution.interface import ExecutionInterface

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ProviderFactory = Callable[[AppConfig], ExecutionInterface]
_REGISTRY: dict[str, ProviderFactory] = {}


def register_execution(name: str) -> Callable[[ProviderFactory], ProviderFactory]:
    """Decorator: register a factory under the given exchange name (lowercase)."""

    def wrap(factory: ProviderFactory) -> ProviderFactory:
        _REGISTRY[name.lower()] = factory
        return factory

    return wrap


def registered_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_execution_provider(cfg: AppConfig) -> ExecutionInterface:
    """Resolve the execution backend for the current environment.

    DI rules (CLAUDE.md §14):
        ENV=dev      → SimulationExecution (paper trading, no exchange)
        ENV=staging  → real provider, but MUST be a demo/testnet venue (virtual money)
        ENV=prod     → real provider selected by cfg.exchange (real capital, §18 gated)
    """
    # Phase-1 (dev) always simulates. Phase-2 (staging) ALSO simulates while
    # STAGING_ENGINE=sim (the interim before demo-venue keys exist): the curated
    # best-performers pool runs on modeled fills, fully isolated in env='staging'
    # (§19), so the phase can run NOW. Flip STAGING_ENGINE=live (+ VST keys +
    # TESTNET=true) to upgrade staging to demo-money execution later.
    if cfg.env is Env.DEV or (cfg.env is Env.STAGING and cfg.staging_engine == "sim"):
        from src.config import load_params
        from src.execution.simulation import SimulationExecution

        # SimulationExecution owns the exit mechanics — max_hold_candles timeout
        # AND trailing-close. Those live in per-bot params, so prefer the bot's
        # own override (cfg.params) and fall back to params.json for single-bot
        # mode. Using base params here would silently ignore per-variant holds
        # and disable trailing on the trailing lab variants. Boundary-layer I/O
        # (load_params) is allowed here (CLAUDE.md §7).
        params = cfg.params if cfg.params is not None else load_params("params.json")
        return SimulationExecution(cfg, params)

    # Phase 2 quarantine safety rail: staging on the LIVE code path (it is not DEV
    # and STAGING_ENGINE != sim) exercises real order placement/fills, but it must
    # run against a demo/testnet venue. Refuse to start staging on a live
    # (TESTNET=false) venue — that would place real orders while masquerading as a
    # paper phase.
    if cfg.env is Env.STAGING and not cfg.testnet:
        raise ValueError(
            "ENV=staging requires TESTNET=true — staging must run on a demo/testnet "
            "venue (e.g. BingX VST) with virtual money, never real capital. Set "
            "TESTNET=true in the staging .env, or use ENV=prod for real trading."
        )

    name = cfg.exchange.lower()
    if name not in _REGISTRY:
        raise ValueError(
            f"Unknown execution provider: '{cfg.exchange}'. "
            f"Registered providers: {registered_providers()}. "
            f"Add a new file under src/execution/providers/ that calls "
            f"@register_execution('{cfg.exchange}') to add support."
        )
    return _REGISTRY[name](cfg)


# ---------------------------------------------------------------------------
# Pre-registration: ccxt-supported crypto exchanges
# ---------------------------------------------------------------------------
# LiveExecution uses getattr(ccxt, cfg.exchange) — anything ccxt supports
# routes through the same code path. New non-ccxt providers (oanda, alpaca,
# ibkr, …) live in their own module under this package.


def _ccxt_factory(cfg: AppConfig) -> ExecutionInterface:
    from src.execution.live import LiveExecution

    return LiveExecution(cfg)


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
    _REGISTRY[_ccxt_name] = _ccxt_factory


# ---------------------------------------------------------------------------
# Auto-import submodules so @register_execution decorators run at package load
# ---------------------------------------------------------------------------

_pkg_path = Path(__file__).parent
for _mod in pkgutil.iter_modules([str(_pkg_path)]):
    if _mod.name.startswith("_"):
        continue
    importlib.import_module(f"{__name__}.{_mod.name}")
