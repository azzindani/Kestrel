#!/usr/bin/env python3
"""
Microstructure recorder — bank live order-book + trade-tape data (owner-authorized
2026-06-21). Kestrel has only ever seen OHLCV candles; fast-timeframe scalp edge lives
in microstructure (bid/ask depth imbalance, aggressor-side trade delta, spread). That
data is NOT available historically (free feeds give OHLCV history only, not L2 depth),
so it must be RECORDED LIVE going forward — then, after weeks accumulate, a signal can
be backtested against it (walk-forward, the way every other Kestrel idea is validated).

This is a standalone Layer-3 boundary recorder (I/O: exchange REST + Postgres). It does
NOT touch the live trading daemon and does NOT trade. It owns its OWN table
(`microstructure`, created here via CREATE TABLE IF NOT EXISTS) — deliberately kept OUT
of the frozen db/schema.py canonical trading schema until/unless a microstructure signal
proves out and a human promotes it via a real migration (CLAUDE.md §4).

Per snapshot (every --interval seconds), per pair, it stores:
  mid, spread_bps, best bid/ask, resting depth (top-5 + top-20 each side) and the depth
  imbalance, plus the aggressor-side trade delta over the trades that printed since the
  previous snapshot (non-overlapping).

Run (inside the container so ccxt + DB_HOST=postgres resolve):
    docker compose exec -T kestrel python3 scripts/record_microstructure.py --once   # one cycle
    docker compose exec -d kestrel python3 scripts/record_microstructure.py           # detached loop
Or as the override.yml `microstructure-recorder` service (durable across restarts).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

DEFAULT_PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT", "ADA/USDT"]

DDL = """
CREATE TABLE IF NOT EXISTS microstructure (
    id BIGSERIAL PRIMARY KEY,
    ts BIGINT NOT NULL,
    pair TEXT NOT NULL,
    mid NUMERIC, spread_bps NUMERIC,
    best_bid NUMERIC, best_ask NUMERIC,
    bid_vol5 NUMERIC, ask_vol5 NUMERIC,
    bid_vol20 NUMERIC, ask_vol20 NUMERIC,
    depth_imb5 NUMERIC, depth_imb20 NUMERIC,
    trade_buy_vol NUMERIC, trade_sell_vol NUMERIC, trade_delta NUMERIC, trade_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_micro_pair_ts ON microstructure (pair, ts DESC);
"""

_INSERT = """
INSERT INTO microstructure (
    ts, pair, mid, spread_bps, best_bid, best_ask, bid_vol5, ask_vol5, bid_vol20,
    ask_vol20, depth_imb5, depth_imb20, trade_buy_vol, trade_sell_vol, trade_delta, trade_count
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
"""


def _imbalance(bid: float, ask: float) -> float:
    tot = bid + ask
    return (bid - ask) / tot if tot > 0 else 0.0


def _features(ob: dict, trades: list, now_ms: int, pair: str) -> dict | None:
    """Pure: turn a raw order book + new trades into one microstructure row."""
    bids, asks = ob.get("bids") or [], ob.get("asks") or []
    if not bids or not asks:
        return None
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2.0
    spread_bps = (best_ask - best_bid) / mid * 1e4 if mid > 0 else 0.0
    bid5 = sum(a for _, a in bids[:5])
    ask5 = sum(a for _, a in asks[:5])
    bid20 = sum(a for _, a in bids[:20])
    ask20 = sum(a for _, a in asks[:20])
    buy = sum(t["amount"] for t in trades if t.get("side") == "buy")
    sell = sum(t["amount"] for t in trades if t.get("side") == "sell")
    return {
        "ts": now_ms,
        "pair": pair,
        "mid": mid,
        "spread_bps": spread_bps,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_vol5": bid5,
        "ask_vol5": ask5,
        "bid_vol20": bid20,
        "ask_vol20": ask20,
        "depth_imb5": _imbalance(bid5, ask5),
        "depth_imb20": _imbalance(bid20, ask20),
        "trade_buy_vol": buy,
        "trade_sell_vol": sell,
        "trade_delta": _imbalance(buy, sell),
        "trade_count": len(trades),
    }


async def _one_cycle(ex, conn, pairs: list[str], last_ts: dict[str, int], now_ms: int) -> int:
    written = 0
    for pair in pairs:
        try:
            ob = await ex.fetch_order_book(pair, limit=20)
            since = last_ts.get(pair)
            trades = await ex.fetch_trades(pair, since=since, limit=200)
            # only count trades strictly newer than the last seen (non-overlapping windows)
            if since is not None:
                trades = [t for t in trades if t.get("timestamp") and t["timestamp"] > since]
            if trades:
                last_ts[pair] = max(t["timestamp"] for t in trades if t.get("timestamp"))
            row = _features(ob, trades, now_ms, pair)
            if row is None:
                continue
            await conn.execute(
                _INSERT,
                *[
                    row[k]
                    for k in (
                        "ts",
                        "pair",
                        "mid",
                        "spread_bps",
                        "best_bid",
                        "best_ask",
                        "bid_vol5",
                        "ask_vol5",
                        "bid_vol20",
                        "ask_vol20",
                        "depth_imb5",
                        "depth_imb20",
                        "trade_buy_vol",
                        "trade_sell_vol",
                        "trade_delta",
                        "trade_count",
                    )
                ],
            )
            written += 1
        except Exception as exc:  # noqa: BLE001 — one bad pair must not kill the recorder
            print(f"[micro] {pair} cycle error: {type(exc).__name__}: {exc}", file=sys.stderr)
    return written


async def _run(args: argparse.Namespace) -> int:
    import asyncpg
    import ccxt.async_support as ccxt
    from dotenv import load_dotenv

    load_dotenv()
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    ex = getattr(ccxt, args.exchange)({"enableRateLimit": True})
    conn = await asyncpg.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    last_ts: dict[str, int] = {}
    try:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                await conn.execute(stmt)
        print(f"[micro] recording {len(pairs)} pairs on {args.exchange} every {args.interval}s -> microstructure")
        cycles = 0
        while True:
            now_ms = int(time.time() * 1000)
            n = await _one_cycle(ex, conn, pairs, last_ts, now_ms)
            cycles += 1
            if args.once:
                print(f"[micro] one cycle: {n} rows written")
                break
            if cycles % 30 == 0:  # ~ every 30 cycles, a heartbeat to stdout
                total = await conn.fetchval("SELECT count(*) FROM microstructure")
                print(f"[micro] cycle {cycles}: {n} rows this cycle, {total} total")
            await asyncio.sleep(args.interval)
    finally:
        await ex.close()
        await conn.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Record live order-book + trade-tape microstructure.")
    p.add_argument("--pairs", default=",".join(DEFAULT_PAIRS), help="comma list of pairs")
    p.add_argument("--exchange", default="gate", help="ccxt exchange id (default gate)")
    p.add_argument("--interval", type=float, default=10.0, help="seconds between snapshots (default 10)")
    p.add_argument("--once", action="store_true", help="run a single cycle and exit (smoke test)")
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
