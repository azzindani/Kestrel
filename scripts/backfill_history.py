#!/usr/bin/env python3
"""
Backfill recent REAL historical candles so the daemon is warmed up immediately
instead of rebuilding history live over ~5 hours.

IMPORTANT volume-unit detail (verified 2026-06-02): the live feed is gate's
ccxt.pro WebSocket, which reports volume in QUOTE currency (USDT, ~millions).
All ccxt REST OHLCV reports BASE currency (BTC, ~tens). Mixing the two corrupts
volume_ma20 / volume_ratio. The exact relation is quote = base × close (ratio
1.000), so we fetch gate REST (same exchange as the live feed) and multiply
each candle's volume by its close to match the live WebSocket scale.

For each bot in bots.json: fetch ~1 day, convert volume base→quote, run through
the production CandleBuilder, write to DB. Then restart the daemon so it
bootstraps from the populated, scale-consistent history.

The candle source defaults to gate (the labs ccxt.pro WS feed). Pass --source to
match a different live feed's venue/volume scale: the Phase-2 staging stack feeds
from BingX REST polling (FEED_MODE=poll, EXCHANGE=bingx), so warm it from BingX.
--bots selects the fleet file (default bots.json); candles are written per the
file's bot_ids + the container's ENV (the bootstrap is bot_id-scoped, so each bot
needs its own history). No exchange API keys needed — fetch_ohlcv is public.

Run — labs (default):
  docker run --rm --network kestrel_net --env-file .env -e DB_HOST=postgres \
    -v /root/Kestrel:/app -w /app --entrypoint python \
    kestrel-kestrel:latest scripts/backfill_history.py

Run — Phase-2 staging warmup (env='staging' candles from BingX, no keys needed):
  docker run --rm --network kestrel_net --env-file .env \
    -e DB_HOST=postgres -e ENV=staging \
    -v /root/Kestrel:/app -w /app --entrypoint python kestrel-kestrel:latest \
    scripts/backfill_history.py --bots bots.staging.json --source bingx
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, "/app")

import ccxt
from dotenv import load_dotenv

from src.config import AppConfig, load_bot_configs, load_params
from src.data.candle_builder import CandleBuilder
from src.db import connection as db_conn
from src.db import writer as db

_DEFAULT_SOURCE = "gate"  # default candle venue — must match the live feed for volume consistency
# Per-timeframe candle period (ms) and how many days to backfill so the daemon has
# enough history to bootstrap indicators (EMA/ATR(50)/ADX) + regime immediately.
# Higher TFs need a long lookback to reach 51+ candles for ATR(50).
_TF_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
_TF_DAYS = {"5m": 2, "15m": 7, "1h": 30, "4h": 120, "1d": 500}


def fetch_quote_ohlcv(pair: str, timeframe: str, days: int, source: str = _DEFAULT_SOURCE) -> list[list]:
    """Fetch REST OHLCV from `source` and convert volume base→quote (× close) to
    match the live feed's quote-volume scale (gate WS and the BingX poll feed both
    carry quote volume)."""
    tf_ms = _TF_MS.get(timeframe, 300_000)
    ex = getattr(ccxt, source)({"enableRateLimit": True})
    ex.load_markets()
    now_ms = int(time.time() * 1000)
    since = now_ms - days * 86_400_000
    rows: list[list] = []
    cur = since
    while cur < now_ms:
        batch = ex.fetch_ohlcv(pair, timeframe, since=cur, limit=1000)
        batch = [r for r in batch if r[0] >= cur]
        if not batch:
            break
        rows.extend(batch)
        cur = batch[-1][0] + tf_ms
        if len(batch) < 2:
            break
    # de-dup + convert volume to quote (USDT) to match the live WS feed
    seen = {
        int(r[0]): [int(r[0]), r[1], r[2], r[3], r[4], r[5] * r[4]]
        for r in rows
    }
    return [seen[k] for k in sorted(seen)]


async def run(bots_path: str = "bots.json", source: str = _DEFAULT_SOURCE) -> None:
    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    base = load_params("params.json")
    bots = load_bot_configs(bots_path, cfg, base)

    # Group bots by (pair, timeframe) so each pair is fetched ONCE (not per bot) —
    # essential at lab scale where hundreds of bots share ~10 pairs.
    groups: dict = {}
    for bot in bots:
        groups.setdefault((bot.pair, bot.timeframe_entry), []).append(bot)

    await db_conn.init_pool(cfg)
    try:
        for (pair, tf), grp in groups.items():
            rows = fetch_quote_ohlcv(pair, tf, days=_TF_DAYS.get(tf, 2), source=source)
            avg_vol = sum(r[5] for r in rows) / len(rows) if rows else 0
            wrote = 0
            for bot in grp:
                builder = CandleBuilder(bot.bot_id, pair, tf, base)
                built: list = []
                builder.set_emitter(built.append)
                for r in rows:
                    builder.process_ohlcv(r, is_closed=True)
                for c in built:
                    try:
                        await db.write_candle(c)
                        wrote += 1
                    except Exception:
                        pass
            print(f"{pair} {tf}: source={source}(quote) fetched={len(rows)} "
                  f"bots={len(grp)} candles_written={wrote} avg_vol={avg_vol:,.0f}", flush=True)
    finally:
        await db_conn.close_pool()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Backfill warmup candle history for a bot fleet.")
    p.add_argument("--bots", default="bots.json", help="fleet file (default bots.json; e.g. bots.staging.json)")
    p.add_argument("--source", default=_DEFAULT_SOURCE, help=f"candle venue, match the live feed (default {_DEFAULT_SOURCE}; bingx for staging poll feed)")
    args = p.parse_args()
    asyncio.run(run(args.bots, args.source))
