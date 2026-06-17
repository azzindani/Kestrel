# RESEARCH_LOOP — autonomous strategy-evaluation loop

> **Purpose:** every 8 hours, evaluate Kestrel's algorithm + strategy, attempt one
> improvement, and stop only when a real target is hit. This file is the loop's **memory**:
> each firing reads it to know what has been tried, what the current best is, and whether to
> stop. Append to it every iteration. Started 2026-06-17.

---

## STOPPING CONDITIONS — stop the loop (delete cron, Telegram CRITICAL) when EITHER holds

1. **Win-rate target (user's bar):** rolling win rate **≥ 70%** over **≥ 100** out-of-sample /
   live trades on the *currently deployed* config.
2. **Significant improvement (the real bar):** a candidate that, on **walk-forward OOS +
   an untouched lockbox** (prior-year data, never searched), shows **positive expectancy**,
   **profit factor ≥ 1.3**, and **deflated Sharpe > 0** across **≥ 3 pairs** — i.e. a genuine
   edge, not a single-regime / data-mined artifact.

Until then: keep iterating. Each iteration either deploys a validated winner or logs a negative
result and retains the baseline. **Never churn the live lab with an unvalidated config.**

> **Honest framing (keep alive, do not delete):** the project has **NO proven edge**. Win rate
> is a poor optimization target — profitable momentum systems routinely win < 50% with positive
> expectancy; the §18/§30 "win > 55%" bar is itself flagged as wrong (see memory
> `project_maker_fee_meanrev_research`). Condition 2 is the metric that actually matters; 70%
> win rate is included because the user asked for it, but is unlikely to be reachable without a
> structural change. Report progress honestly; do not fabricate an edge to satisfy the bar.

---

## HARD GUARDRAILS (every iteration — non-negotiable)

- **NEVER modify frozen files:** `src/risk/manager.py`, `src/execution/live.py`,
  `src/execution/interface.py`, `src/db/schema.py`, `scripts/*.sh`, `.env`, `CLAUDE.md`.
- **NEVER change leverage** (`.env` / CLAUDE.md §4 — human-gated). **NEVER fund, fetch real
  keys, promote to prod, or go live.** This is paper research only.
- Agent MAY modify: `src/signal/*` (patterns, indicators, detector, regime, memory, sizing),
  `params.json` (values within their declared ranges only), `bots.json`, `scripts/build_*_lab.py`
  and other `scripts/*.py` research harness.
- Every backtest applies the fee + slippage model and uses **walk-forward** (train 60% / test
  40%) — never in-sample only.
- Commit directly to **main**, push, deploy. Never branch.
- CI (`ruff check src/ tests/` + tests) must be green before deploy.

---

## END-TO-END ITERATION PROTOCOL (one pass per firing)

1. **MEASURE** — pull live lab metrics from Postgres for `env='dev'`: trade count, win rate,
   net PnL, profit factor, avg R, close-reason mix (stop-out % / trail % / timeout %),
   exposure. Compare to the baseline + last iteration below.
2. **DIAGNOSE** — what is the dominant failure mode this window (e.g. premature stop-outs,
   fee bleed, regime mismatch, overtrading)? One root cause, not symptoms.
3. **HYPOTHESIZE** — pick ONE concrete candidate change targeting that root cause. Do **web
   research** (`WebSearch`/`WebFetch`) when a new angle is needed — look beyond indicator
   tweaks toward *structural* levers (regime gating, funding-rate, session filtering, holding
   period, instrument). Do NOT re-try anything in the REFUTED LEDGER below without a materially
   new variant.
4. **BACKTEST** — implement the candidate in the research harness and run walk-forward OOS +
   lockbox via `scripts/algo_search.py` / `backtest_*.py` across ≥ 3 pairs. Capture IS vs OOS
   vs lockbox expectancy, profit factor, win rate, deflated Sharpe.
5. **DECIDE:**
   - **Validates** (beats baseline AND clears the lockbox without IS→OOS collapse) → update
     `signal/*` + `params.json` + regenerate `bots.json` via `build_momentum_lab.py`, run CI,
     commit + push, `docker compose up -d --build`, then **reset** (reset_dev.py → wipe
     heartbeats → backfill_history → restart) for a clean slate.
   - **Does not validate** → retain baseline, deploy nothing, log the negative result.
6. **RECORD** — append an iteration entry below (date, hypothesis, result, decision, new best).
   Update the REFUTED LEDGER if an idea was killed.
7. **CHECK STOP** — if a STOPPING CONDITION is met, send Telegram CRITICAL, write a `## STOPPED`
   marker here, and `CronDelete` the loop. Else end the iteration; the cron fires again in ~8h.

---

## BASELINE (set 2026-06-17, before iteration 1)

- Deployed config: momentum lab — 120 bots = 3 strategies (`mom_adx`, `triple_mom`,
  `trend_mom`) × 4 TF (`5m`, `15m`, `1h`, `4h`) × 10 pairs.
- Exit profile: `tp_atr=2.4 / sl_atr=1.5 (R/R 1.6) / max_hold=6 / trail arms +0.5R trails 0.5R
  / max_loss_pct=0.01`. Leverage 20×. MAKER sim on. Portfolio guard ±10%.
- **Live metrics:** 124 trades · **31.5% win** · net **−$3.25** USDT.

## CURRENT BEST

- Same as baseline (no validated improvement yet).

---

## REFUTED LEDGER (do not re-try these without a materially new variant)

- **Single-rule entries** — lose at every TF/asset; param sweep 0/36; algo_search 18 archetypes
  × crypto+forex × 5m/1h/4h/1d = 0 viable.
- **mom_adx / triple_mom @ 4h** — recent-year +EV was a **data-mining / single-regime artifact**;
  cross-year lockbox is NEGATIVE (taker −$19 / maker −$10, IS→OOS < 0).
- **Connors RSI-2 (`rsi2_ct/ct5/raw`)** — did not generalize; a 2-pair fluke.
- **Wave family** (`wave_ride` / `vol_burst` / `wave_flip`) — 0/3 clear §30, ~30% win,
  −$0.18–0.20/trade OOS; wider SL did not lift win rate → no-edge, not tuning.
- **Trailing-close** — lifts win rate + cuts timeouts but still −EV OOS; variance shaper, not edge.
- **Risk-shaping** (sl 1.5 / tp 2.4 / trail 0.5R / max_hold 6 / max_loss 0.01) — cut exposure +
  reshaped variance; current baseline; did NOT create edge (still 31.5% win).
- **Regime-conditional momentum @ 5m** (iter 1) — restricting `mom_adx/triple_mom` to trending or
  volatile regimes does NOT rescue expectancy (0/2 each OOS, maker fees). 5m is dead in every
  regime.

**Levers confirmed real but human-gated / structural (outside agent scope or untried):**
maker fees (confirmed big, already on in sim) · **leverage** (.env/§4, human-only) ·
**funding-rate harvesting** (structural, untried) · instrument class (no broker keys).

---

## ITERATION LOG

<!-- newest first; each firing appends one entry -->

### Iteration 1 — 2026-06-17 (inline, loop start)

- **MEASURE:** 124 live trades · 31.5% win · net −$3.25 · profit factor **0.55** · avg notional
  $26.6 / margin $1.33. Close-reason mix: `stop_loss` 28 trades / **0% win** / −13.3% each /
  **−$4.79** (≈ the entire loss); `timeout` 69 / 50.7% / +$1.86; `trailing_stop` 16 / 25% / −$0.45.
  By TF: 5m=86, 15m=26, 1h=1, **4h=0**. By strategy: `trend_mom` 84 trades / **−$3.03** (≈93% of
  loss), `mom_adx` −$0.29, `triple_mom` +$0.07.
- **DIAGNOSE:** Risk-shaping worked partially (stop-out rate 54%→25%), but the system is still
  losing because activity is concentrated at **5m**, where momentum has no edge. `trend_mom` (the
  permissive 5m strategy) is ~all the loss; `mom_adx`/`triple_mom` are ~breakeven only because
  they trade less.
- **HYPOTHESIZE + BACKTEST:** (a) walk-forward OOS, 5m, maker fees, 4 pairs, `mom_adx/triple_mom/
  mom_align/mom_volexp` × tight/wide → **0/8 clear §30**; best −$0.0054/trade, win 36–41%, expR≈0.
  (b) NEW probe — regime-conditional firing (`--regime trending|volatile`) on `mom_adx/triple_mom`
  → trending **0/2** (best 42% win, −EV), volatile too thin (n≈15, −EV).
- **DECIDE:** No candidate validates. Retain baseline; **no live-lab change** (don't churn on a
  negative result). The drag is structural — the **5m timeframe** (cost floor), across all
  strategies and regimes, with maker fees already applied.
- **STRUCTURAL FLAG (user's call — like leverage):** the data says the clean lever is **TF
  composition** (drop 5m/15m; concentrate on higher TF) — but the user chose to keep all 4 TFs,
  so the loop will not override that. Surface to user; do not silently change.
- **NEXT:** stop re-probing 5m parameter/regime space (exhausted). Next iterations target
  *structural* angles inside agent scope (e.g. an ATR%-of-price volatility floor that lets only
  cost-clearing setups fire; holding-period studies) and **web-research** funding-rate / basis
  ideas. 4h-only is in the refuted ledger — needs a materially new variant, not a re-run.
- **CHECK STOP:** not met (win 31.5% < 70%; no lockbox-validated edge).
