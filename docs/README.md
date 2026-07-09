# Kestrel — Documentation

> **Kestrel** is a real-time signal-detection and execution daemon for crypto markets.
> Python · asyncio · PostgreSQL · ccxt. Currently in a **paper-trading / research phase** —
> see [the honest status note](01-overview.md#5-honest-status--no-proven-edge) before
> reading anything as a money-making system.

This `docs/` tree is the **verbose engineering reference** for Kestrel. It explains *what
the code actually does* — function by function, constant by constant — and *why the
project is where it is*. It complements two other documents at the repo root:

| Document | Role |
|---|---|
| **`CLAUDE.md`** | The **specification & contract** — the rules, the frozen files, the schema, the go-live gates. Authoritative for *what is allowed*. |
| **`FINDINGS.md`** | The **research verdict** — the empirical "is there an edge?" report. Authoritative for *what is true about profitability*. |
| **`docs/`** (here) | The **implementation reference** — how each subsystem is built, with file:line precision. Authoritative for *how it works*. |

If `docs/` and `CLAUDE.md` ever disagree, **`CLAUDE.md` wins** — it is the contract, and
several of the files it governs are frozen (human-only). This documentation was written at
commit `f0be6b6` (2026-06-16) and **refreshed 2026-07-09** to reflect the research loop's
findings (iterations 1–54), the 1h indicator-lead patterns, and the adoption of the
**[Points Framework](13-points-framework.md)** as the project's primary scoreboard.

---

## How to read this

Read top-to-bottom for a full understanding, or jump to the subsystem you care about.

| # | Document | What it covers |
|---|---|---|
| 01 | [Overview](01-overview.md) | What Kestrel is, project identity, the capital model in one page, and the **honest no-edge status**. |
| 02 | [Architecture](02-architecture.md) | The 4-layer model, dependency rules, function classification, directory map. |
| 03 | [Data Pipeline](03-data-pipeline.md) | Market feed (WebSocket & REST-polling), candle builder, the indicator suite. |
| 04 | [Signal Engine](04-signal-engine.md) | The five-stage evaluation pipeline: regime → trend → pattern → volume → build. Confidence, TP/SL, the liquidation clamp. |
| 05 | [Strategies & Patterns](05-strategies.md) | All eleven registered patterns, the confluence-momentum family, the regime→pattern map. |
| 06 | [Risk & Capital](06-risk-and-capital.md) | The six risk rules, the bucket model, equity-scaled sizing, liquidation math, and the portfolio guard ("manager bot"). |
| 07 | [Execution](07-execution.md) | The execution contract, simulation vs live, the maker/taker fee model, trailing-close, provider DI. |
| 08 | [Database](08-database.md) | The full schema, retention policy, the trade-context labelling system, the writer API. |
| 09 | [Backtesting & Research](09-backtesting-research.md) | The research harness scripts, walk-forward methodology, the lockbox, and what every experiment found. |
| 10 | [Deployment](10-deployment.md) | Docker topology, the three-phase deploy model, `bots.json`, the operational scripts, the reset protocol. |
| 11 | [Operations](11-operations.md) | Daemon lifecycle, watchdog, scheduler, Telegram alerts, the terminal dashboard, Grafana. |
| 12 | [Go-Live & Definition of Done](12-go-live.md) | The go-live criteria, per-feature/strategy/deploy DoD, and what would actually change the verdict. |
| 13 | [**Points Framework**](13-points-framework.md) | **The primary scoreboard since 2026-07-09**: points (bps) + win-rate measurement, the win-rate geometry law, the HiWin strategy program, daily & program targets, scalability model. |

---

## The two-minute summary

- **What it does:** subscribes to live crypto candles, runs each *closed* candle through a
  pure-functional signal pipeline, validates any signal against six hard risk rules, and
  (in `dev`) simulates the order or (in `prod`) places a real one. Every candle, signal,
  trade, and event is persisted to PostgreSQL. Multiple bots run in **one asyncio event
  loop** in one process.
- **Where it is:** a **~161-bot dev paper fleet** (34 liquid pairs × the validated 1h
  indicator leads + experimental cohorts), plus small staging and lab tiers, on a Gate.io
  data feed, simulation execution, real fees modelled. Maintained by an autonomous daily
  **research loop** (`RESEARCH_LOOP.md`, 54 iterations and counting). No real capital, no
  live keys.
- **The honest verdict:** after 54 research iterations, **five 1h signals are +EV in both
  a recent-year walk-forward AND an untouched prior-year lockbox** (`macd_cross`,
  `macd_rsi`, `cci_mom`, `sma_cross`, `ensemble_3of4`) — real, but marginal: their gross
  edge (~1–15 bps/trade) sits at or below the fee floor, and none clears the formal
  deflated-Sharpe bar. *No confirmed net-of-fee edge exists yet.* Since 2026-07-09 the
  project scores itself in **points + win rate** (the [Points Framework](13-points-framework.md))
  to separate signal quality from fee economics. See
  [Overview §5](01-overview.md#5-honest-status--no-proven-edge) and
  [Backtesting & Research](09-backtesting-research.md).

---

## Conventions used throughout

- **Timestamps** are always **Unix milliseconds (`BIGINT`)** — never local time, anywhere.
- **`§N`** references a section of `CLAUDE.md` (e.g. `§24` = the risk rules).
- **"Frozen"** marks a file the coding agent must never modify (human-only): `risk/manager.py`,
  `execution/live.py`, `execution/interface.py`, `db/schema.py`, `scripts/*.sh`, `.env`,
  `CLAUDE.md`. See [Architecture §6](02-architecture.md#6-frozen-vs-agent-editable-files).
- **Money** is USDT throughout; the per-position unit is a **$10 "bucket"**.
- File references look like `src/signal/detector.py:226` and point at the live code.
