# 02 · Architecture

Kestrel follows a strict **layered architecture** with unidirectional dependencies. The
goal is that *pure logic never touches I/O*, and *I/O never contains business logic*. This
makes the signal engine trivially testable (feed it candles, assert on the signal) and
makes the boundary between "decide" and "act" the one place where real money can move.

## 1. The four layers

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Layer 3 — BOUNDARY (all I/O lives here, and ONLY here)                     │
│   execution/   db/   notify/   viz/                                        │
│   adapters · external integrations · the only place that touches the world │
└──────────────────────────────────────────────────────────────────────────┘
                 ▲ may import any inner layer
┌──────────────────────────────────────────────────────────────────────────┐
│ Layer 2 — DATA ASSEMBLY                                                    │
│   data/   (candle builder, feed adapters → domain types)                   │
└──────────────────────────────────────────────────────────────────────────┘
                 ▲ may import Layer 0 + Layer 1
┌──────────────────────────────────────────────────────────────────────────┐
│ Layer 1 — DOMAIN LOGIC (pure transforms · NO I/O)                          │
│   engine/  signal/  risk/  backtest/                                       │
└──────────────────────────────────────────────────────────────────────────┘
                 ▲ may import Layer 0 only
┌──────────────────────────────────────────────────────────────────────────┐
│ Layer 0 — CORE (types · enums · constants · pure utilities · NEVER I/O)    │
│   src/config.py                                                            │
└──────────────────────────────────────────────────────────────────────────┘
```

**Dependency rules (hard):**

```
Layer 0 → nothing
Layer 1 → Layer 0 only
Layer 2 → Layer 0 + Layer 1
Layer 3 → any inner layer
✗ inner layers may NEVER import a boundary (Layer 3) module
```

`execution/live.py` and `execution/simulation.py` implement the **identical interface**
(`execution/interface.py`) and are swapped via **dependency injection at startup** — `dev`
injects `SimulationExecution`, `prod` injects `LiveExecution`. No code branches on `ENV`
except this one DI decision.

## 2. Function classification

Every function is exactly one of two kinds:

| Kind | Where | Rule |
|---|---|---|
| **Logic function** | inner layers (0/1) | pure: data in → data out · **no I/O ever** |
| **Shell function** | boundary (Layer 3) | reads/writes the world · calls logic functions |

If a function would mix I/O and a transform, it is split: a shell function does the I/O and
calls a logic function for the computation. Example: the candle builder's *shell* receives
the websocket tick; a *logic* function computes the indicators.

## 3. Directory map

```
kestrel/
├── CLAUDE.md · README.md · FINDINGS.md · params.json · bots.json · requirements.txt
├── docs/                      ← this documentation
├── scripts/                   ← lifecycle (.sh, frozen) + research harness (.py)
├── docker-compose.yml         ← Phase-1 labs (postgres · kestrel · grafana · pg-backup)
├── docker-compose.staging.yml ← Phase-2 quarantine (BingX VST demo)
├── docker-compose.override.yml← host-local overrides (gitignored)
└── src/
    ├── config.py              ← Layer 0: types · enums · env schema · pure utils · NO I/O
    ├── engine/                ← Layer 1: daemon · watchdog · scheduler · portfolio_guard
    ├── data/                  ← Layer 2: feed (WS) · candle_builder · providers/
    ├── signal/                ← Layer 1: indicators · regime · patterns · detector · memory · sizing
    ├── execution/             ← Layer 3: interface · simulation · live · providers/
    ├── risk/                  ← Layer 1: manager  (FROZEN — human-only)
    ├── db/                    ← Layer 3: connection · schema · writer
    ├── backtest/              ← Layer 1: runner · metrics
    ├── notify/                ← Layer 3: telegram
    └── viz/                   ← Layer 3: dashboard (rich terminal)
```

> **Note on `risk/`:** the risk *manager* is pure domain logic (Layer 1) but is also a
> **frozen file** — see §6. Its placement in Layer 1 reflects that it is a pure transform
> (`validate(signal, state, cfg) -> ValidationResult`); its frozen status reflects that it
> guards real money and is therefore human-only.

## 4. Module public APIs

These are the declared, externally-consumable entry points (everything else is internal):

```python
signal/detector.py:    evaluate(candles, params, bot_id, session_id, env, ...) -> (Signal, None) | (None, Rejection)
signal/patterns.py:    registry: dict[str, PatternFn]          # + register(name) decorator
risk/manager.py:       validate(signal, state, cfg) -> ValidationResult
execution/interface.py: place_order · cancel_order · get_position · close_position · reconcile
db/writer.py:          write_candle · write_signal · write_trade · write_event  (+ read helpers; all async)
```

## 5. Extension architecture — capabilities are registered, not hardcoded

A core principle (`CLAUDE.md` §9): **add a capability by registering it, never by editing a
dispatcher.** Patterns are the canonical example. The detector does a registry lookup; it
contains no `if pattern == "...":` chain.

```python
# src/signal/patterns.py
PatternFn = Callable[[Sequence[Candle], Params], Optional[PatternResult]]
registry: dict[str, PatternFn] = {}

def register(name: str) -> Callable[[PatternFn], PatternFn]:
    def wrap(fn: PatternFn) -> PatternFn:
        registry[name] = fn
        return fn
    return wrap

@register("mom_adx")
def detect_mom_adx(candles, params): ...
```

Execution venues and data feeds use the same registry pattern (`@register_execution("name")`
/ a ccxt factory) — see [Execution](07-execution.md) and [Data Pipeline](03-data-pipeline.md).

## 6. Frozen vs agent-editable files

The coding agent operates under a strict scope (`CLAUDE.md` §3 / §25). This boundary is the
project's primary safety mechanism: it keeps automated changes away from the code that can
move real money or destroy data.

| Scope | Files | Why |
|---|---|---|
| **FROZEN — human only** | `risk/manager.py` · `execution/live.py` · `execution/interface.py` · `db/schema.py` · `scripts/*.sh` · `.env` · `CLAUDE.md` | Real-money order path, the risk gate, the schema, deploy scripts, credentials, the contract itself. |
| **Agent may modify** | `signal/patterns.py` · `signal/indicators.py` · `signal/detector.py` · `signal/regime.py` · `signal/memory.py` · `params.json` (within declared ranges) · the research scripts (`scripts/*.py`) · `bots.json` | Strategy logic and tuning — the experimental surface. |

Practical consequence you will see throughout these docs: when research needed to model
**maker fees**, it could *not* edit `risk/manager.py` (frozen). Instead the research harness
**monkeypatches** the fee constant at runtime, and the live-relevant maker behaviour was
added to `execution/simulation.py` (not frozen) — never to `execution/live.py` or
`interface.py` (both frozen). The frozen boundary shapes *how* features get built.

## 7. Error architecture

| Error type | Strategy |
|---|---|
| **Programmer error** | fail fast · crash · fix the code |
| **Data error** | return a typed `Result`/`Rejection` — ✗ do not raise for flow control |
| **Environment error** | raise · the watchdog restarts the process |
| **WS disconnect** | exponential backoff, max 5 retries, Telegram alert, then wait |
| **Exchange failures** | circuit-breaker pattern (consecutive failures → stop, cooldown, probe) |
| **Partial failure** | accumulate all errors · ✗ do not stop on the first |

The signal pipeline embodies the "data error → typed result" rule: each stage returns a
typed result **or** a typed `Rejection` (with `stage` + `reason`); it never raises to signal
"no trade here." Rejections are logged to the `signals` table with `outcome='rejected'`.

**Graceful degradation:** if the websocket drops, signal *evaluation* is suspended but open
positions continue to be *monitored*. The daemon never holds an open leveraged position with
no monitoring process alive — `stop.sh` closes all positions before the process exits.
