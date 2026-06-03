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

Run (one-off container on kestrel_net, DB reachable):
  docker run --rm --network kestrel_net --env-file .env -e DB_HOST=postgres \
    -v /root/Kestrel:/app -w /app --entrypoint python \
    kestrel-kestrel:latest scripts/backfill_history.py
"""

from __future__ import annotations

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

_FEED_EXCHANGE = "gate"   # must match the live ccxt.pro feed for volume consistency
_TF_MS = 300_000          # 5m


def fetch_quote_ohlcv(pair: str, timeframe: str, days: int) -> list[list]:
    """Fetch gate REST OHLCV and convert volume base→quote (× close) to match
    the live gate WebSocket volume scale."""
    ex = getattr(ccxt, _FEED_EXCHANGE)({"enableRateLimit": True})
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
        cur = batch[-1][0] + _TF_MS
        if len(batch) < 2:
            break
    # de-dup + convert volume to quote (USDT) to match the live WS feed
    seen = {
        int(r[0]): [int(r[0]), r[1], r[2], r[3], r[4], r[5] * r[4]]
        for r in rows
    }
    return [seen[k] for k in sorted(seen)]


async def run() -> None:
    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    base = load_params("params.json")
    bots = load_bot_configs("bots.json", cfg, base)

    await db_conn.init_pool(cfg)
    try:
        for bot in bots:
            rows = fetch_quote_ohlcv(bot.pair, bot.timeframe_entry, days=1)
            builder = CandleBuilder(bot.bot_id, bot.pair, bot.timeframe_entry, base)
            built: list = []
            builder.set_emitter(built.append)
            for r in rows:
                builder.process_ohlcv(r, is_closed=True)

            wrote, skipped = 0, 0
            for c in built:
                try:
                    await db.write_candle(c)
                    wrote += 1
                except Exception:
                    skipped += 1
            avg_vol = sum(r[5] for r in rows) / len(rows) if rows else 0
            print(f"{bot.bot_id} {bot.pair}: source={_FEED_EXCHANGE}(quote) "
                  f"fetched={len(rows)} wrote={wrote} skipped={skipped} avg_vol={avg_vol:,.0f}",
                  flush=True)
    finally:
        await db_conn.close_pool()


if __name__ == "__main__":
    asyncio.run(run())
