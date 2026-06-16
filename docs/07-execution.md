# 07 · Execution (Layer 3 — boundary)

Execution is the **one place real money can move**. It is a boundary layer: all order I/O
lives here and nowhere else. The signal engine decides; risk validates; execution *acts*. Two
implementations satisfy one contract and are swapped by dependency injection at startup —
`dev`/labs get `SimulationExecution`, `prod`/staging get `LiveExecution`.

## 1. The contract (`execution/interface.py` — FROZEN)

`ExecutionInterface` is an abstract base class — the DI seam. It is **frozen** (human-only),
because changing it changes the real-order surface.

```python
async place_order(signal: Signal) -> dict          # → order_id, entry_price, tp/sl_price,
                                                    #   size_usdt, leverage, ts, fee_usdt,
                                                    #   notional_usdt, liquidation_price
async cancel_order(order_id: str, pair: str) -> bool
async get_position(pair: str) -> Optional[dict]
async close_position(pair: str, reason: str) -> dict # → exit_price, pnl_gross/fee/net_usdt,
                                                     #   pnl_pct, ts
async reconcile() -> list[dict]                      # all open positions (startup DB↔venue sync)
```

`place_order` raises `ExecutionError` on failure (an *environment* error — the watchdog
handles recovery). Everything that can be a *data* result is returned as a dict, not raised.

## 2. Simulation execution (`execution/simulation.py` — not frozen)

The paper-trading engine. It models fills, fees, slippage, trailing stops, and timeouts
faithfully so that simulation results transfer to live with minimal deviation (the §18
go-live bar requires < 15% deviation between simulated and real fills).

### Fee & slippage constants

```
_TAKER_FEE_PCT = 0.04 / 100   # 0.04% per side (market fills)
_MAKER_FEE_PCT = 0.02 / 100   # 0.02% per side (post-only limit fills)
_SLIPPAGE_PCT  = 0.05 / 100   # 0.05% per side (market fills only)
```

### The maker / taker model — fixing the win/loss asymmetry

`cfg.maker_execution` (env `MAKER_EXECUTION`, default `False` = taker, **live-safe**) selects
the fill model:

| | **Taker (default)** | **Maker (`MAKER_EXECUTION=true`)** |
|---|---|---|
| **Entry fill** | market: `entry × (1 ± slippage)` | post-only limit: **`entry`** (no slippage) |
| **Entry fee** | `notional × 0.04%` | `notional × 0.02%` |
| **Take-profit exit** | market: `exit × (1 ∓ slippage)`, taker fee | post-only limit: **`exit`** (no slippage), maker fee |
| **Stop / timeout / liquidation exit** | market + slippage + taker fee | **still market** + slippage + taker fee |

The asymmetry: maker improves the **win side** (entry + TP fill better) more than the **loss
side** (stops still market out). Round-trip cost falls from ~0.18% (taker) to ~0.04% (maker on
both legs). This was built to address the observed *"lose ~10% / win ~3%"* problem — and it
worked: over a 7-hour maker run the average win (+8.77%) ≈ average loss (−8.06%), versus the
3:10 ratio under taker. **It removed the structural bleed; it did not create edge** — the
underlying ~coin-flip then nets ≈ 0.

> **Caveat (documented honestly):** the maker model assumes slippage = 0 *and* guaranteed
> fills. Real post-only orders sometimes don't fill on a fast breakout (adverse selection),
> which is not modelled. So the maker numbers are an **optimistic upper bound** for momentum
> entries.

### PnL, trailing, and exits

PnL is direction-aware: long `pnl_gross = (exit − entry)/entry × notional`, short the mirror;
`pnl_net = pnl_gross − fee_entry − fee_exit`; `pnl_pct = pnl_net / size_usdt × 100`.

`check_exits()` runs once per candle and returns the first applicable reason in priority
order: **trailing-stop** (if enabled) → take-profit → stop-loss → liquidation → timeout
(`candles_held ≥ max_hold_candles`). `unrealized_pnl()` sums the mark-to-market gross PnL of
all open positions — this is what the [portfolio guard](06-risk-and-capital.md#5-the-portfolio-guard--the-manager-bot-engineportfolio_guardpy)
aggregates. (`unrealized_pnl` is sim-only and is **not** on the frozen interface.)

## 3. Trailing-close

Trailing-close lets winners run instead of capping them at a fixed TP. Geometry is in units of
**R = the initial stop distance** (`|entry − SL|`):

- The trail **arms** once favourable excursion ≥ `trail_activation_r × R`.
- Once armed, the stop ratchets to hold `trail_distance_r × R` below the running peak (above
  the running trough for shorts).
- The stop **only tightens, never loosens**, and is floored at the original SL. When trailing
  is on, the fixed TP is dropped (the trail replaces it).

**Research result:** trailing-close beats fixed-TP on every variant that traded — higher win
rate, far fewer timeouts — but is still −EV out-of-sample. It shrinks the bleed; it does not
create edge ([Backtesting & Research](09-backtesting-research.md)). It is enabled in the lab
(`trail_activation_r = 1.0`, `trail_distance_r = 1.0`, `max_hold_candles = 12`).

## 4. Live execution (`execution/live.py` — FROZEN)

The real-order engine. **Frozen, human-only** — the agent may never modify it; nothing here is
exercised in the current paper-only project.

- Built on `ccxt.async_support` with **isolated margin** mode; testnet via
  `set_sandbox_mode()`.
- **Market orders only** (taker; one `_TAKER_FEE_PCT = 0.04%`).
- **Idempotent:** every order carries `clientOrderId = f"kestrel-{signal.ts}-{signal.pair}"`,
  so a retried placement cannot double-fill.
- Entry computes quantity from notional against the live ticker, places with
  `params={"isIsolated": True, "leverage": cfg.leverage}`; close uses `reduceOnly: True`.
- Same PnL math as the simulator.

## 5. Provider registry & dependency injection (`execution/providers/`)

Execution venues register the same way patterns and feeds do.

```
ENV=dev      → SimulationExecution (paper; never contacts an exchange)
ENV=staging  → real provider, but TESTNET=true enforced (demo/testnet venue only)
ENV=prod     → real provider, real capital (gated by §18)
```

- **ccxt crypto venues** route through a ccxt factory → `LiveExecution`. Pre-registered:
  `bybit, binance, okx, kucoin, kraken, gate, mexc, bitget, bingx, huobi`.
- **Non-ccxt venues** self-register via `@register_execution("name")`:
  `alpaca, oanda, ibkr, exness`.

This is why the **BingX** broker decision is plug-and-play for crypto: set `EXCHANGE=bingx`
plus keys, no new code. (Note: ccxt BingX is **crypto-only** — forex would need a separate
non-ccxt integration such as OANDA or the Exness/MetaApi bridge.)

## 6. Where the frozen boundary bites

Because `live.py` and `interface.py` are frozen, the agent **cannot** add maker execution to
the live order path — only to the simulator. Live maker execution (post-only limit placement,
fill-probability handling, partial fills) is a **human-only** build, and is in any case gated
behind a validated edge that does not yet exist. Everything maker-related in the running
system is sim-side only.
