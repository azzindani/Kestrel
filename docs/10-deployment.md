# 10 · Deployment

Kestrel deploys as Docker Compose stacks. This document covers the container topology, the
three-phase promotion model, the `bots.json` fleet format, the operational scripts, and the
reset protocol.

## 1. Three-phase deploy topology

```
Phase 1 — LABS         Phase 2 — STAGING            Phase 3 — PROD
ENV=dev                ENV=staging                  ENV=prod
SimulationExecution    LiveExecution (TESTNET=true) LiveExecution (real keys)
no venue contact       BingX VST demo (virtual $)   real capital
many experimental bots curated winners only         curated, §18-gated
compose (no profile)   compose --profile staging    (not provisioned)
```

- **Phase 1 (labs)** — where everything currently lives. `dev` env, simulation execution, no
  exchange contact, large experimental fleets.
- **Phase 2 (staging)** — a quarantine that runs the **real `LiveExecution` code path** against
  the **BingX VST demo** venue (virtual money), with `TESTNET=true` as a safety rail. It
  shares the database with the labs (same `kestrel_net`) and runs as the **`staging` profile**
  of the one `kestrel` compose project (`docker compose --profile staging up -d staging`), scoped
  to `bot_id LIKE 'staging-%'`. *Dormant*
  until VST keys are added. **Caveat:** the BingX demo is **futures**, while `CLAUDE.md` §13
  specifies **spot** — reconciling that is a human-only CLAUDE.md amendment required before
  go-live.
- **Phase 3 (prod)** — real money. Not provisioned, and gated by the §18 criteria (which
  require a validated edge — see [Go-Live](12-go-live.md)).

## 2. Container topology (`docker-compose.yml` — Phase 1)

| Service | Image | Role | Limits |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | the single source of truth; `pgdata` volume; `pg_isready` healthcheck | 256m / 0.6 cpu |
| `kestrel` | `kestrel-kestrel:latest` (built) | the daemon + watchdog (src **baked into the image**) | 320m / 0.6 cpu (raised in override) |
| `grafana` | `grafana/grafana:11.3.0` | dashboards at `https://kestrel.casava.space` | 256m / 0.4 cpu |
| `pg-backup` | `postgres:16-alpine` | `pg_dump` daily at 03:00 UTC, 7-day rotation | 64m / 0.1 cpu |

**Delivery model (important operational distinction):**

- **`src/` is baked into the image** → changing Python code requires a **rebuild**:
  `docker compose up -d --build`. A plain `restart` runs stale code.
- **`params.json` and `bots.json` are bind-mounted read-only** → editing them is picked up on
  a **restart** (no rebuild needed).

The `kestrel` healthcheck queries the `heartbeats` table for a beat newer than 90 s — a stalled
daemon flips the container unhealthy.

### `docker-compose.override.yml` (gitignored, host-local)

Host-specific overrides for the dev VPS, **not** committed. Current settings:

```yaml
EXCHANGE: gate            # data feed reachable from this VPS (per the VPS geo-restriction memo)
FEED_MODE: ws             # websocket feed
MAKER_EXECUTION: "true"   # simulator uses the post-only limit fill model
PORTFOLIO_TP_PCT: "0.10"  # portfolio guard: profit-lock at +10% aggregate
PORTFOLIO_DD_PCT: "0.10"  # portfolio guard: drawdown-stop at -10% aggregate
# mem_limit 1g · cpus 2.0  (raised above the §13 prod-VPS ceiling for the 120-bot lab)
```

> **Exchange choice:** `gate` is used because, from the dev VPS's network, live ccxt.pro
> websockets work on gate/bingx/kraken/huobi while binance/okx/bybit/bitget are blocked. The
> *broker* decision is BingX; `gate` is just the reachable **data feed** for paper research.

## 3. The fleet — `bots.json`

`bots.json` is the fleet manifest: a JSON array, one object per bot. `load_bot_configs()` reads
it and produces one `AppConfig` per entry (falling back to a single bot from `.env` if the file
is absent). All bots run in **one process, one event loop**, sharing the DB pool, the notifier,
the watchdog, and the portfolio guard.

```json
{
  "bot_id": "dev-BTCUSDT-4h-mom_adx-01",
  "pair": "BTC/USDT",
  "timeframe_entry": "4h",
  "timeframe_regime": "4h",
  "max_active_buckets": 1,
  "strategy": "mom_adx",
  "patterns": ["mom_adx"],
  "params": {
    "tp_atr_multiplier": 2.4, "sl_atr_multiplier": 1.5, "max_hold_candles": 6,
    "trailing_enabled": true, "trail_activation_r": 0.5, "trail_distance_r": 0.5,
    "volume_ratio_min": 1.1, "adx_strong_min": 25.0, "max_loss_pct_per_trade": 0.01
  }
}
```

Per-bot overridable fields: `bot_id`, `pair`, `timeframe_entry`, `timeframe_regime`,
`max_active_buckets`, `strategy` (a reporting label — the dashboard leaderboard keys on the
4th `-`-segment of `bot_id`), `patterns` (the enabled-pattern allowlist; omit = all), and
`params` (overrides merged onto the base `params.json`).

**Current dev fleet (~161 bots, evolves with the research loop):** a 136-bot baseline
(the four 1h indicator leads `macd_cross`/`macd_rsi`/`cci_mom`/`sma_cross` × 34 liquid
USDT pairs) plus the rotating experimental `exp_*` cohort (currently `exp_robustwide`
wide-exit sma_cross ×14 pairs + `exp_ensemble` ensemble_3of4 ×11 pairs). The cohort is
defined in `exp_candidate.json` and rebuilt by `build_exp_cohort.py`; a `bot_registry/`
dedup guard fingerprints every config ever deployed so refuted setups aren't silently
re-run. Small `staging` (curated best performers) and `lab` (owner sandbox) tiers run
alongside.

### Lab generators

| Script | Fleet it writes |
|---|---|
| `build_momentum_lab.py` | the baseline fleet (the 1h indicator leads × 34 liquid pairs) |
| `build_exp_cohort.py` | merges the `exp_candidate.json` cohort into `bots.json`, preserving the baseline verbatim |
| `build_wave_lab.py` | 60 bots — wave family × fixed/trailing × 10 pairs |
| `build_crypto_lab.py` | 120 bots — 3 entries × 4 exit modes × 10 symbols |
| `build_bakeoff.py` | 48 bots — 8 strategy variants × 6 pairs |
| `promote_to_staging.py` | reads `trades` (env=dev), selects cells that are BOTH win ≥ 50% AND net-positive (n ≥ 10), ranks by expectancy, writes the curated `bots.staging.json` (`staging-` prefix). Falls back to the lockbox-lead seed if no cell qualifies. |

## 4. Operational scripts (`scripts/*.sh` — FROZEN)

Shell scripts are **frozen** (human-only, `CLAUDE.md` §3). They form the lifecycle contract:

| Script | Contract |
|---|---|
| `install.sh` | Full setup from zero; validates Python ≥3.11, venv, deps, complete `.env`, DB reachable, schema applied, exchange auth, Telegram reachable, params valid → prints **`[GO]`** or **`[NO-GO]` + reason**. Never proceed past NO-GO. |
| `start.sh` | Start daemon + watchdog. |
| `stop.sh` | SIGTERM → cancel orders → **close all positions at market** → disconnect → exit 0. Never hard-kill, never exit with open positions. |
| `restart.sh` | stop → wait → start. |
| `status.sh` | Health check from the `heartbeats` table. |
| `update.sh` | `git pull` → `install.sh` → GO: restart; NO-GO: abort (stay on current version). |
| `logs.sh` | Tail/export `events` with filters (`--follow`, `--export`, `--last Nd`). |
| `cleanup.sh` | 03:00 UTC daily: delete expired rows per the retention policy, then `VACUUM ANALYZE`. |
| `tune.sh` | record rollback → update `params.json` → 30d backtest → compare → **ACCEPT** (save baseline) or **REVERT** (any metric regresses > 5%). |

> In containerised dev, the equivalent operations are run via `docker compose` (e.g. the
> daemon is the container entrypoint; resets and backfills run as `docker compose exec`).

## 5. The reset protocol

After deploying a **new algorithm**, always reset to get a clean evaluation slate (a standing
user preference). The protocol — **keep candles, wipe everything else for `env='dev'`**:

```
1. stop the daemon
2. reset_dev.py --yes      # wipe dev trades, signals, events, trade_context, pattern_memory
3. wipe dev heartbeats
4. backfill_history.py     # re-warm candle history (groups by (pair, tf); gate source)
5. start (up) the daemon
```

`reset_dev.py` only touches `env='dev'` rows and **never deletes candles** (they are training
data and expensive to refetch). `backfill_history.py` warms each `(pair, timeframe)` so the
daemon's 120-candle buffer is full at startup — high timeframes need more history
(4h needs ~120 days for ATR(50)).

## 6. Standard dev session (containerised)

```
docker compose up -d --build          # rebuild image (src baked) + start
docker compose exec kestrel python3 scripts/reset_dev.py --yes
docker compose exec kestrel python3 scripts/backfill_history.py --source gate
docker compose restart kestrel        # reload bind-mounted bots.json / params.json
docker compose logs -f kestrel        # or watch Grafana / the events table
```

For a code change: rebuild (`up -d --build`). For a config change (`bots.json`/`params.json`):
just `restart`. CI must be green before deploy, and commits go **directly to `main`** (never a
branch) per the project's workflow.
