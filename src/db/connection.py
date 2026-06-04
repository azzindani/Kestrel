"""
Layer 3 boundary — PostgreSQL async connection pool.
All DB I/O is funnelled through this module.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import asyncpg

from src.config import AppConfig

_pool: Optional[asyncpg.Pool] = None


async def init_pool(cfg: AppConfig) -> asyncpg.Pool:
    """Create and store the global connection pool. Call once at startup.

    All bots in a multi-bot process share this one pool, so the fleet closes
    candles roughly in lockstep on each timeframe boundary (a thundering herd
    of concurrent DB ops). max_size is sized to absorb that burst — a small
    pool drains and acquire() blocks waiting for a free connection, which under
    a full fleet reads as a silent feed freeze. Stays well under Postgres'
    default max_connections (100), leaving room for backups/Grafana.
    """
    global _pool
    dsn = f"postgresql://{cfg.db_user}:{cfg.db_password}@{cfg.db_host}:{cfg.db_port}/{cfg.db_name}"
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=4,
        max_size=32,
        command_timeout=30,
    )
    return _pool


async def close_pool() -> None:
    """Gracefully close the pool. Call during daemon shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the active pool. Raises RuntimeError if not initialised."""
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() first")
    return _pool


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection from the pool as an async context manager."""
    async with get_pool().acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection and wrap in a transaction."""
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            yield conn
