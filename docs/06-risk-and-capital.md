# 06 · Risk & Capital

This document covers everything that stands between a *signal* and *money at risk*: the bucket
capital model, the six hard risk rules, equity-scaled position sizing, the liquidation math,
and the portfolio guard ("manager bot"). The risk manager itself is a **frozen, human-only**
file (`src/risk/manager.py`, `CLAUDE.md` §3/§24) — the agent may never modify it.

## 1. The bucket capital model

```
Total simulated capital:  $100 USDT
Bucket size:              $10 USDT, isolated collateral per position
Max buckets:              the single-bot capital model is $100 / $10 = up to 10 slots
Leverage:                 10×–50×  (lab: 20×)
```

> **`max_active_buckets` in the fleet:** the `params.json` base default is **20** (range
> `[1, 20]`), but **every lab bot in `bots.json` overrides it to `1`** — so each of the 120
> bots holds at most one open position at a time, and is its own isolated $10 bucket. The
> "$100 / 10 buckets" picture above is the *single-bot* capital model from `CLAUDE.md` §13/§24;
> the fleet realises it as 120 independent one-bucket bots rather than one ten-bucket bot.

- Each open position consumes one **bucket** of isolated collateral. Buckets never share a
  pool — a blow-up in one cannot drain another.
- A liquidated bucket logs its loss, frees its slot, and the next bucket opens fresh.
- **Capital state is authoritative in the database**, never in memory. On restart the daemon
  reconciles DB state against exchange state; it never assumes the in-memory view is current.

## 2. The six risk rules (`risk/manager.py` — FROZEN)

`validate(signal, state, cfg) -> ValidationResult` applies six rules **in order** and returns
on the **first** failure (`passed=False, reason=<code>`). All thresholds are module constants:

| # | Rule | Condition to pass | `reason` on fail |
|---|---|---|---|
| 1 | **Bucket capacity** | `active_positions < max_active_buckets` | `bucket_limit` |
| 2 | **Liquidation distance** | `\|entry − liq\| / entry ≥ 0.015` (1.5%) | `liquidation_too_close` |
| 3 | **Risk/reward** | `tp_dist / sl_dist ≥ 1.2` | `rr_below_minimum` |
| 4 | **Fee viability** | `tp_pct > round_trip_fee_pct() × 1.5` | `fee_not_viable` |
| 5 | **Daily loss limit** | `session_net_pnl > −5.00 USDT` (resets 00:00 UTC) | `daily_loss_limit` |
| 6 | **Stale data** | `≥ 60 s` since last WS reconnect (or never reconnected) | `stale_data` |

Constants: `_MIN_LIQ_DISTANCE_PCT = 0.015`, `_MIN_RR = 1.2`,
`_FEE_VIABILITY_MULTIPLIER = 1.5`, `_DAILY_LOSS_LIMIT_USDT = -5.00`,
`_WS_STALE_WINDOW_SEC = 60`, `_MAINTENANCE_MARGIN_RATE = 0.005`.

Rule 3 also short-circuits with `reason="sl_distance_zero"` if the stop distance is exactly
zero (a degenerate signal) before computing the R/R ratio.

**Rule 4 is the edge gate.** `round_trip_fee_pct()` returns **0.18%** (taker 0.04%×2 +
slippage 0.05%×2). A trade's take-profit distance must exceed 1.5× that — so the expected
gross gain must beat costs by a margin before any order is allowed. This single rule is *why*
5m strategies (whose average move is below 0.18%) structurally cannot trade profitably: most
of their signals are rejected `fee_not_viable`, and the ones that pass don't clear cost on
average.

> **Maker-fee subtlety:** `round_trip_fee_pct()` is left at the **taker** 0.18% even when the
> simulator runs the maker model. This is intentional and safe — the lab's `tp_atr=2.4`
> (~1.4% move) clears the taker bar regardless, so no genuinely-viable maker trade is wrongly
> blocked. The research harness, which needs the maker bar for sweeps, monkeypatches the fee
> at runtime (it cannot edit the frozen file).

## 3. Equity-scaled position sizing (`signal/sizing.py`)

Sizing is **not** a fixed $10. It scales with the bucket's current equity so the system
compounds when winning and de-risks when losing. `compute_position_size(confidence, state,
params)`:

```
0. fallback  : if SizingState is None → legacy fixed bucket ($10 full / $5 half), done
1. band      = full if confidence ≥ 0.75 else half
2. frac      = size_fraction_full (1.0) if full else size_fraction_half (0.5)
3. size      = equity × frac                              # equity-scaled base
4. drawdown  : drawdown = (peak_equity − equity) / peak_equity
                   if drawdown ≥ drawdown_derisk_threshold (0.20):
                       size ×= drawdown_derisk_factor (0.5)   # only once past a 20% drawdown
5. cooloff   : if consec_losses ≥ consec_loss_cooloff (3):
                   size ×= consec_loss_factor (0.5)
6. cap       : size = min(size, equity)
7. floor     : if size < size_min_usdt (1.0):
                   bump up to the floor if equity ≥ floor, else 0 (bucket exhausted → stop)
```

The `SizingState` (`equity_usdt`, `peak_equity_usdt`, `consec_losses`) is read live from the
database each candle (`db.get_sizing_state(...)` — equity = starting bucket + realised PnL;
consec_losses from the trailing closed trades). **Live proof it works:** observed position
sizes ranged $0.32–$6.92 as bucket equity moved — confirming compounding and de-risking are
active, not theoretical.

### The per-trade loss cap (`cap_size_for_risk`)

A second function bounds the *downside* of any single stop-out independently of leverage:

```
sl_dist_pct = |entry − sl_price| / entry
cap         = (max_loss_pct_per_trade × equity) / (leverage × sl_dist_pct)
size        = min(size, cap)
```

With `max_loss_pct_per_trade = 0.01` (lab; lowered from 0.02 on 2026-06-16) and 20× leverage, a
stop-out is capped at ~1% of bucket equity — even though the raw position move at the stop is
`leverage × SL-distance` ≈ 18% of *notional* (20× × 1.5-ATR ≈ 0.9% stop). **This is the
resolution to the "I lose 10%+ in one trade" observation:** that figure is the % of the
*position*, while the cap limits the dollar loss to ~1% of bucket *equity* by shrinking the
position. Crucially, with the cap active the per-trade **notional** is `max_loss_pct × equity /
sl_dist_pct` — *independent of leverage* — so lowering `max_loss_pct` 0.02→0.01 and widening the
stop 1.0→1.5 ATR together cut per-trade notional by ~3× *for the same candle* (e.g. ~3.3× → ~1.1×
equity at a typical ~0.6% ATR). The **absolute** notional still scales *inversely* with volatility
(the cap fixes dollar-risk, not notional) — on a quiet, low-ATR candle the same 1% dollar-risk
buys a larger position, so notional can run several× equity; what is bounded is the **dollar loss
per stop ≈ 1% of bucket equity**, and the margin actually committed (observed ~$1.5–3 on a $10
bucket). To shrink the per-trade **%-of-margin** figure itself, the only clean lever
is **lower leverage** (`.env`, §4 human-gated) — a wider stop *raises* it.

## 4. Liquidation math (`config.py`)

Computed and stored on every position open:

```
long:  liq = entry × (1 − 1/leverage + maintenance_margin_rate)
short: liq = entry × (1 + 1/leverage − maintenance_margin_rate)
maintenance_margin_rate = 0.005   (Binance spot margin, BTC/ETH)
```

Risk Rule 2 then refuses any signal whose liquidation sits closer than 1.5% from entry —
combined with the fixed-percent stop clamp (`0.7/leverage`,
[Signal Engine §3](04-signal-engine.md#3-tp--sl-computation)), liquidation can never
front-run the stop.

## 5. The portfolio guard — the "manager bot" (`engine/portfolio_guard.py`)

A single in-process coordinator that watches **aggregate** unrealised PnL across the whole
fleet and force-closes *everything* when it crosses a band. This is the user's "seize every
opportunity / don't let it slide" idea, built as risk-shaping (it reshapes variance; it does
**not** create edge).

- **Construction:** `PortfolioGuard(cfg, session_id, notifier, tp_pct, dd_pct)` with
  `check_interval_s = 20`, `cooldown_s = 60`.
- **`enabled`** ⇔ `tp_pct > 0 or dd_pct > 0`. Disabled (both zero) by default → **live-safe**;
  the lab enables ±10% via `PORTFOLIO_TP_PCT=0.10` / `PORTFOLIO_DD_PCT=0.10`.
- **`_aggregate()`** sums `(equity, unrealised)` across all attached bots via each bot's
  `portfolio_snapshot()`.
- **`_decide(equity, unrealised)`** — with `ratio = unrealised / equity`:
  - `ratio ≥ tp_pct` → `"portfolio_take_profit"` (profit-lock)
  - `ratio ≤ −dd_pct` → `"portfolio_drawdown_stop"` (drawdown-stop)
  - else `None`. (Zero equity never fires.)
- **`_fire(reason)`** tells every bot to close its own positions
  (`asyncio.gather(*[bot.force_close_all(reason) ...])`), writes **one** aggregate event, and
  sends **one** Telegram message.
- **`run()`** loops every 20 s; after firing it sleeps the 60 s cooldown before re-arming.

**One-owner-per-resource is preserved:** the guard *instructs*, but each bot closes its **own**
positions (`Daemon.force_close_all` → `Daemon._close_position`). The guard never reaches into
another bot's order state. Forced/guard closes are **taker market-outs** (no maker on a
panic close). The supporting plumbing lives in `SimulationExecution.unrealized_pnl()` and
`Daemon.portfolio_snapshot()` — both sim-only additions; neither touches the frozen execution
interface.

## 6. Risk-shaping vs edge — a standing caveat

The two risk overlays added most recently — **trailing-close**
([Execution §3](07-execution.md#3-trailing-close)) and the **portfolio guard** above — are
**variance shapers, not edge creators.** Trailing-close measurably lifts the win rate and
slashes timeouts (it banks runners), and the guard caps fleet-wide swings — but on a system
whose underlying expectancy is ≈ 0, reshaping the outcome distribution nets ≈ 0. They make the
ride smoother; they do not make a losing system win. The only remaining real-profit lever is
**structural** (e.g. funding-rate harvesting), not more risk overlays. See
[Overview §5](01-overview.md#5-honest-status--no-proven-edge).

## 7. Sizing & exit parameters (`params.json` defaults)

| Param | Default | Role |
|---|---|---|
| `size_fraction_full` / `_half` | 1.0 / 0.5 | fraction of equity at full / half conviction |
| `size_min_usdt` | 1.0 | bucket treated as exhausted below this |
| `drawdown_derisk_threshold` / `_factor` | 0.20 / 0.5 | cut size when in drawdown past threshold |
| `consec_loss_cooloff` / `_factor` | 3 / 0.5 | cut size after a losing streak |
| `max_loss_pct_per_trade` | 0.01 (lab: 0.01) | per-trade equity loss cap (lowered 0.05/0.02→0.01) |
| `tp_atr_multiplier` / `sl_atr_multiplier` | 2.4 / 1.5 (lab: 2.4 / 1.5) | ATR-based exit distances (R/R 1.6; wider stop than the old 1.0) |
| `max_hold_candles` | 6 (lab: 6) | timeout exit (lab lowered 12→6) |
| `trailing_enabled` / `trail_activation_r` / `trail_distance_r` | false / 0.5 / 0.5 | trailing-close; lab: enabled, arms at +0.5R, trails 0.5R (was 1.0/1.0) |
| `tp_sl_pct_enabled` / `tp_pct` / `sl_pct` | false / 0.05 / 0.025 | fixed-percent exit mode |
