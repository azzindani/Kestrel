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

from src.config import Candle, Signal, SignalOutcome, SizingState
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
            candle.bot_id,
            candle.ts,
            candle.pair,
            candle.timeframe,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
            candle.ema9,
            candle.ema21,
            candle.rsi14,
            candle.atr14,
            candle.bb_upper,
            candle.bb_lower,
            candle.bb_width,
            candle.adx,
            candle.volume_ma20,
            candle.volume_ratio,
            candle.regime,
            candle.body_size,
            candle.total_range,
            candle.body_ratio,
            candle.upper_wick,
            candle.lower_wick,
            candle.direction,
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
            signal.bot_id,
            signal.session_id,
            signal.env,
            signal.ts,
            signal.pair,
            signal.timeframe,
            signal.candle_ts,
            signal.pattern,
            signal.direction.value,
            signal.confidence,
            signal.regime,
            signal.layer_regime,
            signal.layer_trend,
            signal.layer_momentum,
            signal.layer_volume,
            signal.layers_passed,
            outcome.value,
            reject_reason,
            trade_id,
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
            trade["bot_id"],
            trade["session_id"],
            trade["env"],
            trade["pair"],
            trade["timeframe"],
            trade["direction"],
            trade["pattern"],
            trade["entry_ts"],
            trade["entry_price"],
            trade["tp_price"],
            trade["sl_price"],
            trade["liquidation_price"],
            trade["bucket_id"],
            trade["size_usdt"],
            trade["leverage"],
            trade["notional_usdt"],
            trade["fee_entry_usdt"],
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
            close["exit_ts"],
            close["exit_price"],
            close["hold_candles"],
            close["close_reason"],
            close["pnl_gross_usdt"],
            close["fee_exit_usdt"],
            close["pnl_net_usdt"],
            close["pnl_pct"],
            close["bucket_balance_after"],
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
            bot_id,
            session_id,
            env,
            ts,
            level,
            category,
            message,
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
            bot_id,
            ts,
            pid,
            status,
            note,
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
            INSERT INTO trade_context (trade_id, candle_id, candle_ts, offset_candles, offset_hours, "window")
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (trade_id, candle_id) DO NOTHING
            """,
            trade_id,
            candle_id,
            candle_ts,
            offset_candles,
            offset_hours,
            window,
        )


async def mark_context_post_complete(trade_id: int) -> None:
    """Mark the post-context window as complete after 48h background job."""
    async with acquire() as conn:
        await conn.execute(
            "UPDATE trades SET context_post_complete = TRUE WHERE id = $1",
            trade_id,
        )


# ---------------------------------------------------------------------------
# trade_context — auto-link helpers (CLAUDE.md §21)
# ---------------------------------------------------------------------------

_CONTEXT_WINDOW_HOURS = 48


async def link_pre_context(
    trade_id: int,
    bot_id: str,
    pair: str,
    timeframe: str,
    entry_ts: int,
    window_hours: int = _CONTEXT_WINDOW_HOURS,
) -> int:
    """Bulk-link every candle in (entry_ts - window_hours, entry_ts] as 'pre'.

    Called once at trade entry. offset_candles is negative (older candles =
    more negative); offset_hours is negative. Returns the number of rows
    inserted (ignores ON CONFLICT duplicates).
    """
    start_ts = entry_ts - window_hours * 3_600_000
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, ts FROM candles
            WHERE bot_id = $1 AND pair = $2 AND timeframe = $3
              AND ts >= $4 AND ts <= $5
            ORDER BY ts
            """,
            bot_id,
            pair,
            timeframe,
            start_ts,
            entry_ts,
        )
        if not rows:
            return 0
        # Newest row in the window has offset_candles closest to 0.
        total = len(rows)
        for i, row in enumerate(rows):
            offset_candles = -(total - 1 - i)
            offset_hours = (row["ts"] - entry_ts) / 3_600_000.0
            await conn.execute(
                """
                INSERT INTO trade_context (trade_id, candle_id, candle_ts, offset_candles, offset_hours, "window")
                VALUES ($1, $2, $3, $4, $5, 'pre')
                ON CONFLICT (trade_id, candle_id) DO NOTHING
                """,
                trade_id,
                row["id"],
                row["ts"],
                offset_candles,
                offset_hours,
            )
        return total


async def link_during_context(
    trade_id: int,
    candle_id: int,
    candle_ts: int,
    entry_ts: int,
) -> None:
    """Link a single in-flight candle as 'during' context. Idempotent."""
    offset_hours = (candle_ts - entry_ts) / 3_600_000.0
    # offset_candles for during starts at 1 and counts forward; estimating
    # via offset_hours / 5min keeps us correct for arbitrary timeframes.
    # If timeframes differ, the consumer can still recompute from candle_ts.
    offset_candles = max(1, round(offset_hours * 12))  # 12 candles per hour @ 5m
    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO trade_context (trade_id, candle_id, candle_ts, offset_candles, offset_hours, "window")
            VALUES ($1, $2, $3, $4, $5, 'during')
            ON CONFLICT (trade_id, candle_id) DO NOTHING
            """,
            trade_id,
            candle_id,
            candle_ts,
            offset_candles,
            offset_hours,
        )


async def link_post_context(
    trade_id: int,
    bot_id: str,
    pair: str,
    timeframe: str,
    exit_ts: int,
    window_hours: int = _CONTEXT_WINDOW_HOURS,
) -> int:
    """Bulk-link every candle in (exit_ts, exit_ts + window_hours] as 'post'.
    Marks the trade's context_post_complete=TRUE atomically with the inserts."""
    end_ts = exit_ts + window_hours * 3_600_000
    async with acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, ts FROM candles
                WHERE bot_id = $1 AND pair = $2 AND timeframe = $3
                  AND ts > $4 AND ts <= $5
                ORDER BY ts
                """,
                bot_id,
                pair,
                timeframe,
                exit_ts,
                end_ts,
            )
            for i, row in enumerate(rows):
                offset_hours = (row["ts"] - exit_ts) / 3_600_000.0
                offset_candles = i + 1
                await conn.execute(
                    """
                    INSERT INTO trade_context (trade_id, candle_id, candle_ts, offset_candles, offset_hours, "window")
                    VALUES ($1, $2, $3, $4, $5, 'post')
                    ON CONFLICT (trade_id, candle_id) DO NOTHING
                    """,
                    trade_id,
                    row["id"],
                    row["ts"],
                    offset_candles,
                    offset_hours,
                )
            await conn.execute(
                "UPDATE trades SET context_post_complete = TRUE WHERE id = $1",
                trade_id,
            )
            return len(rows)


async def trades_pending_post_context(
    window_hours: int = _CONTEXT_WINDOW_HOURS,
    env: Optional[str] = None,
) -> list[dict]:
    """Trades whose exit_ts is older than window_hours ago AND
    context_post_complete=FALSE — eligible for post-window linking."""
    cutoff_ms = int(time.time() * 1000) - window_hours * 3_600_000
    async with acquire() as conn:
        if env is None:
            rows = await conn.fetch(
                """
                SELECT id, bot_id, pair, timeframe, exit_ts
                FROM trades
                WHERE exit_ts IS NOT NULL
                  AND exit_ts <= $1
                  AND COALESCE(context_post_complete, FALSE) = FALSE
                ORDER BY exit_ts
                """,
                cutoff_ms,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, bot_id, pair, timeframe, exit_ts
                FROM trades
                WHERE exit_ts IS NOT NULL
                  AND exit_ts <= $1
                  AND COALESCE(context_post_complete, FALSE) = FALSE
                  AND env = $2
                ORDER BY exit_ts
                """,
                cutoff_ms,
                env,
            )
        return [dict(r) for r in rows]


async def load_recent_candles(bot_id: str, pair: str, timeframe: str, limit: int) -> list[dict]:
    """Load the N most recent candles for a pair/timeframe (used to bootstrap indicators)."""
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM candles
            WHERE bot_id = $1 AND pair = $2 AND timeframe = $3
            ORDER BY ts DESC LIMIT $4
            """,
            bot_id,
            pair,
            timeframe,
            limit,
        )
        return [dict(r) for r in reversed(rows)]


async def get_latest_order_flow(pair: str, max_age_ms: int, now_ms: int) -> Optional[float]:
    """Return the most recent top-5 depth imbalance (depth_imb5) for `pair`, or None.

    The microstructure table is written by the separate recorder process
    (scripts/record_microstructure.py); this is a read-only consumer used by the
    optional order-flow alignment gate (signal/detector.py). Returns None when the
    pair is not recorded or the newest snapshot is older than max_age_ms — so the
    gate fails closed on a stale/absent feed rather than entering on a stale book.
    """
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT depth_imb5, ts FROM microstructure
            WHERE pair = $1 AND depth_imb5 IS NOT NULL
            ORDER BY ts DESC LIMIT 1
            """,
            pair,
        )
    if row is None or row["depth_imb5"] is None:
        return None
    if now_ms - int(row["ts"]) > max_age_ms:
        return None
    return float(row["depth_imb5"])


async def load_pattern_memory(pattern: str, direction: str, session: str, regime: str) -> Optional[dict]:
    """Load a pattern_memory row or return None if not found."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM pattern_memory
            WHERE pattern = $1 AND direction = $2 AND session = $3 AND regime = $4
            """,
            pattern,
            direction,
            session,
            regime,
        )
        return dict(row) if row else None


async def upsert_pattern_memory(
    pattern: str,
    direction: str,
    session: str,
    regime: str,
    sample_count: int,
    win_count: int,
    win_rate: float,
    avg_pnl_pct: float,
    last_updated: int,
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
            pattern,
            direction,
            session,
            regime,
            sample_count,
            win_count,
            win_rate,
            avg_pnl_pct,
            last_updated,
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
            bot_id,
            env,
            since_ts,
        )
        return float(row["total"])


async def count_active_positions(bot_id: str, env: str) -> int:
    """Count trades with no exit_ts (currently open positions)."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM trades WHERE bot_id = $1 AND env = $2 AND exit_ts IS NULL",
            bot_id,
            env,
        )
        return int(row["cnt"])


async def get_open_trades(bot_id: str, env: str) -> list[dict[str, Any]]:
    """Return this bot's currently-open (exit_ts IS NULL) trades.

    Used by Daemon._reconcile() to detect DB-open positions the execution
    engine has no in-memory record of — SimulationExecution holds no state
    across a restart, so after any ungraceful stop its own reconcile() always
    comes back empty; these rows would otherwise jam the bucket forever.
    """
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, pair, entry_ts, entry_price, notional_usdt, bucket_balance_before
            FROM trades
            WHERE bot_id = $1 AND env = $2 AND exit_ts IS NULL
            """,
            bot_id,
            env,
        )
        # NUMERIC columns come back as Decimal — cast now so callers can do
        # plain float arithmetic without re-deriving this per call site.
        return [
            {
                "id": r["id"],
                "pair": r["pair"],
                "entry_ts": r["entry_ts"],
                "entry_price": float(r["entry_price"]),
                "notional_usdt": float(r["notional_usdt"]) if r["notional_usdt"] is not None else None,
                "bucket_balance_before": (
                    float(r["bucket_balance_before"]) if r["bucket_balance_before"] is not None else None
                ),
            }
            for r in rows
        ]


async def count_open_positions_fleet(env: str) -> int:
    """Count open positions across the WHOLE fleet in one env (exit_ts IS NULL).

    Backs the fleet-wide concurrent-position cap (owner directive 2026-07-15:
    "we can put all high win rate bots in prod, but we maintain how many we can
    allow in open position") — many signal sources, bounded capital use.
    """
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM trades WHERE env = $1 AND exit_ts IS NULL",
            env,
        )
        return int(row["cnt"])


async def get_sizing_state(bot_id: str, env: str, starting_bucket: float) -> SizingState:
    """Assemble equity-scaled sizing state for a bot from its closed trades (§11).

    equity        = starting_bucket + cumulative realised PnL
    peak_equity   = high-water mark of that equity curve (>= starting_bucket)
    consec_losses = trailing run of losing trades (most recent first)
    """
    async with acquire() as conn:
        agg = await conn.fetchrow(
            """
            WITH closed AS (
                SELECT pnl_net_usdt,
                       SUM(pnl_net_usdt) OVER (ORDER BY exit_ts, id) AS running
                FROM trades
                WHERE bot_id = $1 AND env = $2
                  AND exit_ts IS NOT NULL AND pnl_net_usdt IS NOT NULL
            )
            SELECT COALESCE(SUM(pnl_net_usdt), 0.0) AS realized,
                   COALESCE(MAX(running), 0.0) AS peak_running
            FROM closed
            """,
            bot_id,
            env,
        )
        recent = await conn.fetch(
            """
            SELECT pnl_net_usdt FROM trades
            WHERE bot_id = $1 AND env = $2
              AND exit_ts IS NOT NULL AND pnl_net_usdt IS NOT NULL
            ORDER BY exit_ts DESC, id DESC
            LIMIT 25
            """,
            bot_id,
            env,
        )
    realized = float(agg["realized"])
    equity = starting_bucket + realized
    peak_equity = starting_bucket + max(float(agg["peak_running"]), 0.0)
    consec = 0
    for r in recent:
        if float(r["pnl_net_usdt"]) <= 0.0:
            consec += 1
        else:
            break
    return SizingState(equity_usdt=equity, peak_equity_usdt=peak_equity, consec_losses=consec)


async def get_fleet_daily_summary(env: str, since_ts: int) -> dict[str, Any]:
    """Aggregate closed-trade stats across ALL bots for one env since `since_ts`.

    Powers the single daily summary (CLAUDE.md §27): one message for the whole
    fleet rather than one per bot. Returns real counts (trades, wins, win rate,
    net PnL, bots that traded) plus a live open-position count.
    """
    async with acquire() as conn:
        closed = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total_trades,
                   COUNT(*) FILTER (WHERE pnl_net_usdt > 0) AS wins,
                   COALESCE(SUM(pnl_net_usdt), 0.0) AS net_pnl,
                   COUNT(DISTINCT bot_id) AS bots
            FROM trades
            WHERE env = $1 AND exit_ts >= $2 AND pnl_net_usdt IS NOT NULL
            """,
            env,
            since_ts,
        )
        active = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM trades WHERE env = $1 AND exit_ts IS NULL",
            env,
        )
    total = int(closed["total_trades"])
    wins = int(closed["wins"])
    return {
        "total_trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": (wins / total) if total else 0.0,
        "net_pnl_usdt": float(closed["net_pnl"]),
        "bots_traded": int(closed["bots"]),
        "active_positions": int(active["cnt"]),
    }
