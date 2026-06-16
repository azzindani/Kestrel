# 11 · Operations

How Kestrel runs, supervises itself, recovers, and reports. This covers the daemon lifecycle,
the multi-bot event loop, the watchdog, scheduled jobs, Telegram alerts, the terminal
dashboard, and Grafana.

## 1. Process map

```
WATCHDOG (separate OS process)
  └── supervises MAIN · restarts on unexpected exit · heartbeat check every 60s
        └── MAIN PROCESS (one asyncio event loop)
              ├── shared: DB pool · TelegramNotifier · PortfolioGuard
              ├── global tasks: cleanup · trade_context_post · daily_summary
              └── per bot (×N from bots.json):
                    ├── feed listener  (WS or REST poll · shared per exchange)
                    ├── candle builder  (tick → OHLCV → emit on close → queue)
                    ├── candle processor (signal → risk → execution per closed candle)
                    └── heartbeat       (writes heartbeats row every 30s)
```

## 2. Daemon bootstrap (`engine/daemon.py::main()`)

```
1. load + validate .env (dotenv)
2. init DB pool (min 4 / max 32) · apply schema (idempotent)
3. init + start TelegramNotifier
4. build one Daemon per bots.json entry (or a single bot from .env)
5. register SIGTERM → _stop_all() (stop shared feeds, then stop each daemon)
6. launch global tasks: cleanup · trade_context_post · daily_summary
7. construct PortfolioGuard; if enabled, attach all daemons and add guard.run() to the task set
8. asyncio.gather(*[d.start() for d in daemons])   # all bots, one loop
```

Multiple bots share **one** DB pool, **one** notifier, and **one** guard. The terminal
dashboard is auto-disabled when more than one bot runs (it is a single-bot view).

## 3. Per-bot candle processing (`_process_candle`)

The hot path, executed for each closed candle a bot receives:

```
1. reset session PnL at UTC midnight (ts // 86_400_000 boundary)
2. write the candle → DB (returns candle_id)
3. if a position is open: link 'during' trade_context
4. SIMULATION ONLY: update_price() + check_exits() → close if TP/SL/trail/timeout/liq hit
5. load the last 120 candles (the detector window)
6. build BucketState (active positions, last WS reconnect ts, session PnL)
7. read SizingState from DB (equity, peak, consec losses) — equity-scaled sizing
8. evaluate(window, params, ..., sizing_state, leverage) → Signal | Rejection
9. risk.validate(signal, state, cfg) → ValidationResult
10. on pass: execution.place_order(signal)
11. write the trade row (returns trade_id); track in _open_trade_ids[pair]
12. link 48h of 'pre' trade_context
13. write the signal row (outcome=fired, trade_id) — or the rejection (outcome=rejected)
14. fire the Telegram signal_fired alert
```

On exit (`_close_position`): call `execution.close_position(pair, reason)`, accumulate session
PnL, update the trade row with the final PnL + `bucket_balance_after`, and fire the
profit/loss/liquidation Telegram alert. `Daemon.force_close_all(reason)` closes every open
position the bot owns — the hook the [portfolio guard](06-risk-and-capital.md#5-the-portfolio-guard--the-manager-bot-engineportfolio_guardpy)
calls. `Daemon.portfolio_snapshot()` returns `(equity, unrealised)` for the guard to aggregate.

**Position monitoring is simulation-side.** In sim, `check_exits()` runs every candle. In live,
the exchange holds the TP/SL orders. Either way, the daemon never holds an open leveraged
position with no monitoring alive.

## 4. The watchdog (`engine/watchdog.py`)

A separate OS process that keeps MAIN alive.

- Constants: restart delay **10 s**, heartbeat interval **60 s**, heartbeat timeout **90 s**.
- It reads the bot IDs from `bots.json` and checks the `heartbeats` table for **all** of them
  every 60 s. If any bot's heartbeat is stale (> 90 s), it kills the child and restarts after
  10 s. A transient DB error does **not** trigger a kill (it returns "all fresh" defensively).
- Restart-after-forced-kill is one of the §18 go-live criteria — proven via this mechanism.

## 5. Scheduled jobs (`engine/scheduler.py`) — global, once per process

| Task | Cadence | Action |
|---|---|---|
| `heartbeat_task` | every **30 s** (per bot) | upsert the bot's `heartbeats` row (`running`) |
| `daily_summary_task` | **00:00 UTC** | `get_fleet_daily_summary` across all bots → one Telegram message |
| `trade_context_post_task` | every **1 h** | fill the 48h `post` window for trades whose exit is now > 48h old; mark `context_post_complete` |
| `cleanup_task` | **03:00 UTC** | delete expired candles(>90d, unlinked)/signals(>60d)/events(>30d) → `VACUUM ANALYZE` |

The three non-heartbeat tasks run **once for the whole process**, not per bot.

## 6. Telegram notifications (`notify/telegram.py`)

The alert channel — **signal, not noise** (`CLAUDE.md` §27). `_TIMEOUT = 10 s`,
`_MAX_RETRIES = 3` with exponential backoff. Failures are swallowed on the final retry —
**Telegram must never crash the daemon.** Messages are HTML, prefixed by level
(ℹ INFO / ⚠️ WARN / 🔴 ERROR / 🚨 CRITICAL).

Structured alerts: `signal_fired` (pattern, direction, confidence, entry/TP/SL),
`trade_closed_profit` / `trade_closed_loss` (exit, PnL, reason, bucket balance), `liquidation`
(CRITICAL), `ws_reconnect` (WARN, attempt N/5), `regime_change`, and the `daily_summary`.

> **Operational note:** with 120 bots, Telegram is **loud**. It is currently enabled; muting is
> a one-line override change (`TELEGRAM_TOKEN` removed / dummy) if the volume is unwanted.

## 7. Terminal dashboard (`viz/dashboard.py`)

A `rich`-based, **DB-backed** live view (single-bot only). It refreshes once per second and
reads the last 20 rows from the `events` table — it is **not** a file tail. Panels: a header
(bot_id, session, uptime, regime), a market panel (price, EMA9/21, RSI14, ATR14, volume ratio),
a bucket panel (open position or "no position", session PnL coloured by sign, W/L count, win
%), and a rolling events table. For fleets, observability is via Grafana and the `events` table
instead.

## 8. Grafana

A provisioned Grafana board at **`https://kestrel.casava.space`** (login `admin` /
`.env GRAFANA_ADMIN_PASSWORD`). The board is **phase-aware** — a `$env` template variable
switches between labs (`dev`), staging, and prod. A dedicated staging/live-ops board exists for
quarantine monitoring.

> **Known infra gotcha:** a shared `caddy-router` once routed `grafana:3000` ambiguously to a
> *different* project's Grafana; the fix pins the explicit container name in the Caddyfile, and
> bind-mounted Caddyfile edits require `docker restart caddy-router` (inode pin), not a reload.

## 9. Lifecycle states

```
START : load env → connect DB → verify exchange creds → reconcile positions (DB vs venue)
        → connect feed → enter event loop
LOOP  : tick → candle builder · candle close → signal pipeline · 30s → heartbeat
        · every candle → monitor open positions · WS drop → backoff → CRITICAL → wait
STOP  : (SIGTERM only) cancel orders → close all positions at market → write final state
        → disconnect → exit 0
CRASH : log traceback → events table → Telegram CRITICAL → exit 1 → watchdog restarts after 10s
```

All logging is structured rows in the `events` table — there is **no `print()`** anywhere in
the codebase.
