# 09 · Backtesting & Research

This is the project's empirical conscience. Kestrel's most important output to date is not a
strategy — it is a **rigorous, repeated, honest finding that no tradeable edge exists** in the
hypothesis space it has explored. This document explains the methodology, the harness, and the
verdicts. It is the long-form companion to `FINDINGS.md` and to the
[Overview status note](01-overview.md#5-honest-status--no-proven-edge).

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
| `algo_search.py` | The algorithm-search harness: rank many hand-written entry archetypes across months in minutes, walk-forward, costs. Adds the **`--fees taker\|maker`** toggle and **`--offset-days`** lockbox. Includes Connors RSI-2 entries (`rsi2_ct/ct5/raw`). Monkeypatches the runner fee constants *and* `risk.manager.round_trip_fee_pct` at runtime (cannot edit the frozen risk file). |
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

**Synthesised conclusion:** the hand-written-OHLCV-entry hypothesis space is **exhausted**.
There is no cost-robust, cross-year-robust edge in any indicator-pattern at any timeframe under
either fee model. *Edge is timeframe + fees + sizing + structure, not more indicators.* The
remaining real candidates are **structural** — chiefly **funding-rate harvesting** (long spot +
short perp), which has the highest structural ceiling but needs two-leg infrastructure, a
funding feed, and larger buckets than $10. That is scoped but not built.

## 5. Why keep running the lab, then?

The 120-bot lab is **not** chasing an edge — it is:

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
