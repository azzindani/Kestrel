"""
Layer 3 boundary — async DB write operations.

Public API (module §8):
    write_candle(candle) -> int          (returns DB id)
    write_signal(signal, outcome, reject_reason, trade_id) -> int
    write_trade(trade_dict) -> int
    write_event(bot_id, session_id, env, level, category, message, payload, trade_id) -> None
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from src.config import Candle, Signal, SignalOutcome
from src.db.connection import acquire


async def write_candle(candle: Candle) -> int:
    """Upsert a completed candle (with indicators) and return its DB id."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO candles (
                bot_id, ts, pair, timeframe,
                open, high, low, close, volume,
                ema9, ema21, rsi14, atr14,
                bb_upper, bb_lower, bb_width,
                adx, volume_ma20, volume_ratio, regime,
                body_size, total_range, body_ratio,
                upper_wick, lower_wick, direction, env
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7, $8, $9,
                $10, $11, $12, $13,
                $14, $15, $16,
                $17, $18, $19, $20,
                $21, $22, $23,
                $24, $25, $26, $27
            )
            ON CONFLICT (bot_id, pair, timeframe, ts) DO UPDATE SET
                open         = EXCLUDED.open,
                high         = EXCLUDED.high,
                low          = EXCLUDED.low,
                close        = EXCLUDED.close,
                volume       = EXCLUDED.volume,
                ema9         = EXCLUDED.ema9,
                ema21        = EXCLUDED.ema21,
                rsi14        = EXCLUDED.rsi14,
                atr14        = EXCLUDED.atr14,
                bb_upper     = EXCLUDED.bb_upper,
                bb_lower     = EXCLUDED.bb_lower,
                bb_width     = EXCLUDED.bb_width,
                adx          = EXCLUDED.adx,
                volume_ma20  = EXCLUDED.volume_ma20,
                volume_ratio = EXCLUDED.volume_ratio,
                regime       = EXCLUDED.regime,
                body_size    = EXCLUDED.body_size,
                total_range  = EXCLUDED.total_range,
                body_ratio   = EXCLUDED.body_ratio,
                upper_wick   = EXCLUDED.upper_wick,
                lower_wick   = EXCLUDED.lower_wick,
                direction    = EXCLUDED.direction
            RETURNING id
            """,
            candle.bot_id, candle.ts, candle.pair, candle.timeframe,
            candle.open, candle.high, candle.low, candle.close, candle.volume,
            candle.ema9, candle.ema21, candle.rsi14, candle.atr14,
            candle.bb_upper, candle.bb_lower, candle.bb_width,
            candle.adx, candle.volume_ma20, candle.volume_ratio, candle.regime,
            candle.body_size, candle.total_range, candle.body_ratio,
            candle.upper_wick, candle.lower_wick, candle.direction,
            "dev" if candle.bot_id.startswith("dev") else "prod",
        )
        return row["id"]


async def write_signal(
    signal: Signal,
    outcome: SignalOutcome,
    reject_reason: Optional[str] = None,
    trade_id: Optional[int] = None,
) -> int:
    """Insert a signal record and return its DB id."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO signals (
                bot_id, session_id, env, ts, pair, timeframe, candle_ts,
                pattern, direction, confidence, regime,
                layer_regime, layer_trend, layer_momentum, layer_volume, layers_passed,
                outcome, reject_reason, trade_id
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11,
                $12, $13, $14, $15, $16,
                $17, $18, $19
            ) RETURNING id
            """,
            signal.bot_id, signal.session_id, signal.env, signal.ts,
            signal.pair, signal.timeframe, signal.candle_ts,
            signal.pattern, signal.direction.value, signal.confidence, signal.regime,
            signal.layer_regime, signal.layer_trend, signal.layer_momentum,
            signal.layer_volume, signal.layers_passed,
            outcome.value, reject_reason, trade_id,
        )
        return row["id"]


async def write_trade(trade: dict[str, Any]) -> int:
    """Insert a new trade record and return its DB id."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO trades (
                bot_id, session_id, env, pair, timeframe, direction, pattern,
                entry_ts, entry_price, tp_price, sl_price, liquidation_price,
                bucket_id, size_usdt, leverage, notional_usdt,
                fee_entry_usdt, bucket_balance_before
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12,
                $13, $14, $15, $16,
                $17, $18
            ) RETURNING id
            """,
            trade["bot_id"], trade["session_id"], trade["env"],
            trade["pair"], trade["timeframe"], trade["direction"], trade["pattern"],
            trade["entry_ts"], trade["entry_price"], trade["tp_price"],
            trade["sl_price"], trade["liquidation_price"],
            trade["bucket_id"], trade["size_usdt"], trade["leverage"],
            trade["notional_usdt"], trade["fee_entry_usdt"],
            trade["bucket_balance_before"],
        )
        return row["id"]


async def close_trade(trade_id: int, close: dict[str, Any]) -> None:
    """Update a trade record on position close."""
    async with acquire() as conn:
        await conn.execute(
            """
            UPDATE trades SET
                exit_ts              = $2,
                exit_price           = $3,
                hold_candles         = $4,
                close_reason         = $5,
                pnl_gross_usdt       = $6,
                fee_exit_usdt        = $7,
                pnl_net_usdt         = $8,
                pnl_pct              = $9,
                bucket_balance_after = $10
            WHERE id = $1
            """,
            trade_id,
            close["exit_ts"], close["exit_price"], close["hold_candles"],
            close["close_reason"], close["pnl_gross_usdt"], close["fee_exit_usdt"],
            close["pnl_net_usdt"], close["pnl_pct"], close["bucket_balance_after"],
        )


async def write_event(
    bot_id: str,
    session_id: str,
    env: str,
    level: str,
    category: str,
    message: str,
    payload: Optional[dict[str, Any]] = None,
    trade_id: Optional[int] = None,
) -> None:
    """Insert a structured log event. This is the ONLY logging channel."""
    ts = int(time.time() * 1000)
    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO events (bot_id, session_id, env, ts, level, category, message, payload, trade_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            bot_id, session_id, env, ts, level, category, message,
            json.dumps(payload) if payload else None,
            trade_id,
        )


async def write_heartbeat(bot_id: str, ts: int, pid: int, status: str, note: Optional[str] = None) -> None:
    """Upsert the heartbeat record for this bot."""
    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO heartbeats (bot_id, ts, pid, status, note)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (bot_id) DO UPDATE SET
                ts = EXCLUDED.ts, pid = EXCLUDED.pid,
                status = EXCLUDED.status, note = EXCLUDED.note
            """,
            bot_id, ts, pid, status, note,
        )


async def link_trade_context(
    trade_id: int,
    candle_id: int,
    candle_ts: int,
    offset_candles: int,
    offset_hours: float,
    window: str,
) -> None:
    """Insert a trade_context row. Ignores duplicates (idempotent)."""
    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO trade_context (trade_id, candle_id, candle_ts, offset_candles, offset_hours, window)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (trade_id, candle_id) DO NOTHING
            """,
            trade_id, candle_id, candle_ts, offset_candles, offset_hours, window,
        )


async def mark_context_post_complete(trade_id: int) -> None:
    """Mark the post-context window as complete after 48h background job."""
    async with acquire() as conn:
        await conn.execute(
            "UPDATE trades SET context_post_complete = TRUE WHERE id = $1",
            trade_id,
        )


async def load_recent_candles(
    bot_id: str, pair: str, timeframe: str, limit: int
) -> list[dict]:
    """Load the N most recent candles for a pair/timeframe (used to bootstrap indicators)."""
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM candles
            WHERE bot_id = $1 AND pair = $2 AND timeframe = $3
            ORDER BY ts DESC LIMIT $4
            """,
            bot_id, pair, timeframe, limit,
        )
        return [dict(r) for r in reversed(rows)]


async def load_pattern_memory(
    pattern: str, direction: str, session: str, regime: str
) -> Optional[dict]:
    """Load a pattern_memory row or return None if not found."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM pattern_memory
            WHERE pattern = $1 AND direction = $2 AND session = $3 AND regime = $4
            """,
            pattern, direction, session, regime,
        )
        return dict(row) if row else None


async def upsert_pattern_memory(
    pattern: str, direction: str, session: str, regime: str,
    sample_count: int, win_count: int, win_rate: float,
    avg_pnl_pct: float, last_updated: int,
) -> None:
    """Upsert pattern performance statistics."""
    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pattern_memory
                (pattern, direction, session, regime, sample_count, win_count, win_rate, avg_pnl_pct, last_updated)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (pattern, direction, session, regime) DO UPDATE SET
                sample_count = EXCLUDED.sample_count,
                win_count    = EXCLUDED.win_count,
                win_rate     = EXCLUDED.win_rate,
                avg_pnl_pct  = EXCLUDED.avg_pnl_pct,
                last_updated = EXCLUDED.last_updated
            """,
            pattern, direction, session, regime,
            sample_count, win_count, win_rate, avg_pnl_pct, last_updated,
        )


async def get_session_pnl(bot_id: str, env: str, since_ts: int) -> float:
    """Return net PnL for closed trades since the given timestamp (session reset)."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(pnl_net_usdt), 0.0) AS total
            FROM trades
            WHERE bot_id = $1 AND env = $2 AND exit_ts >= $3 AND pnl_net_usdt IS NOT NULL
            """,
            bot_id, env, since_ts,
        )
        return float(row["total"])


async def count_active_positions(bot_id: str, env: str) -> int:
    """Count trades with no exit_ts (currently open positions)."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM trades WHERE bot_id = $1 AND env = $2 AND exit_ts IS NULL",
            bot_id, env,
        )
        return int(row["cnt"])
