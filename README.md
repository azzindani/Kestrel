# Kestrel

**A real-time signal-detection and execution daemon for crypto markets.**
Python · asyncio · PostgreSQL · ccxt.

Kestrel subscribes to live candles, runs each *closed* candle through a pure-functional signal
pipeline, validates any signal against six hard risk rules, and either **simulates** the trade
(research/paper) or **executes** it (live). Every candle, signal, trade, and event is persisted
to PostgreSQL. Many bots run side-by-side in a single asyncio event loop.

> ⚠️ **Status: paper/research only — no proven edge.** Exhaustive walk-forward research (with
> fees, slippage, and an untouched prior-year lockbox) has found **no cost-robust,
> cross-year-robust tradeable edge** in any hand-written OHLCV entry, at any timeframe, under
> taker or maker fees. Kestrel today is a rigorously-built research and forward-testing
> platform — **not** a money-making system. No real capital, no live keys, no prod VPS.
> See [`FINDINGS.md`](FINDINGS.md) and [`docs/01-overview.md §5`](docs/01-overview.md#5-honest-status--no-proven-edge).

## Documentation

| Where | What |
|---|---|
| **[`docs/`](docs/README.md)** | **Verbose engineering reference** — every subsystem, with file:line precision. Start here. |
| [`CLAUDE.md`](CLAUDE.md) | The **specification & contract** — rules, frozen files, schema, go-live gates. Authoritative for *what is allowed*. |
| [`FINDINGS.md`](FINDINGS.md) | The **research verdict** — the empirical "is there an edge?" report. |

Jump straight to a subsystem:
[Architecture](docs/02-architecture.md) ·
[Data Pipeline](docs/03-data-pipeline.md) ·
[Signal Engine](docs/04-signal-engine.md) ·
[Strategies & Patterns](docs/05-strategies.md) ·
[Risk & Capital](docs/06-risk-and-capital.md) ·
[Execution](docs/07-execution.md) ·
[Database](docs/08-database.md) ·
[Backtesting & Research](docs/09-backtesting-research.md) ·
[Deployment](docs/10-deployment.md) ·
[Operations](docs/11-operations.md) ·
[Go-Live](docs/12-go-live.md)

## Quick mental model

```
exchange WS/REST → candle builder → signal engine (regime→trend→pattern→volume→build)
                                          → risk.validate() (6 rules) → execution (sim|live)
                                          → PostgreSQL (candles·signals·trades·context·events)
```

- **Capital:** $100 simulated, in isolated **$10 buckets**, equity-scaled sizing, 10×–50×.
- **Costs (always modelled):** ~0.18% round-trip taker, ~0.04% maker; a trade must clear cost
  to be allowed (risk Rule 4).
- **Current fleet:** 120 paper bots (10 pairs × 3 momentum strategies × 4 timeframes) on a
  Gate.io feed with simulation execution.

## Running (containerised dev)

```bash
docker compose up -d --build                                   # src is baked → rebuild on code change
docker compose exec kestrel python3 scripts/reset_dev.py --yes # clean slate (keeps candles)
docker compose exec kestrel python3 scripts/backfill_history.py --source gate
docker compose restart kestrel                                 # reload bind-mounted bots.json / params.json
docker compose logs -f kestrel
```

`params.json` and `bots.json` are bind-mounted (restart to reload); `src/` is baked into the
image (rebuild to apply). See [Deployment](docs/10-deployment.md) for the full topology and the
three-phase (labs → staging → prod) model.
