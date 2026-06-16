# 08 · Database (Layer 3 — boundary)

PostgreSQL is Kestrel's **single source of truth** for position and capital state. Nothing
authoritative lives only in memory; on restart the daemon reconciles from the DB and the
exchange. The schema is multi-bot from day one: **every record carries `bot_id`, `env`, and a
`ts` in Unix milliseconds**. The schema file (`src/db/schema.py`) is **frozen** — any change
is a human-gated migration (`CLAUDE.md` §4).

## 1. Connection pool (`db/connection.py`)

```python
asyncpg.create_pool(dsn=..., min_size=4, max_size=32, command_timeout=30)
```

`max_size = 32` is sized to absorb the *thundering herd* of ~120 bots that close candles in
near-lockstep on a timeframe boundary. The mixed-timeframe lab helps here: 5m/15m/1h/4h closes
stagger, so only the 4h-aligned tops of the hour see all 120 bots fire at once. One pool is
shared by every bot in the process.

## 2. The tables

All tables include `bot_id TEXT NOT NULL`, `env TEXT NOT NULL`, and `ts BIGINT` (unix ms).

### `candles`
OHLCV + the ten precomputed indicators + candle geometry, computed once at close and stored
(never recomputed). `UNIQUE (bot_id, pair, timeframe, ts)` — writes are idempotent upserts.
Columns include `ema9, ema21, rsi14, atr14, bb_upper, bb_lower, bb_width, adx, volume_ma20,
volume_ratio, regime` and `body_size, total_range, body_ratio, upper_wick, lower_wick,
direction`. Indexed by `(pair, timeframe, ts DESC)` and `(bot_id, ts DESC)`.

### `signals`
Every pipeline outcome — **fired, rejected, or expired** — with the pattern, direction,
confidence, regime, the four layer flags + `layers_passed`, `reject_reason`, and a nullable FK
to the resulting `trades.id`. This is the audit trail of *every decision*, not just trades —
which is what makes "why didn't bot X trade?" answerable (look for the rejection rows).

### `trades`
The full lifecycle of a position: entry/exit timestamps & prices, `tp_price`, `sl_price`,
`liquidation_price`, bucket id, `size_usdt`, leverage, notional, `close_reason`
(`take_profit | stop_loss | timeout | manual | liquidated`), the full PnL breakdown
(`pnl_gross_usdt`, `fee_entry_usdt`, `fee_exit_usdt`, `pnl_net_usdt`, `pnl_pct`), and
`bucket_balance_before/after`. Trades are kept **indefinitely** (training data).

### `trade_context`
The **labelled-dataset** system. Every closed trade is automatically linked to the candles
around it: 48 h **before** (`window='pre'`), every candle **during** the hold
(`window='during'`), and 48 h **after** (`window='post'`, filled by a background job once 48 h
have elapsed). Each link records `offset_candles`, `offset_hours`, and the window. Candles
referenced by `trade_context` are **never deleted**, even past the 90-day candle retention —
they are the project's accumulating training corpus.

### `events`
The **only logging channel** — there is no `print()` anywhere in the codebase. One JSONB row
per event: `level` (`INFO|WARN|ERROR|CRITICAL`), `category`
(`signal|order|position|risk|connection|system`), a short `message`, a self-contained
`payload` JSONB, and an optional `trade_id`. The terminal dashboard and `logs.sh` read from
this table.

### `heartbeats`
One row per bot (`bot_id PRIMARY KEY`): `ts`, `pid`, `status`, `note`. The watchdog and the
Docker healthcheck read this to detect a stalled daemon (no fresh heartbeat in 90 s → restart).

### `pattern_memory`
Aggregated outcome statistics keyed `(pattern, direction, session, regime)`: `sample_count`,
`win_count`, `win_rate`, `avg_pnl_pct`, `last_updated`. The signal engine reads this at
evaluation to gently adjust confidence and suppress chronically-losing patterns (see
[Signal Engine §2 Stage 3](04-signal-engine.md#stage-3--pattern-scan-detectorpy--patternspy)).

## 3. Retention policy

| Data | Retention |
|---|---|
| `candles` **not** referenced by `trade_context` | 90 days rolling |
| `candles` referenced by `trade_context` | **indefinite** (training data — never deleted) |
| `signals` | 60 days |
| `events` | 30 days |
| `trades`, `trade_context` | **indefinite** |

Enforced nightly at 03:00 UTC by the cleanup task / `cleanup.sh`: delete expired rows, then
`VACUUM ANALYZE`.

## 4. The writer API (`db/writer.py`)

All async. The declared write surface (`CLAUDE.md` §8) is `write_candle`, `write_signal`,
`write_trade`, `write_event`; the module also provides the lifecycle and read helpers the
daemon needs:

| Function | Purpose |
|---|---|
| `write_candle(candle) -> int` | upsert on `(bot_id, pair, timeframe, ts)`; returns DB id |
| `write_signal(signal, outcome, reject_reason, trade_id) -> int` | persist a fired/rejected/expired decision |
| `write_trade(trade_dict) -> int` | open a trade row; returns `trade_id` |
| `close_trade(trade_id, close_dict)` | fill in exit fields + final PnL |
| `write_event(...)` | the structured logging primitive |
| `write_heartbeat(bot_id, ts, pid, status, note)` | upsert on `bot_id` |
| `link_pre_context / link_during_context / link_post_context` | build the `trade_context` windows |
| `trades_pending_post_context(...)` | trades whose 48 h post-window is now fillable |
| `load_recent_candles(bot_id, pair, timeframe, limit)` | last *N* candles, oldest→newest (the detector window) |
| `get_sizing_state(bot_id, env, starting_bucket) -> SizingState` | equity = start + Σ realised PnL; peak equity; trailing consec-loss count |
| `count_active_positions(bot_id, env) -> int` | open positions (`exit_ts IS NULL`) |
| `get_fleet_daily_summary(env, since_ts) -> dict` | fleet-wide aggregate for the daily Telegram summary |

`get_sizing_state` is the bridge that makes [equity-scaled
sizing](06-risk-and-capital.md#3-equity-scaled-position-sizing) live: each candle the daemon
reads the bucket's real equity/peak/loss-streak from closed trades and hands it to
`evaluate()`.

## 5. The tuning analysis query

`CLAUDE.md` §21 ships a canonical query that joins `trade_context` → `candles` → `trades` to
profile what the market looked like *before* and *during* trades by close reason — the basis
for data-driven parameter tuning. The output is meant to be pasted into a chat session for
analysis. The schema's whole "label every trade with its surrounding context" design exists to
make this kind of question answerable.
