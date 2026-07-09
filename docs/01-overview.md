# 01 · Overview

## 1. What Kestrel is

Kestrel is a **real-time signal-detection and execution daemon**. It connects to a crypto
exchange's market data, watches candles form, and on each *candle close* runs a
deterministic pipeline that decides whether a tradeable pattern is present. If one is, and
it survives a battery of risk checks, Kestrel either **simulates** the trade (research /
paper mode) or **executes** it on the exchange (live mode). Everything — every candle,
signal, rejection, trade, and system event — is written to PostgreSQL so that behaviour is
fully reconstructable after the fact.

> **External description (per `CLAUDE.md` §6):** Kestrel is described as a *"real-time
> signal detection and execution daemon"*, **not** as a "trading bot." This is a deliberate
> framing choice and is preserved throughout the docs.

It is built to run **many bots at once** — different pairs, timeframes, and strategies —
inside a single process and a single asyncio event loop, all sharing one database, one
notifier, and one supervising watchdog. This makes it a *fleet* platform: the current dev
fleet runs **~161 experimental configurations side-by-side** (34 liquid pairs × the
validated 1h leads + rotating `exp_*` cohorts) on identical infrastructure, with separate
staging and lab tiers.

## 2. Project identity

| Field | Value |
|---|---|
| **Name** | Kestrel |
| **Repo** | Standalone, private during development |
| **`bot_id` format** | `{env}-{pair}-{timeframe}-{instance}` — e.g. `prod-BTCUSDT-5m-01`. Lab bots add a strategy segment: `dev-BTCUSDT-5m-mom_adx-01`. |
| **Timestamps** | Unix milliseconds (`BIGINT`) everywhere — never local time |
| **Broker (selected)** | **BingX** (verified account; wired as a generic ccxt venue). Data feed currently **Gate.io** (`EXCHANGE=gate`) because it is reachable from the dev VPS. |
| **Instrument scope** | Spot isolated margin (`CLAUDE.md` §13) — no futures/options/derivatives in the spec, though the demo-staging venue is futures (a documented caveat). |

## 3. The capital & risk model in one page

Kestrel does not bet one big pot. It uses an **isolated bucket** model:

- Total simulated capital: **$100 USDT**.
- Per-position unit: a **$10 bucket** with its own isolated collateral. A bucket that
  liquidates logs its loss and frees its slot for a fresh bucket; buckets never share a pool.
- **Leverage:** 10×–50× (the lab runs 20×).
- **Position sizing is equity-scaled** — size = current bucket equity × a confidence-driven
  fraction — so the system compounds as a bucket grows and de-risks as it bleeds (see
  [Risk & Capital](06-risk-and-capital.md)).
- **Every trade pays modelled costs:** taker 0.04%/side + 0.05%/side slippage ≈ **0.18%
  round-trip** (taker), or as little as ~0.04% round-trip when the **maker** model is on.
  No backtest or simulation is ever run without these costs applied (`CLAUDE.md` §13/§29).
- **Minimum viable edge:** a trade's expected gross gain must exceed the round-trip cost —
  enforced as a hard risk rule, never skipped.

This bucket-and-cost discipline is the spine of the whole project: it is *why* the research
keeps concluding "no edge" honestly rather than reporting illusory profits that vanish once
fees are charged.

## 4. The end-to-end flow (unidirectional)

```
   exchange WS/REST          candle builder            signal engine (pure)
 ─────────────────────  ►  ──────────────────  ►  ───────────────────────────────
   tick / closed bar       OHLCV + indicators      regime → trend → pattern →
                           emitted on close          volume → build_signal
                                                            │
                                                            ▼
                                              risk.validate() — six hard rules
                                                            │  (pass)
                                                            ▼
                                       execution.place_order()  ← I/O boundary
                                       (SimulationExecution | LiveExecution)
                                                            │
                                                            ▼
                                              PostgreSQL: candles · signals ·
                                              trades · trade_context · events
```

Data flows **one way**: candle → signal → risk → execution → DB. The execution layer never
feeds back into the signal layer; any "feedback" (e.g. a position outcome influencing the
next signal) happens only via a fresh DB read on the next candle. This is enforced by the
[layer model](02-architecture.md).

## 5. Honest status — real leads, no confirmed net-of-fee edge

**This is the most important section in the docs. Read it before treating Kestrel as
anything other than a research platform.**

Kestrel is engineered to production standards. After **54 autonomous research-loop
iterations** (documented in `RESEARCH_LOOP.md` and
[Backtesting & Research](09-backtesting-research.md)), the picture is:

1. **Single-rule OHLCV entries lose at every timeframe and asset** once realistic fees and
   slippage are charged. At 5m, the average price move is *smaller than the round-trip cost*
   — the game is negative-sum before a strategy even starts. The 5m search is exhausted.
2. **Five 1h signals are +EV in BOTH a recent-year walk-forward AND an untouched
   prior-year lockbox** — `macd_cross`, `macd_rsi`, `cci_mom`, `sma_cross`, and the
   voting-confluence `ensemble_3of4`. These are the project's first (and only)
   cross-era-robust results, deployed as live paper forward-tests. They are **real but
   marginal**: gross directional capture ~1–15 bps/trade, at or below the ~4 bps maker
   fee floor, and none clears the formal deflated-Sharpe multiple-testing bar (iter 47).
   Dozens of other candidates looked good in the recent year and **collapsed in the
   lockbox** — the recurring data-mining signature the methodology exists to catch.
3. **The maker-fee lever is real and large** (it cuts the cost wall ~4×), but it only
   *amplifies* whatever edge exists — and the measured edge is small, so the fee floor
   remains the binding wall. The un-exhausted levers are **cost-side** (venue/fee tier,
   §4 owner decisions) and **structural** (funding-rate harvesting — needs perps, §4).
4. **Since 2026-07-09 the primary scoreboard is the [Points Framework](13-points-framework.md)**:
   signal quality is measured in **points (bps of price) + win rate**, gross of fees,
   with the dollar/fee bridge as an explicit later phase. This separates "is the signal
   real?" (points) from "do fees eat it?" (economics) — the two questions a dollar
   scoreboard entangles.

**Therefore:**

- Kestrel runs as a **paper / research / forward-testing platform** on a live data feed.
- **No real capital. No live API keys. No production VPS.** Going live is gated by the §18
  criteria, which are *not* met (chief among them: a validated net-of-fee edge).
- The risk overlays (trailing-close, the portfolio guard) are **variance shapers, not
  edge creators** — they reshape the distribution of outcomes; they do not make a losing
  system profitable.

This honesty is a feature, not a bug. The cost model, walk-forward + lockbox discipline,
and the deflated-Sharpe bar are specifically designed to *prevent* the project from
fooling itself, and they are working.

## 6. Where to go next

- To understand *how it's built*: [Architecture](02-architecture.md).
- To understand *the algorithm*: [Signal Engine](04-signal-engine.md) +
  [Strategies & Patterns](05-strategies.md).
- To understand *the research story*: [Backtesting & Research](09-backtesting-research.md).
- To understand *how to run it*: [Deployment](10-deployment.md) + [Operations](11-operations.md).
