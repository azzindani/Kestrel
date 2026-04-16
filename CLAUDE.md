# CLAUDE.md — Kestrel

> Standards: architecture/STANDARDS.md · agent/STANDARDS.md (azzindani/Standards)
> Override: project-specific rules below. ✗ re-state standard rules — reference only.

---

## 1. Role

Systems engineer · Python · asyncio · PostgreSQL · ccxt · real-time event pipelines.
All code production-grade · no shortcuts · no TODOs · no console output — structured logging only.

---

## 2. Core Principles

Follow architecture/STANDARDS.md (all 22 principles). Project applications:

| Principle | Application |
|---|---|
| Unidirectional flow | candle-close → signal → risk → execution · ✗ reverse |
| Function: I/O or logic · ✗ both | signal engine = pure logic · execution = I/O boundary |
| Register capabilities · ✗ hardcode | patterns registered into registry · ✗ if/else dispatch |
| Policy / mechanism separation | risk manager = mechanism · signal engine = policy |
| Config schema inner · loading outer | schema → `src/config.py` · loading → `scripts/` |
| Explicit absence | `Optional[X]` with typed result · ✗ None as silent sentinel |
| Single source of truth | position state authoritative in DB · ✗ in-memory only |
| Fail fast · supervisor recovers | crash → watchdog restarts · ✗ silent swallow |
| Idempotent retriable ops | order placement idempotent via idempotency key |
| One owner per resource | DB owns state · ✗ concurrent writers without lock |

---

## 3. ! Critical Restrictions

```
✗ agent modifies: risk/manager.py · execution/live.py · scripts/*.sh · .env · CLAUDE.md
✗ hardcoded credentials — .env only · ✗ credentials past boundary layer
✗ inner layers import boundary layers (Layer 3 → Layer 0/1/2 only)
✗ open leveraged position with no monitoring process alive
✗ deploy to prod without GO from install.sh
✗ real money before all go-live criteria met (Section 18)
✗ print() anywhere — structured logging to events table only
✗ TODO or FIXME in risk/ or execution/ modules
✗ in-memory as sole source of position state — DB is authoritative
✗ hardcoded indicator values — params.json with defined ranges only
✗ backtest without fee + slippage model applied
✗ in-sample backtest only — walk-forward required (train 60% · test 40%)
✗ None as silent sentinel — use Optional[X] with explicit check
✗ global mutable state
✗ logic + I/O in same function
✗ wildcard imports
✗ local time — all timestamps Unix milliseconds (BIGINT)
```

---

## 4. 🛑 Stop and Ask Before

```
- Schema migration or new table
- Any change to risk/manager.py
- Removing any public API function
- Adding new top-level module
- Changing leverage or bucket size in params.json
- Any action on prod environment with real capital
- force-push to any branch
```

---

## 5. Investigation Protocol

Bug fix or unexpected behavior → follow in order · ✗ skip steps:

```
1. Read       — source files · events table logs · error payload JSONB
2. Trace      — follow candle → signal → risk → execution data flow end-to-end
3. Cross-check — params.json · DB state · exchange state for drift
4. Root cause — fix actual cause · ✗ patch downstream symptom
5. Verify     — grep same pattern elsewhere · run backtest · confirm metrics hold
```

✗ guess-and-check · ✗ change multiple things simultaneously.

---

## 6. Project Identity

**Name:** Kestrel
**External description:** Real-time signal detection and execution daemon. ✗ describe as trading bot externally.
**Repo:** Standalone, independent, private during development.
**bot_id format:** `{env}-{pair}-{timeframe}-{instance}` e.g. `prod-BTCUSDT-5m-01`
**All timestamps:** Unix milliseconds (BIGINT) · ✗ local time anywhere in codebase.

---

## 7. Layer Model (architecture/STANDARDS.md §2)

```
Layer 0 (innermost): src/config.py
  → types · enums · constants · pure utilities · ✗ I/O ever

Layer 1: engine/ · signal/ · risk/ · backtest/
  → domain logic · pure transforms · ✗ I/O

Layer 2: data/
  → assembles external stream data into Layer 0/1 domain types
  → candle builder, indicator computation

Layer 3 (boundary): execution/ · db/ · notify/ · viz/
  → all I/O exclusively here · adapters · external integrations
```

**Dependency rules:**
```
Layer 0 → nothing
Layer 1 → Layer 0 only
Layer 2 → Layer 0 · Layer 1
Layer 3 → any inner layer
✗ inner layers import Layer 3
execution/live.py · execution/simulation.py → identical interface · swapped via DI at startup
```

**Function classification (every function = one type):**
```
Logic function: inner layers · ✗ I/O · pure transform: data in → data out
Shell function: boundary layer only · reads/writes externals · calls logic functions
I/O + transform in one function → split into shell + logic
```

---

## 8. Module Public APIs

Undeclared = internal · ✗ consumed externally.

```
signal/detector.py:    evaluate(candles: list[Candle], params: Params) -> Signal | None
signal/patterns.py:    registry: dict[str, PatternFn]
risk/manager.py:       validate(signal: Signal, state: BucketState) -> ValidationResult
execution/interface.py: place_order · cancel_order · get_position · close_position
db/writer.py:          write_candle · write_signal · write_trade · write_event (async)
```

---

## 9. Extension Architecture — Pattern Registration

Patterns register into `signal/patterns.py` registry.
Add pattern = new registered function · ✗ modify detector.py.

```python
PatternFn = Callable[[list[Candle], Params], PatternResult | None]
registry: dict[str, PatternFn] = {}

def register(name: str) -> Callable:
    def wrap(fn: PatternFn) -> PatternFn:
        registry[name] = fn
        return fn
    return wrap

# Usage:
@register("impulse_retracement")
def detect_impulse_retracement(candles: list[Candle], params: Params) -> PatternResult | None:
    ...
```

New session-awareness threshold set → new registered threshold profile · ✗ modify existing session logic.

---

## 10. Error Architecture (architecture/STANDARDS.md §7)

| Error type | Strategy |
|---|---|
| Programmer error | fail fast · crash · fix code |
| Data error | return in `Result` type · ✗ raise |
| Environment error | raise · watchdog handles restart |
| WS disconnect | exponential backoff · max 5 retries · Telegram alert · wait |
| Exchange failures | circuit breaker: 5 consecutive → stop orders · 30s cooldown · probe |
| Partial failure | accumulate all errors · ✗ stop on first |

Graceful degradation: WS drop → suspend signal evaluation · maintain position monitoring · ✗ crash.
✗ open position with no monitoring process alive — stop.sh closes all positions before exit.

---

## 11. State Architecture

```
Ownership:
  PostgreSQL owns position state — authoritative
  On restart: reconcile DB + exchange state · ✗ assume in-memory is current
  Signal engine: stateless per evaluation · reads candle history from DB
  Pattern memory: read from DB at evaluation · write after trade close · ✗ cache in-process

Concurrency:
  ✗ shared mutable state between coroutines
  Pass state explicitly | use asyncio.Queue
  Every async operation: explicit timeout
  WebSocket listener: dedicated coroutine · ✗ block event loop

Unidirectional:
  candle-close event → signal pipeline → risk check → execution
  ✗ execution layer feeds back into signal layer
  Feedback (e.g. position update affecting next signal) = new DB read · ✗ backward reference
```


---

## 12. Project Structure

```
kestrel/
├── CLAUDE.md · README.md · .env.example · params.json · requirements.txt
├── scripts/
│   ├── install.sh   ← full setup from zero · prints GO|NO-GO
│   ├── start.sh     ← start daemon + watchdog
│   ├── stop.sh      ← graceful · closes positions first · ✗ hard kill
│   ├── restart.sh   ← stop → wait → start
│   ├── status.sh    ← health check
│   ├── update.sh    ← git pull → validate → restart
│   ├── logs.sh      ← tail events with filter
│   ├── tune.sh      ← param change → backtest → compare → ACCEPT|REVERT
│   └── cleanup.sh   ← retention + VACUUM
├── kestrel.service  ← systemd unit
└── src/
    ├── config.py          ← Layer 0: types · enums · env schema · ✗ I/O
    ├── engine/            ← Layer 1: daemon · watchdog · scheduler
    ├── data/              ← Layer 2: feed (WS) · candle builder
    ├── signal/            ← Layer 1: indicators · regime · patterns · detector · memory
    ├── execution/         ← Layer 3 boundary: interface · live · simulation
    ├── risk/              ← Layer 1: manager (✗ agent · human-only)
    ├── db/                ← Layer 3 boundary: connection · schema · writer
    ├── backtest/          ← Layer 1: runner · metrics
    ├── notify/            ← Layer 3 boundary: telegram
    └── viz/               ← Layer 3 boundary: terminal dashboard
```

---

## 13. Hard Constraints

```
Instrument:      spot isolated margin only · ✗ futures · ✗ options · ✗ derivatives
Leverage:        10x–50x
Bucket size:     $10 USDT · independent isolated collateral · ✗ shared pool
Timeframes:      5m (entry) · 15m (regime filter)
Pairs (initial): BTCUSDT · ETHUSDT · expand only after validation
DB:              PostgreSQL · multi-bot from day one · bot_id on every record
Fee model:       taker 0.04% entry + 0.04% exit + 0.05% slippage/side = ~0.18% round trip
Min edge:        avg gain per trade > 0.18% · enforced in backtest · ✗ skip
VPS:             Singapore | Tokyo · 1 vCPU · 1GB RAM · 20GB SSD · Ubuntu 22.04 LTS · $4–6/month
```

---

## 14. Environment Separation

```
ENV=dev   → simulation engine · testnet keys · DEBUG logging
ENV=prod  → live engine · real keys · INFO logging

One codebase · .env is the only switch
✗ code branches on ENV except DI at startup
DI at startup: dev → inject SimulationExecution · prod → inject LiveExecution
```

**.env required keys:**
```
ENV · BOT_ID
EXCHANGE · API_KEY · API_SECRET · TESTNET
DB_HOST · DB_PORT · DB_NAME · DB_USER · DB_PASSWORD
PAIR · TIMEFRAME_ENTRY · TIMEFRAME_REGIME
LEVERAGE · BUCKET_SIZE_USDT · MAX_ACTIVE_BUCKETS
TELEGRAM_TOKEN · TELEGRAM_CHAT_ID · LOG_LEVEL
```

---

## 15. Scripts Contract

```
install.sh   → Python≥3.11 · venv · deps · .env complete · DB reachable · schema applied
               exchange auth valid · Telegram reachable · params valid
               → prints [GO] | [NO-GO] + failure reason · ✗ proceed past NO-GO

stop.sh      → SIGTERM → cancel orders → close positions at market → disconnect → exit 0
               ✗ hard kill · ✗ exit with open positions

update.sh    → git pull → install.sh → GO: restart · NO-GO: abort (stay on current version)

cleanup.sh   → 03:00 UTC daily:
               DELETE unlinked candles >90d
               DELETE signals >60d · events >30d
               VACUUM ANALYZE

tune.sh      → record rollback → update params.json → 30d backtest →
               compare vs baseline metrics →
               all improve/hold → ACCEPT · save baseline |
               any regress >5% → REJECT · revert params.json
```

---

## 16. Daemon Lifecycle

```
START:
  1. load + validate .env
  2. connect PostgreSQL — abort if unreachable
  3. connect exchange REST — verify credentials
  4. reconcile positions: DB state vs exchange state
  5. connect WebSocket — begin streaming
  6. enter main event loop

LOOP:
  on tick:         update candle builder
  on candle close: signal pipeline → execute if fires
  every 30s:       heartbeat → heartbeats table
  every candle:    monitor open positions: TP | SL | timeout (max 3–5 candles)
  on WS drop:      exponential backoff · max 5 retries → Telegram CRITICAL · wait
  ✗ place orders within 60s of WS reconnection (stale data)

STOP (SIGTERM only):
  cancel orders → close all positions at market → write final state → disconnect → exit 0

CRASH:
  log traceback → events table → Telegram CRITICAL → exit 1 → watchdog restarts after 10s
```

**Process map:**
```
WATCHDOG (OS process)
  → supervises main · restarts on unexpected exit · heartbeat check every 60s
  └── MAIN PROCESS
        ├── WebSocket listener   (asyncio · always-on)
        ├── Candle builder       (tick → OHLCV · emits on close)
        ├── Signal engine        (pure evaluation on candle close)
        ├── Position monitor     (async · TP/SL/timeout every candle)
        ├── Risk manager         (validates before every order · pure)
        └── DB writer            (async · non-blocking)
```

---

## 17. Capital and Risk Model

**Bucket architecture:**
```
Total simulated:  $100 USDT
Bucket size:      $10 USDT isolated per position
Max buckets:      10 · active buckets at start: 1
Liquidated bucket → log loss → slot reopens → next bucket fresh
Capital state:    authoritative in DB · ✗ in-memory only
```

**Liquidation formula (computed + stored on every position open):**
```
long:  entry × (1 - 1/leverage + maintenance_margin_rate)
short: entry × (1 + 1/leverage - maintenance_margin_rate)
maintenance_margin_rate = 0.005 (Binance spot margin · BTC/ETH)
```

**Fee model (enforced in simulation and backtest · ✗ skip):**
```
Taker fee:        0.04% per side
Round trip:       0.08%
Slippage:         0.05% per side
Total round trip: ~0.18%
Min viable trade: avg gross gain > 0.18%
```

**Return context (not a deadline):**
```
Phase 1: $100 → $320 → withdraw $120 (capex recovery) · sustain $200
Phase 2: scale capital · increase risk profile
```


---

## 18. Go-Live Criteria (human-enforced · ✗ skip any)

```
[ ] install.sh → [GO] on clean Colab session
[ ] testnet paper trading: 14 days · zero unplanned crashes
[ ] walk-forward backtest: win rate >55% out-of-sample
[ ] simulated fee+slippage vs real testnet fills: <15% deviation
[ ] watchdog: proven restart after forced kill
[ ] stop.sh: confirmed graceful close of all positions
[ ] ✗ TODO/FIXME in risk/ or execution/
[ ] one full session log reviewed by human before go-live
[ ] Telegram alerts confirmed working end-to-end
[ ] DB backup cron confirmed (pg_dump · daily)
```

---

## 19. Database Schema

All tables: `bot_id TEXT NOT NULL` · `env TEXT NOT NULL` · `ts BIGINT` (unix ms) on every record.

### candles
```sql
CREATE TABLE candles (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL, ts BIGINT NOT NULL, pair TEXT NOT NULL, timeframe TEXT NOT NULL,
    open NUMERIC NOT NULL, high NUMERIC NOT NULL, low NUMERIC NOT NULL,
    close NUMERIC NOT NULL, volume NUMERIC NOT NULL,
    -- indicators (computed at close · stored · ✗ recomputed)
    ema9 NUMERIC, ema21 NUMERIC, rsi14 NUMERIC, atr14 NUMERIC,
    bb_upper NUMERIC, bb_lower NUMERIC, bb_width NUMERIC,
    adx NUMERIC, volume_ma20 NUMERIC, volume_ratio NUMERIC, regime TEXT,
    -- candle geometry (precomputed)
    body_size NUMERIC, total_range NUMERIC, body_ratio NUMERIC,
    upper_wick NUMERIC, lower_wick NUMERIC, direction TEXT,
    UNIQUE (bot_id, pair, timeframe, ts)
);
CREATE INDEX idx_candles_lookup ON candles (pair, timeframe, ts DESC);
CREATE INDEX idx_candles_bot ON candles (bot_id, ts DESC);
```

### signals
```sql
CREATE TABLE signals (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL, session_id TEXT NOT NULL, env TEXT NOT NULL,
    ts BIGINT NOT NULL, pair TEXT NOT NULL, timeframe TEXT NOT NULL, candle_ts BIGINT NOT NULL,
    pattern TEXT NOT NULL,      -- 'impulse_retracement' | 'wick_rejection' |
                                --  'compression_breakout' | 'momentum_continuation' | 'anomaly_fade'
    direction TEXT NOT NULL,    -- 'long' | 'short'
    confidence NUMERIC NOT NULL, regime TEXT NOT NULL,
    layer_regime SMALLINT NOT NULL, layer_trend SMALLINT NOT NULL,
    layer_momentum SMALLINT NOT NULL, layer_volume SMALLINT NOT NULL,
    layers_passed SMALLINT NOT NULL,
    outcome TEXT NOT NULL,      -- 'fired' | 'rejected' | 'expired'
    reject_reason TEXT,
    trade_id BIGINT REFERENCES trades(id)
);
CREATE INDEX idx_signals_ts ON signals (ts DESC);
CREATE INDEX idx_signals_pattern ON signals (pattern, outcome);
CREATE INDEX idx_signals_bot ON signals (bot_id, ts DESC);
```

### trades
```sql
CREATE TABLE trades (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL, session_id TEXT NOT NULL, env TEXT NOT NULL,
    pair TEXT NOT NULL, timeframe TEXT NOT NULL, direction TEXT NOT NULL, pattern TEXT NOT NULL,
    entry_ts BIGINT NOT NULL, exit_ts BIGINT, hold_candles INTEGER,
    entry_price NUMERIC NOT NULL, exit_price NUMERIC,
    tp_price NUMERIC NOT NULL, sl_price NUMERIC NOT NULL, liquidation_price NUMERIC NOT NULL,
    bucket_id INTEGER NOT NULL, size_usdt NUMERIC NOT NULL,
    leverage INTEGER NOT NULL, notional_usdt NUMERIC NOT NULL,
    close_reason TEXT,          -- 'take_profit' | 'stop_loss' | 'timeout' | 'manual' | 'liquidated'
    pnl_gross_usdt NUMERIC, fee_entry_usdt NUMERIC NOT NULL,
    fee_exit_usdt NUMERIC, pnl_net_usdt NUMERIC, pnl_pct NUMERIC,
    bucket_balance_before NUMERIC NOT NULL, bucket_balance_after NUMERIC,
    context_pre_complete BOOLEAN DEFAULT FALSE, context_post_complete BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_trades_entry ON trades (entry_ts DESC);
CREATE INDEX idx_trades_bot ON trades (bot_id, env, entry_ts DESC);
CREATE INDEX idx_trades_outcome ON trades (close_reason, env);
```

### trade_context
```sql
CREATE TABLE trade_context (
    trade_id BIGINT NOT NULL REFERENCES trades(id),
    candle_id BIGINT NOT NULL REFERENCES candles(id),
    candle_ts BIGINT NOT NULL,
    offset_candles INTEGER NOT NULL,   -- negative=before · 0=during · positive=after
    offset_hours NUMERIC NOT NULL,
    window TEXT NOT NULL,              -- 'pre' | 'during' | 'post'
    PRIMARY KEY (trade_id, candle_id)
);
CREATE INDEX idx_context_trade ON trade_context (trade_id, window, offset_hours);
```

### events
```sql
CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL, session_id TEXT NOT NULL, env TEXT NOT NULL,
    ts BIGINT NOT NULL,
    level TEXT NOT NULL,       -- 'INFO' | 'WARN' | 'ERROR' | 'CRITICAL'
    category TEXT NOT NULL,    -- 'signal' | 'order' | 'position' | 'risk' | 'connection' | 'system'
    message TEXT NOT NULL,
    payload JSONB,             -- full structured context · every event self-contained
    trade_id BIGINT REFERENCES trades(id)
);
CREATE INDEX idx_events_ts ON events (ts DESC);
CREATE INDEX idx_events_cat ON events (category, ts DESC);
CREATE INDEX idx_events_bot ON events (bot_id, ts DESC);
CREATE INDEX idx_events_trade ON events (trade_id);
```

### heartbeats · pattern_memory
```sql
CREATE TABLE heartbeats (
    bot_id TEXT PRIMARY KEY, ts BIGINT NOT NULL, pid INTEGER NOT NULL,
    status TEXT NOT NULL, note TEXT         -- 'running' | 'stopping' | 'error'
);

CREATE TABLE pattern_memory (
    pattern TEXT NOT NULL, direction TEXT NOT NULL, session TEXT NOT NULL, regime TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0, win_count INTEGER NOT NULL DEFAULT 0,
    win_rate NUMERIC, avg_pnl_pct NUMERIC, last_updated BIGINT,
    PRIMARY KEY (pattern, direction, session, regime)
);
```

**Retention policy:**
```
candles (not in trade_context): 90d rolling
candles (in trade_context):     indefinite — training data · ✗ delete
signals:                        60d rolling
events:                         30d rolling
trades:                         indefinite
trade_context:                  indefinite
```

---

## 20. Log Schema

Every event → single JSONB row in events table · ✗ multiline · ✗ prose messages.
`payload` = full structured context for that event type.

**Log categories:** `signal · order · position · risk · connection · system`
**Log levels:** `INFO · WARN · ERROR · CRITICAL`

**Payload example (signal_fired):**
```json
{
  "ts": 1744812721443, "session": "dev-colab-004", "event": "signal_fired",
  "pair": "BTCUSDT", "timeframe": "5m", "candle_close": 83421.50,
  "signal": { "pattern": "impulse_retracement", "direction": "long",
               "confidence": 0.78, "layers_passed": ["trend","momentum","volume"] },
  "indicators": { "ema9": 83380.20, "ema21": 83210.50, "rsi14": 54.3,
                  "atr14": 210.40, "volume_ratio": 1.62 },
  "order": { "side": "buy", "size_usdt": 10.00, "leverage": 20,
              "entry": 83421.50, "tp": 83756.30, "sl": 83211.10,
              "tp_pct": 0.40, "sl_pct": 0.25, "max_hold_candles": 4 }
}
```

**Terminal live stream:** `rich` · rolling 20 events from events table · updates on candle close · ✗ file tail.

---

## 21. Trade Context Window

Every closed trade → automatic labeled dataset entry (48h before + during + 48h after).

```
On ENTRY:   link candles from (entry_ts - 48h) to entry_ts → window='pre'
While OPEN: link each new closed candle                      → window='during'
On EXIT:    schedule background job: runs after 48h from exit_ts
            links candles exit_ts to (exit_ts + 48h)         → window='post'
            mark context_post_complete = TRUE
```

**Analysis query (paste output into chat for tuning):**
```sql
SELECT tc.window, ROUND(tc.offset_hours) AS hour_offset, t.close_reason,
       AVG(c.volume_ratio) AS avg_vol_ratio, AVG(c.rsi14) AS avg_rsi,
       AVG(c.adx) AS avg_adx, AVG(c.body_ratio) AS avg_body_ratio,
       COUNT(*) AS sample_count
FROM trade_context tc
JOIN candles c ON tc.candle_id = c.id
JOIN trades t  ON tc.trade_id  = t.id
WHERE t.env = 'prod' AND t.pattern = 'impulse_retracement'
  AND tc.window IN ('pre', 'during')
GROUP BY tc.window, ROUND(tc.offset_hours), t.close_reason
ORDER BY tc.window, hour_offset, t.close_reason;
```

---

## 22. Signal Engine

**Pipeline (pure functions · no I/O):**
```
candle_close
  → regime_filter(candles, params) → RegimeResult | Rejection
  → trend_filter(candles, params)  → TrendResult  | Rejection
  → pattern_scan(candles, params)  → PatternResult | Rejection   (registry lookup)
  → volume_confirm(candle, params) → VolumeResult  | Rejection
  → build_signal(results, params)  → Signal
  → risk_manager.validate(signal, state) → ValidationResult
  → execution.place_order(signal)  ← boundary (I/O here only)
```

Each stage returns typed result | typed rejection · ✗ exceptions for flow control.
Rejection logged to signals table with `outcome='rejected'` + `reject_reason`.

**Indicators (computed at candle close · stored in candles table · ✗ recomputed later):**
```
ATR(14)       → dynamic TP/SL sizing · volatility baseline
EMA(9/21)     → trend direction · momentum (cross detection)
RSI(14)       → momentum filter · overextension detection
BB(20, 2)     → squeeze detection · mean reversion boundaries · bb_width
ADX(14)       → regime filter (>20 = directional trend)
volume_ma20   → baseline for volume_ratio
volume_ratio  → current_volume / volume_ma20
```

**Regime → permitted patterns:**
```
TRENDING  (ADX>20, EMA spread>threshold):    impulse_retracement · momentum_continuation
VOLATILE  (ATR14>ATR50×1.5, ADX>15):        compression_breakout · anomaly_fade
RANGING   (ADX<20, BB width<threshold):      wick_rejection · anomaly_fade
QUIET     (ATR14<ATR50×0.5, vol_ratio<0.7): ✗ all signals blocked
```

**TP/SL (dynamic · ATR-based · ✗ fixed values):**
```
long:  TP = entry + ATR×tp_atr_multiplier · SL = entry - ATR×sl_atr_multiplier
short: TP = entry - ATR×tp_atr_multiplier · SL = entry + ATR×sl_atr_multiplier
Default: tp_atr_multiplier=1.6 · sl_atr_multiplier=1.0 · min R/R = 1.2 (enforced by risk)
```

**Confidence → position size:**
```
≥ 0.75 → full bucket ($10) · 0.55–0.74 → half bucket ($5) · <0.55 → ✗ no fire
```

**Session thresholds (UTC):**
```
Asian  00–08: volume_ratio_min ×1.2 · min_confidence ×1.1
London 08–16: base params
US     13–21: volume_ratio_min ×0.9
Overlap 13–16: compression_breakout only (highest momentum window)
```

---

## 23. Pattern Specifications

### impulse_retracement
```
Trigger: body_ratio>0.6 · volume_ratio>1.3 · direction matches trend bias
Next:    retracement 30–50% of trigger body · volume LOWER than trigger · ✗ close below trigger open (long)
Entry:   close of retracement candle
Logic:   weak-hand profit taking → dip → continuation
```

### wick_rejection
```
Trigger: lower_wick > 2.0×body_size · close in top 30% of range · within 1 ATR of support
Entry:   close of rejection candle
Logic:   sellers absorbed by buyers · wick = failed attempt
```

### compression_breakout
```
Setup:   ATR(5) < ATR(20)×0.5 · BB width declining 3+ candles · volume declining
Trigger: close outside BB boundary · volume > volume_ma20×1.5
Entry:   close of breakout candle in break direction
Cancel:  if price reverses inside BB next candle → immediate exit
```

### momentum_continuation
```
Setup:   3 consecutive same-direction candles · each body ≥ previous (acceleration) · volume increasing
Trigger: 4th candle is small retracement (body<40% of 3rd) · lower volume than 3rd
Entry:   close of 4th (retracement) candle
```

### anomaly_fade
```
Trigger: volume > volume_ma20 + 2.5×volume_stddev · price move > ATR×2.5 in single candle
Action:  ✗ chase spike direction · wait for reversal candle close · enter AGAINST spike
Logic:   extreme moves attract stop hunts → snapback to mean
```

---

## 24. Risk Manager Rules

**✗ agent modifies `src/risk/manager.py` · human-only · all changes require CLAUDE.md update first.**

```
1. active_positions < max_active_buckets                  → ✗ else reject · reason='bucket_limit'
2. liquidation_distance ≥ 1.5% from entry                → ✗ else reject · reason='liquidation_too_close'
3. TP_dist / SL_dist ≥ 1.2                               → ✗ else reject · reason='rr_below_minimum'
4. expected_gross_profit > round_trip_fee × 1.5           → ✗ else reject · reason='fee_not_viable'
5. session_net_pnl > -5.00 USDT (resets 00:00 UTC)       → ✗ else block all · reason='daily_loss_limit'
6. last_ws_reconnect > 60s ago                            → ✗ else block all · reason='stale_data'
```

---

## 25. Coding Agent Specification

**Scope — AGENT MAY modify:**
```
src/signal/patterns.py      ← pattern detection logic
src/signal/indicators.py    ← indicator computation
src/signal/detector.py      ← voting and consensus
src/signal/regime.py        ← regime classification
src/signal/memory.py        ← pattern memory read/write
params.json                 ← tunable parameters within defined ranges
```

**Scope — ✗ AGENT NEVER modifies:**
```
src/risk/manager.py         ← human-only
src/execution/live.py       ← real order execution
src/execution/interface.py  ← execution contract
src/db/schema.py            ← schema changes require migration
scripts/*.sh                ← deployment scripts
.env                        ← credentials
CLAUDE.md                   ← this document
```

**params.json contract (every value must follow this structure):**
```json
{
  "param_name": {
    "value": 1.3,
    "type": "float",
    "range": [1.0, 3.0],
    "description": "what this controls and why",
    "impact": "higher = fewer signals, higher quality"
  }
}
```
✗ set values outside range · ✗ add params without full contract structure.

**Log-driven tuning workflow:**
```
1. ./scripts/logs.sh --export --last 7d > analysis_input.jsonl
2. paste into chat · ask specific question:
   "Which pattern lowest win rate in Asian session?"
   "Are SLs hitting before TP more than expected?"
   "Is volume filter blocking too many valid signals?"
3. receive specific param change recommendation
4. ./scripts/tune.sh --param <name> --value <new_value>
5. report before/after metrics · human confirms
```

---

## 26. Params Contract Reference

Default values (all in params.json · ranges enforced by install.sh and tune.sh):

```
ema_fast:            9     int    [5, 20]      fast EMA period
ema_slow:            21    int    [15, 50]     slow EMA period
rsi_low:             45    float  [30, 55]     RSI min for long entry filter
rsi_high:            55    float  [45, 70]     RSI max for short entry filter
volume_ratio_min:    1.3   float  [1.1, 2.5]  volume must exceed MA × this
tp_atr_multiplier:   1.6   float  [0.8, 3.0]  TP distance in ATR units
sl_atr_multiplier:   1.0   float  [0.5, 2.0]  SL distance in ATR units
min_confidence:      0.55  float  [0.4, 0.8]  min confidence to fire signal
adx_trend_min:       20    float  [15, 30]     ADX threshold for TRENDING regime
bb_width_threshold:  0.02  float  [0.01, 0.05] BB width threshold for RANGING
max_hold_candles:    4     int    [2, 8]       max candles before timeout exit
max_active_buckets:  1     int    [1, 5]       max concurrent open positions
body_ratio_min:      0.6   float  [0.4, 0.8]  min body/range for impulse pattern
wick_ratio_min:      2.0   float  [1.5, 4.0]  min wick/body for rejection pattern
compression_factor:  0.5   float  [0.3, 0.7]  ATR(5)/ATR(20) threshold for squeeze
```

---

## 27. Notification Specification (Telegram)

Events that trigger Telegram messages (✗ noise · signal only):

```
signal_fired:         pattern · direction · confidence · entry/TP/SL
trade_closed_profit:  exit price · net PnL · close reason · session stats
trade_closed_loss:    exit price · net PnL · close reason · bucket balance
liquidation:          CRITICAL · pair · loss · bucket balance remaining
ws_reconnect:         WARN · exchange · attempt number
regime_change:        new regime · pairs affected
daily_summary:        total trades · win rate · net PnL · bucket states (00:00 UTC)
system_error:         CRITICAL · error message · bot_id · timestamp
```

---

## 28. Visualization — Terminal Dashboard

`rich` library · DB-backed · updates on candle close · ✗ file tail · ✗ browser.

```
┌─────────────────────────────────────────────────────────────────────┐
│  KESTREL  │  dev-BTCUSDT-5m-01  │  2025-04-16 14:32:01 UTC        │
│  Session: dev-colab-004  │  Uptime: 00:47:12  │  Regime: TRENDING  │
├──────────────┬──────────────────────────────────────────────────────┤
│  MARKET      │  BTC/USDT  5m                                        │
│  Price       │  83,421.50  │  EMA9/21: 83,380 / 83,210            │
│  RSI14       │  54.3       │  ATR14: 210.40  │  Vol: 1.62x         │
├──────────────┼──────────────────────────────────────────────────────┤
│  BUCKET 1    │  $10.00  │  No open position                         │
│  SESSION     │  PnL: +$0.797  │  Trades: 1W 0L  │  Win: 100%      │
├──────────────┴──────────────────────────────────────────────────────┤
│  RECENT EVENTS (last 20 from events table)                          │
│  14:32:01  [SYS]  Candle 14:30 closed. C:83421 V:142.3            │
│  14:32:01  [SIG]  impulse_retracement LONG conf:0.78  → FIRE      │
│  14:32:02  [ORD]  BUY 10 USDT @ 83421  TP:83756  SL:83211        │
│  14:45:02  [POS]  TP HIT @ 83758. Net PnL: +$0.797               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 29. Colab Development Protocol

Colab = disposable cloud server · ✗ notebook · every session cold-starts from zero.

**Session sequence (✗ skip steps):**
```
Cell 1: !git clone <repo> && cd kestrel
Cell 2: write .env from Colab secrets
Cell 3: !bash scripts/install.sh          → must print [GO] · abort if [NO-GO]
Cell 4: !bash scripts/start.sh --env dev
Cell 5: !bash scripts/status.sh
Cell 6: !bash scripts/logs.sh --follow
```

**Backtest cell sequence:**
```
Cell A: fetch historical OHLCV via ccxt (free · no auth)
Cell B: indicator unit tests (verify against known values)
Cell C: signal detection tests (patterns on historical data)
Cell D: backtest loop (full simulation · fees + slippage applied)
Cell E: metrics output (win rate · Sharpe · drawdown · fee impact)
Cell F: equity curve (matplotlib · one-time · not realtime)
```

**Simulation realism — all must be modeled (✗ skip any → backtest unreliable):**
```
isolated margin per bucket · liquidation price formula · taker fee both sides ·
slippage 0.05% per side · order rejection scenarios · WS reconnection handling ·
candle close timing accuracy · funding rate if perpetuals (0 for spot margin)
```

---

## 30. Definition of Done

**Per feature:**
```
[ ] implements spec in this document exactly
[ ] corresponding test in /tests
[ ] ✗ TODO or FIXME
[ ] ✗ print() — logging only
[ ] ✗ hardcoded values — .env or params.json
[ ] passes install.sh validation
```

**Per strategy change:**
```
[ ] backtest on ≥90 days of data
[ ] walk-forward validation (train 60% · test 40%)
[ ] fee + slippage model applied
[ ] win rate >55% on out-of-sample data
[ ] R/R ≥ 1.2 on average
[ ] tune.sh reports ACCEPT (no regression vs baseline)
```

**Per deployment:**
```
[ ] all go-live criteria (Section 18) met
[ ] clean Colab cold-start verified within 24h of deployment
[ ] Telegram notifications confirmed end-to-end
[ ] DB backup cron confirmed
[ ] stop.sh graceful close confirmed
[ ] human monitoring plan for first 48h live
```

---

*Kestrel CLAUDE.md v2.0*
*Standards: azzindani/Standards architecture/ + agent/*
*Update when: conventions change · new module added · go-live criteria revised · schema migrated*
