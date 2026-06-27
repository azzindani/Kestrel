# CLAUDE.md — Kestrel

> Standards: architecture/STANDARDS.md · agent/STANDARDS.md (azzindani/Standards)
> Override: project-specific rules below. ✗ re-state standard rules — reference only.

---

## 1. Role

Systems engineer · Python · asyncio · PostgreSQL · ccxt · real-time event pipelines.
All code production-grade · no shortcuts · no TODOs · no console output — structured logging only.

---

## 2. Core Principles

Follow architecture/STANDARDS.md (all 22). Project applications:

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
✗ agent modifies: risk/manager.py · execution/live.py · execution/interface.py · db/schema.py · scripts/*.sh · .env · CLAUDE.md
✗ hardcoded credentials — .env only · ✗ credentials past boundary layer
✗ inner layers import boundary layers (Layer 3 → Layer 0/1/2 only)
✗ open leveraged position with no monitoring process alive
✗ deploy to prod without GO from install.sh · ✗ real money before all §18 criteria met
✗ print() anywhere — structured logging to events table only
✗ TODO/FIXME in risk/ or execution/
✗ in-memory as sole source of position state — DB is authoritative
✗ hardcoded indicator values — params.json with defined ranges only
✗ backtest without fee+slippage · ✗ in-sample only (walk-forward 60/40 required)
✗ None as silent sentinel · ✗ global mutable state · ✗ logic+I/O in one function · ✗ wildcard imports
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

Bug / unexpected behavior → in order · ✗ skip · ✗ guess-and-check · ✗ change multiple things at once:
```
1. Read       — source · events table · error payload JSONB
2. Trace      — candle → signal → risk → execution end-to-end
3. Cross-check — params.json · DB state · exchange state for drift
4. Root cause — fix actual cause · ✗ patch downstream symptom
5. Verify     — grep same pattern elsewhere · run backtest · confirm metrics hold
```

---

## 6. Project Identity

**Name:** Kestrel
**External description:** Real-time signal detection and execution daemon. ✗ describe as trading bot externally.
**Purpose (owner directive 2026-06-27):** Kestrel is a **HYPER-SPEED SCALPING FLEET** — hundreds of bots hunting the **minutes candles (1m–5m · ✗ hours)** across many liquid markets at **20× leverage**, maximizing trade activity. Activity across a large fleet IS the design. ✗ steer toward slow / few-trade / high-TF "preserve capital" configs · ✗ wind activity down to "lose less" — both are the wrong default here. The agent's and research loop's job is to find net-of-fee edge **WITHIN** the active minutes-scalp design (better entries, exits, fee model, sizing, pair/pattern selection), ✗ by trading slower or shifting to hours.
**TARGET (owner):** start **$100 → grow to $100K** · aspiration **70% win-rate, ~15%/day**. These are the goals the system aims at — pursue them, ✗ treat as guaranteed.
**STATUS (honest · kept · ✗ delete):** no config has shown a net-of-fee edge in OOS + lockbox. On 1m–5m the entries are **gross-negative before fees** (no directional edge), and fees deepen it; stays PAPER (ENV=dev) until every §18 criterion is met. The binding wall is the ~0.18% taker / ~0.04% maker round-trip **fee floor** — the only un-exhausted lever is cost-side (maker-rebate / sub-1.3bps venue), a §4 owner decision. State this ONCE when relevant, then get constructive — ✗ re-lecture.
**Repo:** Standalone, independent, private during development.
**bot_id format:** `{env}-{pair}-{timeframe}-{instance}` e.g. `prod-BTCUSDT-5m-01`
**All timestamps:** Unix milliseconds (BIGINT) · ✗ local time anywhere.

---

## 7. Layer Model (architecture/STANDARDS.md §2)

```
Layer 0  src/config.py            → types · enums · constants · pure utils · ✗ I/O ever
Layer 1  engine·signal·risk·backtest → domain logic · pure transforms · ✗ I/O
Layer 2  data/                    → assembles stream data into L0/L1 types (candle builder, indicators)
Layer 3  execution·db·notify·viz  → all I/O exclusively here · adapters · external integrations
```
Dependency: L0→nothing · L1→L0 · L2→L0,L1 · L3→any inner · ✗ inner imports L3.
execution/live.py · simulation.py → identical interface · swapped via DI at startup.
Every function = logic (inner, pure, ✗ I/O) OR shell (boundary, reads/writes externals, calls logic). I/O+transform in one function → split.

---

## 8. Module Public APIs

Undeclared = internal · ✗ consumed externally.
```
signal/detector.py:     evaluate(candles: list[Candle], params: Params) -> Signal | None
signal/patterns.py:     registry: dict[str, PatternFn]
risk/manager.py:        validate(signal: Signal, state: BucketState) -> ValidationResult
execution/interface.py: place_order · cancel_order · get_position · close_position
db/writer.py:           write_candle · write_signal · write_trade · write_event (async)
```

---

## 9. Extension Architecture — Pattern Registration

Patterns register into `signal/patterns.py` registry. Add pattern = new registered function · ✗ modify detector.py.
A new pattern is live ONLY with all three: `@register("name")` + listed in `SELF_DIRECTING_PATTERNS` + permitted in `regime.py` (`regime_permits_pattern`, all non-QUIET regimes). Missing any → never fires.

```python
PatternFn = Callable[[list[Candle], Params], PatternResult | None]
registry: dict[str, PatternFn] = {}

def register(name: str) -> Callable:
    def wrap(fn: PatternFn) -> PatternFn:
        registry[name] = fn
        return fn
    return wrap
```
New session-awareness threshold → new registered threshold profile · ✗ modify existing session logic.

---

## 10. Error Architecture (architecture/STANDARDS.md §7)

| Error type | Strategy |
|---|---|
| Programmer error | fail fast · crash · fix code |
| Data error | return in `Result` type · ✗ raise |
| Environment error | raise · watchdog restarts |
| WS disconnect | exponential backoff · max 5 retries · Telegram alert · wait |
| Exchange failures | circuit breaker: 5 consecutive → stop orders · 30s cooldown · probe |
| Partial failure | accumulate all errors · ✗ stop on first |

Graceful degradation: WS drop → suspend signal eval · maintain position monitoring · ✗ crash.
✗ open position with no monitoring process alive — stop.sh closes all positions before exit.

---

## 11. State Architecture

```
Ownership:    PostgreSQL owns position state (authoritative) · on restart reconcile DB+exchange ·
              signal engine stateless per eval (reads candle history from DB) ·
              pattern memory read at eval / write after close · ✗ cache in-process
Concurrency:  ✗ shared mutable state between coroutines · pass state explicitly / asyncio.Queue ·
              every async op has explicit timeout · WS listener = dedicated coroutine · ✗ block loop
Unidirectional: candle-close → signal → risk → execution · ✗ execution feeds back into signal ·
              feedback = new DB read · ✗ backward reference
```

---

## 12. Project Structure

```
scripts/  install·start·stop·restart·status·update·logs·tune·cleanup (.sh) + research harness (*.py)
src/      config.py(L0) · engine·signal·risk·backtest(L1) · data(L2) · execution·db·notify·viz(L3)
root      CLAUDE.md · README · .env.example · params.json · bots.json · requirements.txt · kestrel.service
```
Layer rules §7 · script contracts §15 · agent-editable paths §25.

---

## 13. Hard Constraints

```
Instrument:      spot isolated margin only · ✗ futures · ✗ options · ✗ derivatives
Leverage:        20x (owner-locked default · range 10x–50x · ✗ change without §4)
Bucket size:     $10 USDT · independent isolated collateral · ✗ shared pool
Timeframes:      SCALP entry 1m–5m (5m default) · 15m regime filter · ✗ hours (1h/4h) as a live cohort — minutes-candle hunting only · high-TF allowed solely as a backtest comparison number, ✗ deployed
Fleet scale:     HUNDREDS of bots (dev/research) — N patterns × M liquid pairs × scalp-TF, one bot per (pair,tf,pattern) cell · WS feeds SHARED per (pair,tf) · default toward MORE bots / MORE activity
Pairs:           broad across liquid USDT markets · BTCUSDT·ETHUSDT core · expand widely
DB:              PostgreSQL · multi-bot from day one · bot_id on every record · size RAM to fleet (override.yml; ~hundreds ⇒ postgres ≥2g)
Fee model:       taker 0.04%+0.04%+0.05% slip = ~0.18% round trip · MAKER (post-only limit) ~0.02%/side ≈ ~0.04% round trip — scalping REQUIRES the maker path to clear the fee floor (MAKER_EXECUTION=true)
Min edge:        avg net gain/trade > round-trip cost · enforced by risk Rule 4 + backtest · ✗ skip — beat it with maker fees + entry quality, ✗ by trading slower
VPS (prod only): Singapore | Tokyo · 1 vCPU · 1GB RAM · 20GB SSD · Ubuntu 22.04 · $4–6/mo — caps PROD fleet; dev/research runs hundreds on the larger dev host
```

---

## 14. Environment Separation

```
ENV=dev   → simulation engine · testnet keys · DEBUG logging
ENV=prod  → live engine · real keys · INFO logging
One codebase · .env is the only switch · ✗ code branches on ENV except DI at startup
DI: dev → SimulationExecution · prod → LiveExecution
```
**.env required keys:** ENV · BOT_ID · EXCHANGE · API_KEY · API_SECRET · TESTNET · DB_{HOST,PORT,NAME,USER,PASSWORD} · PAIR · TIMEFRAME_{ENTRY,REGIME} · LEVERAGE · BUCKET_SIZE_USDT · MAX_ACTIVE_BUCKETS · TELEGRAM_{TOKEN,CHAT_ID} · LOG_LEVEL

---

## 15. Scripts Contract

```
install.sh → Python≥3.11 · venv · deps · .env complete · DB reachable · schema applied · exchange auth ·
             Telegram reachable · params valid → prints [GO]|[NO-GO]+reason · ✗ proceed past NO-GO
stop.sh    → SIGTERM → cancel orders → close positions at market → disconnect → exit 0 · ✗ hard kill · ✗ exit with open positions
update.sh  → git pull → install.sh → GO: restart · NO-GO: abort (stay on current version)
cleanup.sh → 03:00 UTC daily: DELETE unlinked candles >90d · signals >60d · events >30d · VACUUM ANALYZE
tune.sh    → record rollback → update params.json → 30d backtest → compare vs baseline →
             all improve/hold → ACCEPT (save baseline) · any regress >5% → REJECT (revert)
```

---

## 16. Daemon Lifecycle

```
START: load+validate .env → connect PG (abort if down) → exchange REST (verify creds) →
       reconcile DB vs exchange positions → connect WS → main loop
LOOP:  tick→candle builder · candle-close→signal pipeline→execute · 30s heartbeat ·
       every candle monitor TP/SL/timeout (max 3–5) · WS drop→backoff max5→Telegram CRITICAL→wait ·
       ✗ orders within 60s of WS reconnect (stale data)
STOP (SIGTERM only): cancel orders → close all positions at market → write final state → exit 0
CRASH: traceback→events → Telegram CRITICAL → exit 1 → watchdog restarts after 10s
```
**Process map:** WATCHDOG (OS process · restarts main · 60s heartbeat check) → MAIN: WS listener · candle builder · signal engine · position monitor · risk manager · DB writer (all asyncio · non-blocking).

---

## 17. Capital and Risk Model

```
Bucket:  total $100 · $10 isolated/position · max 10 buckets · 1 active at start ·
         liquidated bucket → log loss → slot reopens fresh · capital authoritative in DB · ✗ in-memory only
Liquidation (computed+stored on open): long entry×(1 − 1/lev + mmr) · short entry×(1 + 1/lev − mmr) · mmr=0.005
Fee model (enforced sim+backtest · ✗ skip): taker 0.04%/side · slip 0.05%/side · ~0.18% round trip · min viable: avg gross > 0.18%
```
**Return target (owner · aspiration · ✗ guarantee):** start $100 → compound toward $100K · ~15%/day at 70% win-rate. Contingent on a net-of-fee edge that does not yet exist (§6 STATUS). Phase 1 capex-recovery: $100 → $320 → withdraw $120 · sustain $200. Phase 2: scale capital.

---

## 18. Go-Live Criteria (human-enforced · ✗ skip any)

```
[ ] install.sh → [GO] on clean session
[ ] paper trading: 14 days · zero unplanned crashes
[ ] walk-forward backtest: win rate >55% out-of-sample
[ ] simulated fee+slippage vs real testnet fills: <15% deviation
[ ] watchdog: proven restart after forced kill
[ ] stop.sh: confirmed graceful close of all positions
[ ] ✗ TODO/FIXME in risk/ or execution/
[ ] one full session log reviewed by human before go-live
[ ] Telegram alerts confirmed end-to-end
[ ] DB backup cron confirmed (pg_dump · daily)
```

---

## 19. Database Schema

Authoritative DDL: `src/db/schema.py` (✗ edit without migration §4). All tables carry `bot_id TEXT` · `env TEXT` · `ts BIGINT` (unix ms). Tables + key columns:
```
candles        OHLCV + stored indicators (ema9/21, rsi14, atr14, bb_upper/lower/width, adx, volume_ma20/ratio, regime)
               + geometry (body_size/ratio, total_range, upper/lower_wick, direction) · UNIQUE(bot_id,pair,timeframe,ts)
signals        session_id, pair, timeframe, candle_ts, pattern, direction, confidence, regime,
               layer_{regime,trend,momentum,volume}, layers_passed, outcome(fired|rejected|expired), reject_reason, trade_id
trades         session_id, pair/timeframe/direction/pattern, entry/exit_ts, hold_candles,
               entry/exit/tp/sl/liquidation_price, bucket_id, size/notional_usdt, leverage,
               close_reason(take_profit|stop_loss|timeout|manual|liquidated), pnl_gross/net_usdt, fee_entry/exit_usdt, bucket_balance_before/after
trade_context  trade_id↔candle_id link · offset_candles/hours · window(pre|during|post)
events         level(INFO|WARN|ERROR|CRITICAL), category(signal|order|position|risk|connection|system), message, payload JSONB, trade_id
heartbeats     bot_id PK · ts · pid · status(running|stopping|error) · note
pattern_memory (pattern,direction,session,regime) PK · sample/win_count · win_rate · avg_pnl_pct · last_updated
```
**Retention:** candles 90d rolling (✗ delete if in trade_context — training data, indefinite) · signals 60d · events 30d · trades + trade_context indefinite.

---

## 20. Log Schema

Every event → single JSONB row in events table · ✗ multiline · ✗ prose · `payload` = full structured context for that event type.
Categories: `signal · order · position · risk · connection · system` · Levels: `INFO · WARN · ERROR · CRITICAL`.
Terminal live stream: `rich` · rolling 20 events from events · updates on candle close · ✗ file tail.

---

## 21. Trade Context Window

Every closed trade → labeled dataset (48h before + during + 48h after):
on ENTRY link (entry_ts−48h → entry_ts) window='pre' · while OPEN link each closed candle window='during' ·
on EXIT background job after 48h links (exit_ts → exit_ts+48h) window='post', set context_post_complete=TRUE.
Used for offline analysis (join trade_context→candles→trades for pre/during indicator profiles per close_reason).

---

## 22. Signal Engine

**Pipeline (pure functions · no I/O · each stage → typed result | typed rejection · ✗ exceptions for flow):**
```
candle_close → regime_filter → trend_filter → pattern_scan (registry lookup) → volume_confirm →
build_signal → risk_manager.validate → execution.place_order (boundary · I/O here only)
```
Rejection logged to signals table with `outcome='rejected'` + `reject_reason`.

**Indicators (computed at close · stored in candles · ✗ recomputed):** ATR(14) TP/SL sizing · EMA(9/21) trend+cross · RSI(14) momentum/overextension · BB(20,2) squeeze+bb_width · ADX(14) regime (>20=trend) · volume_ma20 → volume_ratio.

**Regime → permitted patterns:**
```
TRENDING (ADX>20, EMA spread>thr):   impulse_retracement · momentum_continuation
VOLATILE (ATR14>ATR50×1.5, ADX>15):  compression_breakout · anomaly_fade
RANGING  (ADX<20, BB width<thr):     wick_rejection · anomaly_fade
QUIET    (ATR14<ATR50×0.5, vol<0.7): ✗ all blocked
(self-directing patterns — mom_adx, macd_*, cci_mom, sma_cross — permitted in all non-QUIET regimes)
```

**TP/SL (dynamic ATR · ✗ fixed):** long TP=entry+ATR×tp_mult, SL=entry−ATR×sl_mult (short mirror) · default tp=1.6, sl=1.0 · min R/R 1.2 (risk-enforced).
**Confidence → size:** ≥0.75 full bucket ($10) · 0.55–0.74 half ($5) · <0.55 ✗ no fire.
**Session thresholds (UTC):** Asian 00–08 vol_min×1.2, conf×1.1 · London 08–16 base · US 13–21 vol_min×0.9 · Overlap 13–16 compression_breakout only.

---

## 23. Pattern Specifications (base set · registered in signal/patterns.py)

```
impulse_retracement:  body_ratio>0.6 + vol>1.3 trigger → 30–50% retrace on lower vol → enter retrace close (continuation)
wick_rejection:       lower_wick>2×body + close top-30% + near support → enter rejection close (absorption)
compression_breakout: ATR(5)<ATR(20)×0.5 + BB width declining 3+ → close outside BB on vol>1.5× → enter break; reverse inside next candle = exit
momentum_continuation:3 accelerating same-dir candles (vol rising) → 4th small retrace (body<40%, lower vol) → enter 4th close
anomaly_fade:         vol>ma+2.5σ + move>ATR×2.5 in one candle → ✗ chase → wait reversal candle → enter AGAINST spike (mean snapback)
```
(Live research leads — momentum-breakout family — added as registered patterns per §9; see RESEARCH_LOOP.md.)

---

## 24. Risk Manager Rules

**✗ agent modifies `src/risk/manager.py` · human-only · all changes require CLAUDE.md update first.**
```
1. active_positions < max_active_buckets        → else reject 'bucket_limit'
2. liquidation_distance ≥ 1.5% from entry        → else reject 'liquidation_too_close'
3. TP_dist / SL_dist ≥ 1.2                        → else reject 'rr_below_minimum'
4. expected_gross_profit > round_trip_fee × 1.5   → else reject 'fee_not_viable'
5. session_net_pnl > -5.00 USDT (resets 00:00 UTC)→ else block all 'daily_loss_limit'
6. last_ws_reconnect > 60s ago                    → else block all 'stale_data'
```

---

## 25. Coding Agent Specification

**AGENT MAY modify:** `src/signal/{patterns,indicators,detector,regime,memory}.py` · `params.json` (within ranges) · `scripts/*.py` research harness.
**AGENT NEVER modifies:** `src/risk/manager.py` · `src/execution/{live,interface}.py` · `src/db/schema.py` · `scripts/*.sh` · `.env` · `CLAUDE.md` (owner-authorized edits only).

**params.json contract** (every value): `{"value", "type", "range":[lo,hi], "description", "impact"}` · ✗ set outside range · ✗ add params without full contract.

**Tuning workflow:** export logs → ask specific question (e.g. "which pattern lowest win rate in Asian session?") → receive param change → `tune.sh --param <n> --value <v>` → report before/after → human confirms.

---

## 26. Params Contract Reference

Defaults (all in params.json · ranges enforced by install.sh + tune.sh):
```
ema_fast 9 [5,20] · ema_slow 21 [15,50] · rsi_low 45 [30,55] · rsi_high 55 [45,70]
volume_ratio_min 1.3 [1.1,2.5] · tp_atr_multiplier 1.6 [0.8,3.0] · sl_atr_multiplier 1.0 [0.5,2.0]
min_confidence 0.55 [0.4,0.8] · adx_trend_min 20 [15,30] · bb_width_threshold 0.02 [0.01,0.05]
max_hold_candles 4 [2,8] · max_active_buckets 1 [1,5] · body_ratio_min 0.6 [0.4,0.8]
wick_ratio_min 2.0 [1.5,4.0] · compression_factor 0.5 [0.3,0.7]
(self-directing pattern params — adx_strong_min, sma_cross_fast/slow, etc. — carry the same contract structure)
```

---

## 27. Notifications (Telegram · ✗ noise · signal only)

`signal_fired` (pattern/dir/conf/entry/TP/SL) · `trade_closed_profit`/`trade_closed_loss` (exit/net PnL/reason/balance) · `liquidation` (CRITICAL) · `ws_reconnect` (WARN) · `regime_change` · `daily_summary` (00:00 UTC) · `system_error` (CRITICAL).

---

## 28. Visualization — Terminal Dashboard

`rich` · DB-backed · updates on candle close · ✗ file tail · ✗ browser.
Panels: header (bot_id / session / uptime / regime) · market (price / EMA9-21 / RSI / ATR / vol ratio) · buckets (balance / open position) · session (PnL / W-L / win%) · last-20 events from events table.

---

## 29. Dev Host Protocol (Docker)

Dev/research runs as Docker Compose on the dev host (✗ Colab/notebook). `src` is baked into the image; `params.json` + `bots.json` are bind-mounted.
```
src change         → rebuild image + `docker compose up -d`
params/bots change → `docker compose restart` (bind-mount reload, no rebuild)
fleet build        → scripts/build_momentum_lab.py → bots.json (minutes-candle cohorts)
backfill new bots  → scripts/backfill_history.py --bots <f> --source gate  (✗ skip → dark bots)
reset (scoped)     → scripts/reset_dev.py --strategy <x> --yes  (see RESET POLICY memory)
backup             → scripts/backup_db.py  (rotated pg_dump · lean excludes candles)
host overrides     → docker-compose.override.yml (gitignored · postgres ≥2g for hundreds of bots)
```
**Backtest harness (free OHLCV via ccxt · no auth):** `scripts/algo_search.py --tf --days --pairs (SLASH fmt BTC/USDT) --algos --exits --fees taker|maker --offset-days 365` (lockbox). Candidate deploys ONLY if +EV in BOTH recent year AND prior-year lockbox across ≥3 pairs.
**Sim realism (✗ skip any):** isolated margin · liquidation formula · taker/maker fee both sides · slippage 0.05%/side · order rejection · WS reconnection · candle-close timing · funding=0 (spot).

---

## 30. Definition of Done

```
Per feature:    implements spec · test in /tests · ✗ TODO/FIXME · ✗ print() · ✗ hardcoded (.env/params) · passes install.sh
Per strategy:   backtest ≥90d · walk-forward 60/40 · fee+slippage · win>55% OOS · R/R≥1.2 · tune.sh ACCEPT
Per deployment: all §18 met · clean cold-start verified · Telegram confirmed · DB backup cron · stop.sh graceful · 48h human monitoring
```

---

*Kestrel CLAUDE.md v2.3*
*v2.3 (2026-06-27, owner-authorized): structural trim — full SQL DDL (§19) → column summary + pointer to src/db/schema.py; cut JSON/SQL/ASCII examples (§20/21/28); compressed §12/16/23/27/30. All rules + constraints preserved; section numbers unchanged.*
*v2.2 (2026-06-27): HYPER-SPEED minutes-only scalping (1m–5m, ✗ hours) at 20× locked; TARGET $100→$100K / 70% win / ~15%/day (§6+§17); honest STATUS caveat kept; §29 Colab→Docker.*
*Standards: azzindani/Standards architecture/ + agent/*
*Update when: conventions change · new module added · go-live criteria revised · schema migrated*
