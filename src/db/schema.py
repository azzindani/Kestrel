"""
Layer 3 boundary — database schema DDL.
apply_schema() is idempotent: safe to run on every startup.
"""
from __future__ import annotations

import asyncpg

from src.db.connection import acquire

# ---------------------------------------------------------------------------
# DDL statements (order matters — foreign key dependencies)
# ---------------------------------------------------------------------------

_DDL = [
    # ------------------------------------------------------------------
    # candles
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS candles (
        id             BIGSERIAL PRIMARY KEY,
        bot_id         TEXT      NOT NULL,
        ts             BIGINT    NOT NULL,
        pair           TEXT      NOT NULL,
        timeframe      TEXT      NOT NULL,
        open           NUMERIC   NOT NULL,
        high           NUMERIC   NOT NULL,
        low            NUMERIC   NOT NULL,
        close          NUMERIC   NOT NULL,
        volume         NUMERIC   NOT NULL,
        ema9           NUMERIC,
        ema21          NUMERIC,
        rsi14          NUMERIC,
        atr14          NUMERIC,
        bb_upper       NUMERIC,
        bb_lower       NUMERIC,
        bb_width       NUMERIC,
        adx            NUMERIC,
        volume_ma20    NUMERIC,
        volume_ratio   NUMERIC,
        regime         TEXT,
        body_size      NUMERIC,
        total_range    NUMERIC,
        body_ratio     NUMERIC,
        upper_wick     NUMERIC,
        lower_wick     NUMERIC,
        direction      TEXT,
        env            TEXT      NOT NULL DEFAULT 'dev',
        UNIQUE (bot_id, pair, timeframe, ts)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles (pair, timeframe, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_candles_bot    ON candles (bot_id, ts DESC)",

    # ------------------------------------------------------------------
    # trades (referenced by signals — create before signals)
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS trades (
        id                      BIGSERIAL PRIMARY KEY,
        bot_id                  TEXT      NOT NULL,
        session_id              TEXT      NOT NULL,
        env                     TEXT      NOT NULL,
        pair                    TEXT      NOT NULL,
        timeframe               TEXT      NOT NULL,
        direction               TEXT      NOT NULL,
        pattern                 TEXT      NOT NULL,
        entry_ts                BIGINT    NOT NULL,
        exit_ts                 BIGINT,
        hold_candles            INTEGER,
        entry_price             NUMERIC   NOT NULL,
        exit_price              NUMERIC,
        tp_price                NUMERIC   NOT NULL,
        sl_price                NUMERIC   NOT NULL,
        liquidation_price       NUMERIC   NOT NULL,
        bucket_id               INTEGER   NOT NULL,
        size_usdt               NUMERIC   NOT NULL,
        leverage                INTEGER   NOT NULL,
        notional_usdt           NUMERIC   NOT NULL,
        close_reason            TEXT,
        pnl_gross_usdt          NUMERIC,
        fee_entry_usdt          NUMERIC   NOT NULL,
        fee_exit_usdt           NUMERIC,
        pnl_net_usdt            NUMERIC,
        pnl_pct                 NUMERIC,
        bucket_balance_before   NUMERIC   NOT NULL,
        bucket_balance_after    NUMERIC,
        context_pre_complete    BOOLEAN   DEFAULT FALSE,
        context_post_complete   BOOLEAN   DEFAULT FALSE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trades_entry   ON trades (entry_ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trades_bot     ON trades (bot_id, env, entry_ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trades_outcome ON trades (close_reason, env)",

    # ------------------------------------------------------------------
    # signals
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS signals (
        id               BIGSERIAL PRIMARY KEY,
        bot_id           TEXT      NOT NULL,
        session_id       TEXT      NOT NULL,
        env              TEXT      NOT NULL,
        ts               BIGINT    NOT NULL,
        pair             TEXT      NOT NULL,
        timeframe        TEXT      NOT NULL,
        candle_ts        BIGINT    NOT NULL,
        pattern          TEXT      NOT NULL,
        direction        TEXT      NOT NULL,
        confidence       NUMERIC   NOT NULL,
        regime           TEXT      NOT NULL,
        layer_regime     SMALLINT  NOT NULL,
        layer_trend      SMALLINT  NOT NULL,
        layer_momentum   SMALLINT  NOT NULL,
        layer_volume     SMALLINT  NOT NULL,
        layers_passed    SMALLINT  NOT NULL,
        outcome          TEXT      NOT NULL,
        reject_reason    TEXT,
        trade_id         BIGINT    REFERENCES trades(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_signals_ts      ON signals (ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_signals_pattern ON signals (pattern, outcome)",
    "CREATE INDEX IF NOT EXISTS idx_signals_bot     ON signals (bot_id, ts DESC)",

    # ------------------------------------------------------------------
    # trade_context
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS trade_context (
        trade_id        BIGINT    NOT NULL REFERENCES trades(id),
        candle_id       BIGINT    NOT NULL REFERENCES candles(id),
        candle_ts       BIGINT    NOT NULL,
        offset_candles  INTEGER   NOT NULL,
        offset_hours    NUMERIC   NOT NULL,
        window          TEXT      NOT NULL,
        PRIMARY KEY (trade_id, candle_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_context_trade ON trade_context (trade_id, window, offset_hours)",

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS events (
        id          BIGSERIAL PRIMARY KEY,
        bot_id      TEXT      NOT NULL,
        session_id  TEXT      NOT NULL,
        env         TEXT      NOT NULL,
        ts          BIGINT    NOT NULL,
        level       TEXT      NOT NULL,
        category    TEXT      NOT NULL,
        message     TEXT      NOT NULL,
        payload     JSONB,
        trade_id    BIGINT    REFERENCES trades(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_ts    ON events (ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_events_cat   ON events (category, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_events_bot   ON events (bot_id, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_events_trade ON events (trade_id)",

    # ------------------------------------------------------------------
    # heartbeats
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS heartbeats (
        bot_id  TEXT    PRIMARY KEY,
        ts      BIGINT  NOT NULL,
        pid     INTEGER NOT NULL,
        status  TEXT    NOT NULL,
        note    TEXT
    )
    """,

    # ------------------------------------------------------------------
    # pattern_memory
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS pattern_memory (
        pattern       TEXT    NOT NULL,
        direction     TEXT    NOT NULL,
        session       TEXT    NOT NULL,
        regime        TEXT    NOT NULL,
        sample_count  INTEGER NOT NULL DEFAULT 0,
        win_count     INTEGER NOT NULL DEFAULT 0,
        win_rate      NUMERIC,
        avg_pnl_pct   NUMERIC,
        last_updated  BIGINT,
        PRIMARY KEY (pattern, direction, session, regime)
    )
    """,
]


async def apply_schema() -> None:
    """Apply all DDL statements idempotently. Safe to call on every startup."""
    async with acquire() as conn:
        for stmt in _DDL:
            await conn.execute(stmt.strip())
