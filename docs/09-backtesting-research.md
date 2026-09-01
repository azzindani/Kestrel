# 09 · Backtesting & Research

This is the project's empirical conscience. Kestrel's most important outputs to date are
(a) a **rigorous, repeated, honest finding that no *net-of-fee* edge exists** in the
hypothesis space it has explored, and (b) **five cross-era-validated 1h leads** — real but
marginal signals that survive both a recent-year walk-forward and an untouched prior-year
lockbox. This document explains the methodology, the harness, and the verdicts. It is the
long-form companion to `FINDINGS.md`, to `RESEARCH_LOOP.md` (the autonomous loop's full
iteration log — the living continuation of this document), and to the
[Overview status note](01-overview.md#5-honest-status--no-proven-edge). Since 2026-07-09,
new research is scored on the **[Points Framework](13-points-framework.md)** (gross points
+ win rate) rather than net dollars.

## 1. Methodology — the rules that prevent self-deception

Every backtest in Kestrel obeys these non-negotiables (`CLAUDE.md` §13/§29/§30):

1. **Fees + slippage always applied.** No backtest runs without the cost model
   (~0.18% round-trip taker, ~0.04% maker). A strategy that looks good gross and dies net is
   a *loss*, and the harness reports it as one.
2. **Walk-forward, never in-sample-only.** Train on 60% of the window, test on the held-out
   40%. A result that only shines in-sample is overfit and rejected.
3. **The 120-candle window constraint.** The backtest runner passes each entry only the last
   **120 candles** (`window = candles[i-119:i+1]`) — *exactly* what the live detector sees.
   Any indicator with a longer lookback silently never fires. This is why Connors' SMA(200)
   produced zero trades and had to become SMA(100); **any deployable pattern must use ≤120
   candles of history.**
4. **The cost-vs-move reality check.** At 5m the *average* price move is ~0.164%, *below* the
   0.18% round-trip cost. The game is negative-sum before a strategy starts. This is the
   single most important fact in the whole project.

## 2. The research harness (`scripts/*.py`)

These are one-off, read-only research tools — they fetch public OHLCV (no keys) and run the
**production** indicator + signal code, so backtest behaviour matches live behaviour.

| Script | What it does |
|---|---|
| `backtest_real.py` | Fetch real OHLCV (okx/gate/kraken fallback), build candles through the production `CandleBuilder`, walk-forward 60/40 with costs. `fetch_ohlcv(pair, tf, days, offset_days=0)` — **`offset_days` shifts the window back to build a lockbox** (data the search never saw). |
| `algo_search.py` | The algorithm-search harness: rank many hand-written entry archetypes across months in minutes, walk-forward, costs. Key flags grown over the research loop: **`--fees taker\|maker\|none`** (the `none` mode powers gross-edge decomposition), **`--offset-days`** (lockbox), **`--by-pair`** (per-pair breadth tables — the iter-43 playbook), **`--deflated-sharpe`** (Bailey & López de Prado PSR/DSR), **`--htf-confirm 4h\|1d`** (cross-timeframe confluence test, refuted iter 51), plus the `ensemble_Kof4` voting algos. **`--intrabar tp_first\|sl_first\|close`** (2026-09-01) — how a candle spanning both bracket levels is resolved: `close` = sim parity (exits on the close only, filled at the close), `sl_first` = worst case; the runner default `tp_first` is optimistic and is what made the hiwin33 bracket read 70% while the live fleet booked 54%. **`--atr-floor-bps`** — cost-floor entry gate (block entries with ATR14 < N bps of price). Monkeypatches the runner fee constants *and* `risk.manager.round_trip_fee_pct` at runtime (cannot edit the frozen risk file). |
| `retired_ledger.py` | `list` / `check [bots file]` against `retired_strategies.json`, the permanent ledger of forward-test-retired (pattern, timeframe, bracket) cells. `check` exits 1 if a bots file redeploys one. Run alongside `bot_registry.py check`. |
| `backtest_grid.py` | TP×SL×hold grid sweep over months, walk-forward; emits `.md` leaderboard + `.csv` + `.json` per-trade detail (MAE/MFE/realised-R). |
| `param_sweep.py` | Grid over `params.json` ranges (TP/SL, min-confidence, volume); does *any* in-range config clear §30? |
| `edge_scan.py` | Predictive-power scan: information-coefficient + quintile spread per feature vs the ~0.18% cost, at 1/4/8-candle horizons. Answers "is there exploitable structure *at all*?" before strategy design. |
| `backtest_trailing.py` | Fixed-TP vs trailing-close A/B on identical entries — isolates the exit effect. |
| `backtest_wave.py` | The wave family (ride/scalp/flip) on real data, walk-forward. |
| `research_4h_meanrev.py` | Hardens the `ema_spread` mean-reversion idea edge_scan surfaced — fade extreme spread, taker *and* maker. |
| `simulate_local.py` | Synthetic-data backtest for unit-testing indicator/pattern logic without external data. |
| `fetch_ohlcv.py` | Public OHLCV fetch utility (kucoin/kraken/okx/bybit). |
| `build_*lab.py` | Generate `bots.json` for a fleet (see [Deployment](10-deployment.md)). |
| `reset_dev.py` / `backfill_history.py` | Reset the dev slate / warm the candle history. |

Reports land in `reports/` (e.g. `reports/lockbox_4h.log`, `reports/algo_search_*.md`,
`reports/breakout_xyear.log`).

## 3. The metrics that matter (and the one that doesn't)

A core research finding (from a 4-agent literature sweep) is that **the §18/§30 "win rate >
55%" go-live bar is the wrong metric** — and it is currently *blocking the only profitable
family*:

- Trend/momentum strategies — the whole profitable family in the literature — run **30–45%
  win** and make money on **payoff ratio** (3–5×), not hit rate. A `>55%` bar structurally
  *excludes all momentum* and only ever validates mean-reversion.
- Professionals optimise **expectancy + profit factor (≥1.3) + Deflated Sharpe**, with R/R as
  a *constraint*, not the target.

> **This is a `CLAUDE.md` §18/§30 amendment, which is §3 HUMAN-ONLY.** The agent surfaced it as
> the user's decision and cannot change it. Until the bar is amended, even a genuinely +EV
> momentum strategy "fails" go-live on win rate alone. This is documented as a *known tension*
> between the contract and the research, not a resolved item.

## 4. The verdicts (chronological)

1. **Single-rule entries lose everywhere.** Param sweep: 0/36 configs passed §30; apparent
   winners were overfit noise. Across 18 archetypes × (crypto + forex) × 5m/1h/4h/1d: nothing.
2. **5m is structurally dead.** A 168-trade 5m lab: 30.4% win, −$18.79, nearly everything
   dying on stop-loss. The mean move is below cost — confirmed, not inferred.
3. **The wave family: no edge, not overfit.** 120d × 10 pairs, 0/3 clear §30, all ~30% win,
   all ≈ −$0.18–0.20/trade OOS, IS ≈ OOS. Wider SL did **not** raise the win rate — refuting
   the "premature stop-out" hypothesis. It is *no edge*, not *bad tuning*.
4. **The maker lever is real and large** — confirmed across all 10 pairs. Every strategy
   improves taker→maker; round-trip cost ~0.18% → ~0.04%. **But it only amplifies whatever
   edge exists.**
5. **The momentum "win" was data-mined and is refuted.** `mom_adx`/`triple_mom` at 4h were
   net-positive on a recent-year 10-pair walk-forward (the first broad positive result) and
   were promoted. The **prior-year lockbox** (data no search ever touched) then showed them
   **net-negative**. `breakout_vol` is the exact mirror (positive prior-year, negative
   recent-year). **No hand-written entry is +EV on both independent years.** Lesson: a single
   365-day OOS window is *not enough* — correlated pairs ≈ one bet, one year ≈ one regime;
   belief requires passing **both** a recent-year and a prior-year lockbox.
6. **RSI-2 was a 2-pair fluke** that did not generalise across 10 pairs — textbook
   multiple-testing shrinkage.
7. **`ema_spread` mean-reversion: refuted.** Real but tiny (~0.04%/trade gross, below even
   maker cost), IS→OOS is noise (sign flips, t ≈ 0).

**Synthesised conclusion (as of 2026-06-16):** the hand-written-OHLCV-entry hypothesis space
looked exhausted — no cost-robust, cross-year-robust edge at any timeframe under either fee
model.

### 4b. The research loop era (iterations 18–54, 2026-06-21 → present)

The owner then authorized indicator strategies ("macd, rsi, moving average any period"),
and the autonomous research loop (`RESEARCH_LOOP.md`) found the project's only
**cross-era survivors** — each +EV on BOTH the recent-year walk-forward AND the untouched
prior-year lockbox at **1h, maker**:

| Lead | Iter | Recent expR | Lockbox expR | Note |
|---|---|---|---|---|
| `macd_cross` | 18 | +0.13 | +0.17 | trend-aligned MACD signal cross — the first survivor ever |
| `macd_rsi` | 22 | +0.06..0.09 | +0.12 | raw cross + RSI-14 confirm; RSI rescues it |
| `cci_mom` | 31 | +0.12 | +0.07 | CCI ±100 breakout; ~3× activity |
| `sma_cross` | 32 | +0.14 | +0.12 | 9/21 SMA cross; most fill-robust (survives taker, iter 46) |
| `ensemble_3of4` | 52 | +0.07 | +0.14 | ≥3-of-4 lead voting; best R/R (1.68) on record |

Equally important, the loop **refuted dozens of siblings and filters** with the same
recurring signature — strong recent year, negative lockbox (ema_cross, donch/breakout
variants, stochastic, cci_revert, supertrend, VWAP, pairs stat-arb, lead-lag, ADX/
volatility/session/HTF confluence gates, 15m down-shift, 4h up-shift…). The meta-lesson:
**momentum-breakout crosses can validate cross-era; mean-reversion fades and add-on
filters never have.** Full ledger in `RESEARCH_LOOP.md`.

The formal capstones:

- **Deflated Sharpe (iter 47):** even the best cell (`sma_cross`/wide) — per-trade Sharpe
  +0.164, PSR(>0)=1.000 in-sample — **fails DSR > 0.95 at the project's true search
  breadth** (N ≥ 60–200 trials) and fails the lockbox at every N. The strongest signal is
  statistically indistinguishable from the best of many random tries.
- **Live corroboration (iter 49):** live PSR on the real forward-test agrees — no lead
  clears even the raw 0.95 bar; backtest-best and live-best *disagree* (the signature of
  noise dominating).
- **Gross decomposition (iter 42):** all leads are gross-positive in both eras — **the 1h
  directional tilt is real; the wall is purely the ~4 bps fee.** This finding is what the
  [Points Framework](13-points-framework.md) is built on.

The remaining un-exhausted levers are **cost-side** (venue/fee tier, §4 owner) and
**structural** — chiefly **funding-rate harvesting** (long spot + short perp; needs perps
= §13/§4 owner amendment, two-leg infrastructure, a funding feed). Scoped, not built.

### 4c. The points reframe (2026-07-09 →)

Research is now scored in **gross points (bps of entry price) + win rate** with the fee
hurdle tracked as an explicit ladder (>0 signal / ≥4 bps maker-viable / ≥18 bps
taker-viable), and win rate treated as an engineerable exit-geometry property (the
`1/(1+g)` law). The active program — HiWin re-geometry of the five leads, a
mean-reversion re-audit under its natural high-win geometry, and MFE/MAE exit mining on
the 208k-candle `trade_context` corpus — is specified in
[Points Framework §5](13-points-framework.md), with targets in §6. All the
anti-self-deception machinery above (walk-forward, lockbox, breadth, DSR, refuted ledger)
carries over unchanged.

### 4d. The fill-model verdict (2026-09-01)

The first full forward test of the points program (hw33: six entries × 34 pairs × the
hiwin33 bracket, 10,291 closed 5m trades in 8 days) came back at **53-55% win, PF
0.55-0.64**, not the 68-72% the sweep had promised. The gap is not the entries: the
backtest runner resolves any candle that touches *both* bracket levels as a take-profit
(TP is checked on the candle's favourable extreme before the stop on its adverse
extreme), and with a 15 bps TP on 5m candles that is most candles. The sim only sees the
close, so it books fewer wins, fills them 2× past the bracket, and fills stops 1.4× past
theirs (realized R/R 0.57 vs bracket 0.33). `algo_search.py --intrabar close` reproduces
the live numbers to within a few points. Under every fill model the bracket is −EV.

Two rules came out of it, both now in the refuted ledger: a bracket narrower than a
typical candle range is validated only under `--intrabar close` **and** `sl_first`; and
a win rate that appears only under the optimistic default belongs to the harness, not the
signal. The same pass found that no stored indicator (ADX, RSI, volume ratio, BB width)
separates live winners from losers at all, and that the only monotone live feature is ATR
as a share of price, which is a cost-floor mechanism rather than a signal.

## 5. Why keep running the lab, then?

The dev fleet (~161 bots) is **not** just chasing an edge — it is:

- the **live forward-test of the five leads** — the only place their marginal backtest
  edge can be confirmed or refuted with real (paper) fills over weeks;

- a **landscape / parity lab** — confirming the simulator matches the production code paths
  across timeframes and the maker/taker model behaves as modelled;
- a **forward-test** — exercising every §18 go-live criterion (uptime, watchdog restart,
  graceful close, Telegram, backups) on live data with zero capital at risk;
- an **activity generator** — producing trades so the operational machinery, dashboards, and
  the trade-context training corpus are exercised at fleet scale.

It is honest infrastructure validation, clearly labelled as such.

## 6. The Definition of Done for any strategy change

Before *any* strategy change is considered validated (`CLAUDE.md` §30):

```
[ ] backtest on ≥ 90 days of data
[ ] walk-forward (train 60% · test 40%)
[ ] fee + slippage model applied
[ ] (current contract) win rate > 55% out-of-sample      ← see §3: this bar is contested
[ ] R/R ≥ 1.2 on average
[ ] tune.sh reports ACCEPT (no > 5% regression vs baseline)
[ ] — and per the lockbox lesson — pass BOTH a recent-year AND a prior-year lockbox
```
