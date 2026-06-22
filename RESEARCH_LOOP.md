# RESEARCH_LOOP — autonomous strategy-evaluation loop

> **Purpose:** every 8 hours, evaluate Kestrel's algorithm + strategy, attempt one
> improvement, and stop only when a real target is hit. This file is the loop's **memory**:
> each firing reads it to know what has been tried, what the current best is, and whether to
> stop. Append to it every iteration. Started 2026-06-17.

---

## MODE: HYPER-SCALP MAINTENANCE (since iter 12, 2026-06-20) — owner directive + CLAUDE.md v2.1

**PURPOSE CHANGE (read CLAUDE.md §6/§13 v2.1):** Kestrel is now officially a HIGH-FREQUENCY
SCALPING FLEET — hundreds of bots, 5m, many markets, maximizing ACTIVITY. The owner was explicit:
"there is no point building a bot if not to do hyper-speed scalping with hundreds of bots; if we
do it slow I can do it myself and won't need you." So the loop's prior instinct — prune to slow /
few-trade / high-TF to "lose less" — is now **WRONG and forbidden**. Find net-of-fee edge WITHIN
the active scalp design; ✗ shrink the fleet or its activity to reduce bleed.

**Authorities (every run may, within agent scope — never touch frozen files EXCEPT CLAUDE.md when
the owner explicitly authorizes it, as on 2026-06-20):**
1. **Improve entries/exits/fees/sizing/pair+pattern selection** — to lift edge WITHOUT cutting
   activity. (bots.json / `build_momentum_lab.py` / `signal/*` / params.json.)
2. **Add new parameters** — within declared `params.json` ranges + full contract.
3. **Add statistical enforcements** — significance/sample gates, suppress a `(pattern,regime,
   session)` cell ONLY if it is a genuine dead-weight loser (`signal/memory.py`).
4. **Add bots / pairs / patterns** — scaling UP activity is encouraged; keep WS feeds shared.

**Cell-viability rule (NARROWED — activity is now a goal):** prune a `(strategy × TF)` cell ONLY if
it is **structurally dead**: either it has **≥ 50 closed trades** AND **net < 0** AND **profit
factor < 1.0** AND no param/fee fix is plausible, OR it **essentially never fires** (a shape pattern
that is dormant for days). Prefer FIXING (maker fees, exit tuning, looser gate) over removing. ✗
prune a cell merely for being "slow" or to make the fleet calmer — that contradicts the purpose.

- **Cadence: every 8h** (re-activated from the daily monitoring pause — the fleet needs active
  upkeep now, not once-a-day watching).
- Each run still: MEASURE → DIAGNOSE → (maintain: prune/param/enforce, or re-validate) → if
  anything deployed, the **FULL ritual** (lint format+check → commit/push main → CI green →
  redeploy → reset_dev+wipe heartbeats+backfill+restart → verify clean) → log a `system` event →
  CHECK STOP. Skip deploy+reset only if the fleet is byte-identical to what's live.
- Still **no proven edge**; maintenance cuts losers, it does not manufacture one. A new STRUCTURAL
  direction (funding-rate) or leverage change stays **§4 human-gated** — flag, don't start.

---

## STOPPING CONDITIONS — stop the loop (delete cron, Telegram CRITICAL) when EITHER holds

1. **OWNER'S TARGET (the stated goal, 2026-06-20):** on the *currently deployed* config,
   rolling win rate **≥ 70%** over **≥ 100** out-of-sample / live trades **AND** average
   **≥ 15% daily return**. This is the owner's explicit bar — hit BOTH and the mission is done.
2. **Significant improvement (the realistic edge bar):** a candidate that, on **walk-forward OOS +
   an untouched lockbox** (prior-year data, never searched), shows **positive expectancy**,
   **profit factor ≥ 1.3**, and **deflated Sharpe > 0** across **≥ 3 pairs** — i.e. a genuine
   edge, not a single-regime / data-mined artifact.

Until then: keep iterating. Each iteration either deploys a validated winner or logs a negative
result and retains the baseline. **Never churn the live lab with an unvalidated config.**

> **Honest framing (keep alive, ✗ delete — owner is told this repeatedly):** the project has **NO
> proven edge**, and **15%/day is not realistically achievable** — it compounds to ~66× in a month
> and ~10^22 in a year; the best *real, out-of-sample* result this project has ever produced is a
> fraction of a percent per day, and even that died in the lockbox. 70% win rate is likewise a poor
> optimization target (profitable momentum systems routinely win < 50% with positive expectancy;
> the §18/§30 "win > 55%" bar is itself flagged wrong — see `project_maker_fee_meanrev_research`).
> Condition 1 is recorded because it is the OWNER'S stated target and he owns the decision;
> Condition 2 is the metric that actually matters and is the only realistic path to a real stop.
> The loop's job is to maximize genuine risk-adjusted return WITHIN the hyper-scalp design (CLAUDE.md
> v2.1) and report progress **honestly** — ✗ fabricate an edge or a return figure to satisfy a bar.

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
- CI must be green before deploy. CI runs **`ruff format --check src/ tests/`** AND
  **`ruff check src/ tests/`** AND the test suite — run **all three** locally (the format check
  is easy to forget and will red the build; run `ruff format src/ tests/` to fix).

## STANDING PREFERENCES (the user's recurring asks — honor EVERY iteration)

These are the things the user repeats and gets frustrated when skipped. The full end-to-end
ritual below is non-optional — "evaluate" alone is not a finished iteration.

1. **Evaluate the algorithm & strategy** every firing (MEASURE + DIAGNOSE + BACKTEST). Use web
   browsing when a fresh angle is warranted.
2. **Apply a new bot / update** every firing — rotate the experimental cohort to the current
   best candidate so there is always something new and visible (skip only if it is byte-identical
   to what is already live).
3. **Reset everything** after deploying anything new — `feedback_reset_after_new_algorithm`:
   wipe trades/signals/events/trade_context/pattern_memory + heartbeats, **KEEP candles**,
   backfill, restart. A deploy without a reset is the #1 mistake — the slate must visibly zero.
4. **Ensure CI passes** — lint to CI scope, push, and confirm `gh` shows `completed/success`
   before declaring done.
5. **Commit & push to `main`** directly — never a branch (`feedback_no_branches_commit_to_main`).
6. **Redeploy** — rebuild (code) or restart (config) and confirm the container is healthy.
7. **Make it visible in Grafana** — the reset zeroes the panels and the cohort rotates, so the
   dashboard changes; also log the `system` event marker.
8. **Be honest about edge** — the cohort/visibility is a live testbed, not a profit claim; the
   project still has no proven edge. Do not fake an edge to satisfy the bar.
9. **Always FULL-RESET each firing** (owner confirmed iter 21: "always remember to do it") — but
   the 8h cron is the CHECK-IN rhythm, NOT the strategy DECISION window. **Never apply or remove a
   strategy off an 8h read.** A NEW algo deploys only if it survives the untouched prior-year LOCKBOX
   (+expectancy, ≥3 pairs) — that gate, not elapsed time, is the fluke-killer. An ALREADY-DEPLOYED
   live lead is judged on TRADE COUNT not clock (~30+ to read, ~100+ to trust; at 1h that is weeks).
   The owner's "reset except the offer and bid data" stands: KEEP both `candles` AND the
   `microstructure` (bid/offer) table on every reset.

---

## END-TO-END ITERATION PROTOCOL (one pass per firing — DO ALL STEPS, IN ORDER)

This is the user's required ritual. Earlier failures were skipping the back half (no reset, no
CI-verify). **Never stop after "evaluate" — the iteration is only DONE after deploy + reset are
verified green.** The whole point is that Grafana visibly changes every iteration.

1. **MEASURE** — pull live `env='dev'` metrics: trade count, win rate, net PnL, profit factor,
   avg R, close-reason mix (stop-out/trail/timeout %), exposure, per-TF + per-strategy split
   (especially `exp_*` cohort rows). Re-baseline from the CURRENT slate (a prior reset zeroes it).
2. **DIAGNOSE** — the ONE dominant failure mode this window (premature stop-outs, fee bleed,
   regime mismatch, overtrading), root cause not symptom.
3. **HYPOTHESIZE** — ONE concrete candidate targeting that root cause. **Web research**
   (`WebSearch`/`WebFetch`) when a new angle helps — favor *structural* levers (regime gating,
   funding-rate/basis, session filtering, holding period, instrument) over indicator tweaks.
   Do NOT re-try the REFUTED LEDGER without a materially new variant.
4. **BACKTEST** — run walk-forward OOS + lockbox via `scripts/algo_search.py` / `backtest_*.py`
   across ≥ 3 pairs (in-container: `docker compose exec -T kestrel python3 scripts/...`). Capture
   IS vs OOS vs lockbox expectancy, profit factor, win rate, deflated Sharpe. **This backtest —
   not the live slate — is the edge arbiter and the stop-condition evidence** (live resets each
   deploy, so it can't accumulate 100 trades; the OOS sample does).
5. **APPLY (always ship something visible)** — write `exp_candidate.json` with this iteration's
   **best candidate** and rotate the cohort: `python3 scripts/build_exp_cohort.py`. Promote to
   the **baseline** too (`build_momentum_lab.py`/`params.json`) **only if it fully
   validates** (beats baseline AND clears the lockbox without IS→OOS collapse). If the best
   candidate is *identical* to what's already deployed, say so in the log and skip the
   deploy+reset (let the slate accumulate) — otherwise proceed.
   **DEDUP GUARD — RUN THIS EVERY FIRING, don't make the same mistakes:** before deploying, run
   `python3 scripts/bot_registry.py check bots.json`. The registry lives in `bot_registry/` (one
   JSON shard per instrument + `_index.json`; ~1.3k+ configs reconstructed from the full git
   history of bots.json). Every config has a stable fingerprint over its BEHAVIOUR
   (pair/timeframes/patterns/params, NOT the bot_id label). The check prints NEW vs SEEN and exits
   1 if any are SEEN. A SEEN config means that exact bot already ran in a past fleet — re-deploying
   it just re-measures a known result. **Each iteration's value comes from the NEW count: prefer
   genuinely new hypotheses (new patterns / new param regimes), not new copies of a refuted one.**
   It is OK to retain a few SEEN configs as deliberate CONTROLS (say so in the log). After a deploy
   lands, run `python3 scripts/bot_registry.py build` to fold the new snapshot into `bot_registry/`
   and commit the shards alongside.
6. **LINT** — run BOTH (CI runs both; `feedback_local_lint_must_match_ci`):
   `ruff format --check src/ tests/` **and** `ruff check src/ tests/`. Fix with
   `ruff format src/ tests/` before continuing. (The format check is the easy-to-forget half.)
7. **COMMIT + PUSH** — commit DIRECTLY to `main`, never a branch (`feedback_no_branches_commit_to_main`);
   `git push origin main`. Then **verify CI is green** (`gh run list --limit 1` until
   `completed/success`). Do not call the iteration done on a red/in-progress CI.
8. **REDEPLOY** — code change → `docker compose up -d --build kestrel`; config-only
   (bots.json/params.json) → `docker compose restart kestrel`. Confirm container `(healthy)`.
9. **FULL RESET (the ritual I kept missing — `feedback_reset_after_new_algorithm`)** — whenever a
   new/changed config was deployed this iteration, IN THIS ORDER:
   `reset_dev.py --yes` (wipe trades/signals/events/trade_context/pattern_memory) →
   `backfill_history.py --source gate` → `docker compose up -d --build`/`restart` →
   **THEN `DELETE FROM heartbeats;` (full wipe, AFTER the restart — this is the only safe spot).**
   **KEEP candles.** Wait ~40s, then verify clean: `trades=0`, heartbeats == intended fleet size
   (e.g. 120 = the diversity fleet), all expected patterns present, `errors=0`.
   *Heartbeat-orphan lesson (bit me twice):* the OLD container keeps writing heartbeats for its
   bots until the restart actually kills it. So a wipe BEFORE restart is useless if any long step
   (backfill takes ~3 min) runs between the wipe and the restart — the dropped bots' ids repopulate
   and linger as phantom "live" rows. And a post-restart `ts < now-90s` age-threshold delete misses
   them too (they're only seconds old). Only a FULL `DELETE FROM heartbeats` AFTER the restart is
   safe: the old fleet is dead, so just the new fleet repopulates within 30s. (Skip ONLY when step 5
   deployed nothing new.)
10. **RECORD** — append an iteration entry below (date, hypothesis, backtest result, what was
    deployed, reset done?, new best) + update the REFUTED LEDGER if an idea died. **Write a
    `system` event** so it shows in Grafana (Recent Events / Events-by-Category):
    ```sql
    INSERT INTO events (bot_id, session_id, env, ts, level, category, message, payload)
    VALUES ('dev-research-loop','research-loop','dev', <now_ms>, 'INFO','system',
            'research_loop iter <N> — <one-line result + deployed + reset>',
            '{"event":"research_loop_iteration","iteration":<N>,"deployed":<bool>,"reset":<bool>,...}'::jsonb);
    ```
    (via `docker compose exec -T postgres psql -U kestrel -d kestrel -c "..."`).
11. **CHECK STOP** — if a STOPPING CONDITION holds, send Telegram CRITICAL, write a `## STOPPED`
    marker here, and `CronDelete` the loop. Else end; the cron fires again in ~8h.

> **Self-check before declaring the iteration done (the user WILL ask):** evaluated ✓ · applied
> a change ✓ · lint+CI green ✓ · committed+pushed to main ✓ · redeployed healthy ✓ · full reset
> verified clean ✓ · system event logged ✓. If any box is unchecked, the iteration is NOT done.

---

## Experimental cohort (the `exp_*` bots — visible live A/B)

A small slice of bots (~16) running the loop's current best candidate, alongside the untouched
120 baseline. They are **env=dev / simulation** (safe execution path) and carry a strategy
label starting with `exp_`, so they appear as their own rows in the dashboard's per-strategy /
leaderboard / per-bot panels — "watch them rise and fall." The 120 baseline configs are never
touched by the cohort.

**Rotate the cohort each iteration** (params-only candidate → no rebuild; new `signal/*` code →
rebuild):
```
1. write exp_candidate.json   # {iteration, note, arms:[{label:"exp_*", timeframe, pairs,
                              #   strategies:[{name,patterns}], params:{...}}]}
2. python3 scripts/build_exp_cohort.py        # strips old exp_*, merges new cohort into bots.json
3. validate it loads:  docker compose exec -T kestrel python3 -c \
     "import os;from src.config import *;load_bot_configs('bots.json',AppConfig.from_mapping(os.environ),load_params('params.json'))"
4. if a prior cohort had trades:  docker compose exec -T kestrel python3 scripts/reset_exp.py --yes
5. docker compose up -d --build kestrel   # (restart suffices if no signal/* change)
```
`build_exp_cohort.py` keeps baseline entries verbatim; `reset_exp.py` wipes ONLY `exp_*` rows
(baseline + candles + shared pattern_memory untouched). **Cohort allocation is honest, not a
hidden edge** — keep the no-edge framing; the cohort is a live testbed, not a profit claim.

## CURRENT COHORT

- **RETIRED iter 6 (2026-06-19).** The `exp_h1tp` 1h-momentum cohort is now a subset of the
  diverse-120 baseline (which includes 1h mom_adx/triple_mom), so it was folded away — the whole
  120-bot fleet IS the experiment. The cohort tooling (`build_exp_cohort.py`/`reset_exp.py`) stays
  available for a future genuinely-distinct arm.
- ~~Iter 4: `exp_h1tp`~~; ~~Iter 3: `exp_tod` seasonal~~ refuted; ~~Iter 2: `exp_h1run`~~; ~~Iter 1~~.

## BASELINE (diversity fleet, set iter 6 2026-06-19)

- **Iter 6 PIVOT to hypothesis DIVERSITY** (user: "40 bots not effective, we didn't learn more").
  The iter-5 40-bot fleet was wide on instruments but tested only 2 ideas (mom_adx, triple_mom —
  both refuted momentum). New baseline = **120 bots = 6 patterns × 2 TF (1h/4h) × 10 pairs**:
  `mom_adx` + `triple_mom` (VALIDATED CONTROLS, registry=SEEN) + `impulse_retracement`,
  `compression_breakout`, `anomaly_fade`, `wick_rejection` (the latter three NEVER deployed as
  bots → 80 registry-NEW hypotheses). This is "120 done productively" — NOT the old redundant 120
  (3 momentum patterns × 4 TF, half sub-cost-floor). `build_momentum_lab.py`.
- Dedup guard at deploy: **80 NEW + 40 SEEN** (the SEEN are the deliberate momentum controls).
- ~~Iter 5: 40 bots (2 momentum × 2 TF × 10) + 8 cohort~~ — too narrow, pivoted to diversity.
- Exit profile (UNIFORM across all 6 patterns, isolates signal not exit tuning): `tp_atr=2.4 /
  sl_atr=1.5 (R/R 1.6) / max_hold=6 / trail arms +0.5R trails 0.5R / max_loss_pct=0.01`.
  Pattern-shape params (wick_ratio_min, compression_factor, ...) from params.json. Leverage 20×.
  MAKER sim on. Portfolio guard ±10%.
- **Live metrics (PRE-reset diagnostic that motivated the cohort):** 124 trades · **31.5% win**
  · net **−$3.25** · profit factor 0.55.
- **FULL RESET performed 2026-06-17** after the cohort deploy (standing preference
  `feedback_reset_after_new_algorithm`): dev slate wiped (124 trades / 203 signals / 7924 events
  / 57k trade_context / heartbeats cleared, **candles kept**), all 136 bots relaunched clean.
  → Each MEASURE step now **re-baselines from the fresh slate** — do not expect the 124-trade
  numbers above; they are history. The clean-slate evaluation starts here.

## CURRENT BEST

- **LEAD (iter 18): `macd_cross` (trend-aligned MACD signal cross) @ 1h, maker.** The project's
  FIRST signal +EV in BOTH the recent year AND the untouched prior-year LOCKBOX (recent expR +0.13 /
  lockbox +0.17, R/R ~1.2, 51–52% win, IS→OOS positive both; corroborated by sma_cross_9_21 +0.17/
  +0.12). NOT yet a confirmed stop-#2 edge — modest, wins <55%, at 1h (not the 5m mandate), and clean
  "both-eras" pairs are only ~2–3 (DOGE/ADA firm). **Status: live FORWARD-TEST** — deployed as a
  6-bot 1h `macd_cross` cohort alongside the 5m fleet (§13 research arm). Accumulate live + OOS
  evidence before any promotion. ✗ overclaim as edge.
- **LEAD #2 (iter 22): `macd_rsi` (raw MACD signal cross + RSI-14 confirmation across 50) @ 1h,
  maker.** The SECOND cross-era +EV signal: recent expR +0.06..+0.09, lockbox **+0.12**, R/R 1.52,
  lockbox-positive on **5/6 pairs** (same breadth as macd_cross, BTC the lone loser) with ~50% MORE
  trades. The RSI filter rescues the raw cross (data-mined alone). **Status: live FORWARD-TEST** —
  6-bot 1h cohort (medium exit) alongside macd_cross + the 5m fleet. Same modest-lead caveats (win
  <50%, 1h not 5m). NOT a confirmed edge. Note `macd_rsi_ct` (zero-aligned + RSI) == `macd_cross_ct`
  (RSI redundant there) — only the RAW-cross variant adds information.
- The 5m hyper-scalp baseline is unchanged and remains −EV (no edge at 5m for any indicator incl.
  MACD — the cost floor dominates short TF). The earlier seasonality lead stays REFUTED (iter 4).
- **Note:** the 5m search is exhausted; the NEW frontier is INDICATOR strategies at 1h (owner opened
  macd/rsi/MA). macd_cross is the first hit. Next: validate per-pair deflated-Sharpe / PF, and test
  more indicator confluences (RSI+MACD, MA-cross variants) at 1h.

---

## REFUTED LEDGER (do not re-try these without a materially new variant)

- **Single-rule entries** — lose at every TF/asset; param sweep 0/36; algo_search 18 archetypes
  × crypto+forex × 5m/1h/4h/1d = 0 viable.
- **mom_adx / triple_mom @ 4h** — recent-year +EV was a **data-mining / single-regime artifact**;
  cross-year lockbox is NEGATIVE (taker −$19 / maker −$10, IS→OOS < 0).
- **Connors RSI-2 (`rsi2_ct/ct5/raw`)** — did not generalize; a 2-pair fluke.
- **Stochastic oscillator (`stoch_revert`, `stoch_ct`)** (iter 22) — 1h, maker, recent + lockbox.
  `stoch_revert` (mean-revert) ~breakeven/negative both eras → REFUTED. `stoch_ct` (trend-aligned)
  is +EV both eras but MARGINAL (lockbox expR only +0.06, IS→OOS slightly negative) — below the
  deploy bar; not promoted. A new indicator family, but no edge (cost floor still dominates).
- **RSI confluence on the TREND-ALIGNED MACD cross (`macd_rsi_ct`)** (iter 22) — adding an RSI>50/<50
  filter to `macd_cross_ct` is REDUNDANT: the zero-line condition (ml>0) already implies RSI>50, so
  `macd_rsi_ct` == `macd_cross_ct` byte-for-byte. RSI only adds information on the RAW (non-zero-
  aligned) cross — that variant is the deployed `macd_rsi`. Don't re-test RSI on the _ct variant.
- **ADX trend-strength confluence on the MACD family (`macd_adx*`, `macd_rsi_adx*`)** (iter 23) —
  gating the MACD/RSI cross on an ADX floor (≥20/≥25) does NOT improve the leads out-of-era. The best
  cell `macd_rsi_adx25`/medium looked strongest in the recent year (expR +0.16) but COLLAPSED to +0.02
  (breakeven) in the lockbox — a recent-year data-mining artifact. All ADX variants are worse cross-era
  than plain macd_cross/macd_rsi; the gate cut ~⅔ of trades for no robust gain. adx20 is too loose
  (−EV recent). Don't re-add an ADX entry gate to the MACD family as an edge lever.
- **Wave family** (`wave_ride` / `vol_burst` / `wave_flip`) — 0/3 clear §30, ~30% win,
  −$0.18–0.20/trade OOS; wider SL did not lift win rate → no-edge, not tuning.
- **Trailing-close** — lifts win rate + cuts timeouts but still −EV OOS; variance shaper, not edge.
- **Risk-shaping** (sl 1.5 / tp 2.4 / trail 0.5R / max_hold 6 / max_loss 0.01) — cut exposure +
  reshaped variance; current baseline; did NOT create edge (still 31.5% win).
- **Regime-conditional momentum @ 5m** (iter 1) — restricting `mom_adx/triple_mom` to trending or
  volatile regimes does NOT rescue expectancy (0/2 each OOS, maker fees). 5m is dead in every
  regime.
- **High-ADX entry gate @ 5m** (iter 14) — raising the trend-strength bar (`--regime trending` /
  higher `adx_strong_min`) on `mom_adx/triple_mom` cuts trade count ~15% but per-trade economics
  are flat (≈ −$0.007/trade, maker, both configs), win% slightly LOWER, 0/4 clear §30. "Fewer
  trades, same −EV" → shrink-activity-with-no-benefit. Don't re-try as an edge lever.
- **Widening the 5m stop (sl 0.9→1.3 ATR)** (iter 13 deployed, iter 14 refuted) — does NOT cut the
  stop-out rate (share of closes unchanged ~36–37%) or lift win rate; just makes each stop a bigger
  % loss. Exit-distance tuning reshapes variance, never creates edge (same as Trailing/Risk-shaping).
- **Lockbox validation is DATA-INFEASIBLE at 5m** (iter 14, methodology limit, not a strategy) — no
  exchange (gate/kraken/okx) retains year-old 5m candles, so stop-condition #2 (prior-year untouched
  lockbox) cannot be evidenced for the 5m fleet by backtest. The lockbox that refuted 1h/4h momentum
  has no 5m data. The 5m fleet can only accumulate LIVE paper; recent-window backtests lack an
  out-of-era check (data-mining-prone). Tension with the owner's 5m hyper-scalp mandate — flagged.
- **1h momentum** (iter 2) — the LEAST-BAD TF but still NOT an edge: `mom_adx`/wide is recent-year
  OOS +EV (n=747, win 45.6%, net +$5.03, expR +0.10, R/R 1.37, maker) but the **lockbox
  (prior year) is breakeven/negative** (win 43.0%, net −$0.50, expR +0.02); tight variants clearly
  −EV both windows. Same "wins recent / dies prior" data-mining signature as 4h → not promoted.
  Kept as the cohort testbed only.

- **Time-of-day / overnight seasonality** (iter 3 found, iter 4 refuted) — the gross UTC-window
  drift (≈18:00–00:00, hold 4h) is statistically real (pooled non-overlap t≈4.8 recent / 2.2
  lockbox) but the **effect size (~0.05% gross) is below maker cost**: pooled net-maker is
  NEGATIVE in the lockbox (−0.008%), and only 1 of 6 pairs (DOGE) is robust across both windows
  (1-in-6 luck). The iter-3 "8/120 net-maker survivors" was a multiple-testing/threshold artifact.
  Not a net-of-cost edge. `session_seasonal` pattern stays registered (tested) but undeployed.

> **MILESTONE (iter 4): the directional + seasonal entry search space is EXHAUSTED.** Every entry
> idea testable in this per-bot directional architecture — single-rule, confluence-momentum at
> 5m/15m/1h/4h, Connors RSI-2, wave/flip, regime-conditional, time-of-day seasonality — has been
> refuted across recent + lockbox. The remaining levers that could create a net-of-cost edge are
> **structural** (funding-rate / basis harvesting — needs a different non-directional mechanism +
> perp funding data, not just a new pattern) or **human-gated** (leverage, .env/§4). The loop
> should escalate this to the user rather than keep manufacturing marginal directional variants.

**Levers confirmed real but human-gated / structural (outside agent scope or untried):**
maker fees (confirmed big, already on in sim) · **leverage** (.env/§4, human-only) ·
**funding-rate harvesting** (structural, untried) · instrument class (no broker keys).

---

## ITERATION LOG

<!-- newest first; each firing appends one entry -->

### Iteration 23 — 2026-06-22 (ACTIVE SEARCH: ADX trend-strength confluence on the MACD family → REFUTED by lockbox; justified HOLD)

- **MEASURE:** post-iter-22 slate, 260 closes, the known −EV 5m book (trend_momentum 28.8% −$4.04,
  mom_adx −$1.25, triple_mom −$0.89). Both 1h MACD cohorts (macd_cross + the iter-22 macd_rsi) at
  **0 trades** — only a few 1h candles have closed since the iter-22 backfill and a qualifying cross
  is rare; expected, not dark (both have 720+ candles/bot). Fleet healthy: 250 hb, 0 errors/1h,
  postgres 12.5% / kestrel 17% of 2g.
- **HYPOTHESIS (iter-18's flagged next step):** the MACD crosses that drag macd_cross/macd_rsi win
  <50% are the ones firing in CHOP — so gate the cross on ADX (real trend strength). Built 4 harness
  algos: `macd_adx20/25` (MACD cross + ADX floor) and `macd_rsi_adx20/25` (+ RSI-50 confirm), using
  the stored `adx` column. 1h, maker, 6 pairs, walk-forward OOS + LOCKBOX.
- **RESULT — REFUTED (data-mining signature):** `macd_rsi_adx25`/medium was the BEST *recent* cell
  (expR **+0.16**, avg +$0.011/trade, IS→OOS +0.017 — looked great) but **COLLAPSES to expR +0.02
  (breakeven, 38% win) in the lockbox.** Every ADX variant is WORSE cross-era than the plain leads:
  lockbox expR — macd_cross_ct +0.16/+0.14, macd_rsi +0.11, vs macd_rsi_adx25 +0.02/+0.08,
  macd_adx25 +0.04/+0.05. The ADX≥25 gate cut trade count ~⅔ (539→181) WITHOUT adding robust edge —
  the recent +0.16 was over-fit to recent-year trends. adx20 too loose (−EV recent). The lockbox
  caught exactly what it exists to catch.
- **DECISION — JUSTIFIED HOLD (no deploy, no reset).** Nothing beats the deployed macd_cross/macd_rsi
  cohorts cross-era; deploying an ADX variant would reset the just-deployed (iter-22) macd_rsi cohort
  before it logged a single trade, to chase a data-mined artifact = churn (ritual 3b). The harness
  additions (the 4 algos) are committed for reproducibility; no signal/patterns.py, bots.json, or
  fleet change → no reset (slate byte-identical to live).
- **HONEST:** the MACD family (macd_cross/macd_rsi) remains the only cross-era +EV signal set; ADX
  filtering does not improve it out-of-era. Still a forward-test lead, NOT a confirmed edge.
- **STOP CHECK:** NOT met. Continue — let the two 1h cohorts accumulate live trades (weeks at 1h).

### Iteration 22 — 2026-06-22 (ACTIVE STRATEGY SEARCH: stochastic + RSI/MACD confluence → deployed macd_rsi, the 2nd cross-era +EV 1h signal)

- **MEASURE:** post-iter-21 slate, 38 closes, all −EV (mom_adx −$0.52, trend_momentum 0% win
  −$0.92, triple_mom −$0.32 — the known 5m no-edge book). MACD 1h cohort 0 trades (rare cross,
  expected). Fleet healthy: 244 heartbeats, postgres 13% / kestrel 16% of 2g, 0 errors/1h. The 5m
  live slate warrants no deploy — so this firing's work was the BACKTEST search (owner directive #2,
  independent of the live slate).
- **SEARCH (built + backtested):** added 4 new research algos to algo_search.py — `stoch_revert`
  (stochastic %K/%D mean-revert), `stoch_ct` (trend-aligned stoch), `macd_rsi` (raw MACD signal
  cross + RSI-14 confirmation across 50), `macd_rsi_ct` (zero-aligned + RSI). 1h, maker, 6 pairs,
  walk-forward OOS + LOCKBOX.
- **RESULT:**
  - **`macd_rsi` is the winner — +EV in BOTH eras** (recent expR +0.06..+0.09, lockbox **+0.12**,
    R/R 1.52, medium exit), lockbox-positive on **5/6 pairs** (ETH/SOL/DOGE/XRP/ADA; only BTC
    negative — SAME breadth as the deployed macd_cross), with **~50% MORE trades** (90–102/pair vs
    53–65). The RSI filter RESCUES the raw MACD cross (which alone was data-mined +recent/−lockbox).
  - **`macd_rsi_ct` ≡ `macd_cross_ct`** (byte-identical leaderboard rows) — the RSI>50 filter is
    REDUNDANT for the zero-line-aligned variant (ml>0 already implies RSI>50). Not deployed.
  - **`stoch_ct`** marginal (+EV both eras but expR only +0.06 lockbox, IS→OOS slightly negative) —
    not deploy-grade. **`stoch_revert`** ~breakeven/negative — REFUTED.
- **DEPLOYED `macd_rsi`** as a live pattern (signal/patterns.py detect_macd_rsi + SELF_DIRECTING;
  reuses macd_* params; RSI-50 centerline is definitional like macd_cross's zero line; unit tests
  incl. RSI-blocks-the-cross + missing-RSI) and a **6-bot 1h macd_rsi cohort** (medium exit
  tp2.0/sl1.0/hold6 — its best bracket) ALONGSIDE the 5m fleet + macd_cross cohort (§13 research
  arm). Fleet 244→**250**. Dedup: **6 NEW** + 244 SEEN (existing fleet retained as controls).
  Leverage UNCHANGED 20×.
- **SHIP (full ritual):** ruff format+check clean · **mypy src/ clean (CI parity)** · patterns suite
  57 green + promote_to_staging _EXIT-sync green · committed+pushed main `612459c` · **CI green** ·
  rebuilt image (code change) · FULL RESET (stop → wipe dev trades/signals/events/trade_context/
  pattern_memory, **KEEP candles + microstructure** per owner standing rule → start new image →
  **backfilled 720 1h candles × 6 NEW macd_rsi bot_ids** (iter-20 lesson: new bot_ids start blind)
  → DELETE heartbeats after restart). VERIFIED clean: 250 heartbeats, dev trades reset, 0 errors,
  postgres 13%; cohort LIVE via the exact daemon path (window=120, macd_computes=True, rsi populated
  54/55/47 — NOT dark).
- **HONEST CAVEAT:** macd_rsi is a forward-test LEAD like macd_cross — modest (win <50%), at 1h NOT
  the 5m mandate, BTC negative, PF/deflated-Sharpe borderline. It clears the SAME deploy gate that
  justified macd_cross (lockbox +EV, ≥3 pairs) but is NOT a confirmed stop-#2 edge. ✗ overclaim.
- **STOP CHECK:** NOT met (no lockbox-confirmed PF≥1.3/deflated-Sharpe>0 edge; 5m −EV; cohorts are
  forward-test leads). Continue — let the two 1h cohorts accumulate live trades (weeks at 1h).

### Iteration 21 — 2026-06-22 (host OOM-reboot recovery + owner-directed FULL RESET keeping bid/offer data; cadence-vs-decision-window discipline codified)

- **TRIGGER:** owner OOM-rebooted the host, then asked three things: (1) "does the loop still
  work?", (2) "I don't see any new algorithm/strategy applied or removed, and the data is not
  reset", (3) "reset — except the offer and bid data." Followed by: "do you need longer before
  deciding to apply a new algo/strat?" (owner likes the 8h rhythm: "I can see it's working good by
  8-hour basis").
- **REBOOT RECOVERY (verified, no fault):** the session-only cron `13177ba9` (0 0,8,16) SURVIVED the
  reboot (CronList shows it) and all containers auto-restarted ~1h prior (restart: unless-stopped).
  So BOTH the loop schedule and the fleet self-recovered — nothing was lost. **Codified rule: a host
  reboot does not kill the loop; verify with `CronList` + `docker compose ps` before assuming damage.**
- **MEASURE (pre-reset, the slate the owner saw):** 5m fleet 321 closed · 25.2% win · −$13.79 (same
  −EV book). **MACD 1h cohort: 0 trades** — confirmed NOT dark this time (720 1h candles/bot present,
  survived the reboot since candles persist in pg) — just no qualifying trend-aligned cross yet
  (≈13 1h candles since iter-20 backfill; a cross that also clears volume/regime/risk is rare). Live,
  waiting. Microstructure recorder healthy (22.6k rows, 6 pairs, 3s fresh).
- **"No new algo applied/removed" is CORRECT, not a miss:** since iter-18 the loop has been
  forward-testing the MACD 1h lead + monitoring; nothing has cleared stop-cond #2, so nothing should
  be added or removed. Manufacturing a deploy to look busy would be theater (honesty pref #8). Said so.
- **OWNER-DIRECTED FULL RESET (microstructure KEPT):** stop kestrel → wipe dev trades/signals/events/
  trade_context/pattern_memory → restart → `DELETE FROM heartbeats`. **KEPT `candles` (565k) AND the
  `microstructure` bid/offer table (22.6k)** per the owner's "reset except the offer and bid data."
  Verified clean: trades/signals/trade_context/pattern_memory = 0, daemon `(healthy)`, recorder
  untouched. Candles kept means the MACD cohort stays live (no re-backfill needed).
- **CADENCE vs DECISION-WINDOW (the owner's question, answered + codified in STANDING PREFERENCES):**
  the 8h cron is the CHECK-IN rhythm, NOT the strategy DECISION window. Two clocks: (1) applying a
  NEW algo can happen in ONE iteration, but ONLY if it clears the untouched prior-year LOCKBOX with
  +expectancy across ≥3 pairs — that gate (not elapsed time) blocks an 8h data-mining fluke (cf. the
  "4h momentum" that looked +EV recent-year and DIED in lockbox); (2) judging an ALREADY-DEPLOYED
  live lead (the MACD 1h cohort) needs a real TRADE sample — ~30+ closed trades to read, ~100+ to
  trust — which at 1h with rare crosses is WEEKS. So: NEVER apply/remove a strategy off an 8h read;
  new deploys must survive the lockbox, deployed leads must accumulate enough trades. Yes — longer.
- **STOP CHECK:** NOT met (5m −EV; MACD cohort no trades yet; no lockbox-confirmed edge). Continue;
  next firing 08:00 UTC.

### Iteration 20 — 2026-06-21 (BUG FIX: the iter-18 MACD cohort was SILENTLY DARK — never backfilled → fixed)

- **MEASURE:** 244 live, err8h=**0** (iter-19 fix held, swap recovered). 5m fleet: 242 closed, 26.9%
  win, −$12.14 (same −EV). Recorder banking well (20,514 rows / 11.4h). **MACD cohort: 0 trades, 0
  signals after ~10h** — investigated (NOT "just early").
- **DIAGNOSE (deep, read-first):** the pattern WORKS (simulated 2–6 fires/120-candles per pair, incl.
  a DOGE cross 3.6h ago at the exact 120-candle daemon window) — yet 0 live signals. Root cause:
  `db.load_recent_candles` filters **`WHERE bot_id = $1`** (candles are stored per-bot, UNIQUE
  (bot_id,pair,tf,ts)). The 6 cohort bot_ids are NEW and were **never backfilled** — I skipped the
  backfill in the iter-18 deploy because the 5m candles looked "fresh," but new bot_ids have ZERO
  candles under their own id. So each cohort bot saw only the ~6 1h candles it had written since
  startup — far below MACD's 36 → `detect_macd_cross` returned None (insufficient data) every close,
  silently (pattern-stage rejections only write an event, not a signal row → invisible in `signals`).
- **FIX (operational, no code change):** backfilled 720 1h candles per cohort bot_id
  (`backfill_history.py --bots <cohort-only>`). VERIFIED via the exact daemon path
  (`load_recent_candles(bot_id,pair,'1h',120)` → 120 candles, `macd_computes=True`, `fires_now=None`
  = no cross right now, correct). No restart needed — `_process_candle` reloads the window from DB
  each close, so the next 1h cross will fire. Cohort is now genuinely live.
- **LESSON (codified):** a deploy that introduces NEW bot_ids (new cohort/strategy) MUST run
  `backfill_history.py` even if existing candles look fresh — candles are per-bot_id, so a new bot
  starts blind until backfilled. "candles kept" across a reset only covers EXISTING bot_ids. The
  standard reset ritual's backfill step (on full bots.json) covers this; iter-18 erred by SKIPPING it.
- **NO NEW STRATEGY:** fixing the dark cohort (the active experiment) was the priority; deploying
  another while this one wasn't even running would be churn. 5m slate kept (legit), no reset needed
  (data-population fix, not a new algorithm).
- **STOP CHECK:** NOT met (5m −EV; macd cohort now live but no trades yet; no lockbox-confirmed edge).
  Continue — the cohort should produce its first real forward-test trades within ~1–2 days.

### Iteration 19 — 2026-06-21 (INFRA FIX: candle_processor_error flood from a reset-without-restart; clean reset; MACD cohort awaiting 1h data)

- **MEASURE:** 244 live, recorder banking well (9,312 rows, 6 pairs, 3s fresh). 5m fleet: 69 closed,
  24.6% win, −$3.63 (trend_momentum −$2.60 worst, others small −; same −EV book). **macd_cross
  cohort: 0 trades / 0 signals** — only ~1–2 1h candles have closed since the iter-18 reset, and a
  MACD cross is infrequent, so 0 is expected this early (NOT a fault — verified below).
- **DOMINANT SIGNAL — `candle_processor_error` flood: 868/8h, ONGOING ~2.8/min** (42 last 15min). NOT
  the new MACD pattern (read the payload, iter-5 protocol): it's a **trade_context FK violation** —
  `Key (trade_id)=(20xx) is not present in table "trades"` — on 5m fleet bots (PEPE/UNI/ADA).
- **ROOT CAUSE:** the iter-18 rogue-daemon cleanup ran `reset_dev` (wiping `trades`) **without
  restarting the kestrel daemon**. The daemon kept in-memory open-position refs to the deleted trades
  and tried to write the `trade_context` "during"-window row every candle → FK violation each tick,
  forever. The reset ritual REQUIRES the restart precisely to drop that stale state; I skipped it in
  the hurry to clear the rogue-daemon contamination.
- **FIX (proper reset, the step skipped before):** `reset_dev` → **`docker compose restart kestrel`**
  (daemon reconciles to the empty trades table, no ghost refs) → `DELETE heartbeats` after restart.
  VERIFIED: candle_processor_error last 60s = **0** (flood stopped); 244 heartbeats; trades=0;
  recorder unaffected. This also gives the macd_cross cohort + 5m fleet a genuinely clean slate.
- **NO NEW STRATEGY THIS FIRING (justified):** the macd_cross 1h cohort is the active experiment and
  has not yet had data (needs 1h closes with a cross); deploying ANOTHER indicator now would be churn
  before the last one produced a single result. Recorder accumulating toward a future microstructure
  test. So: fix the fault, clean the slate, let macd + the recorder run. Not a HOLD-for-nothing — a
  real infra fault was root-caused and fixed.
- **LESSON (codified):** `reset_dev` MUST be paired with a daemon restart, or orphaned open positions
  flood candle_processor_error with trade_context FK violations. Symptom to recognize next time.
- **STOP CHECK:** NOT met (5m −EV; macd cohort no data yet; no lockbox-confirmed edge). Continue.

### Iteration 18 — 2026-06-21 (owner-directed STRATEGY SEARCH — MACD found +EV cross-era at 1h → deployed 1h macd_cross cohort)

- **TRIGGER:** owner — "your job is to find algorithm and strategy; permitted to use indexes like
  macd, rsi; [also] moving average any period; reset is a must; don't change anything unless I asked;
  ensure we pass ci, commit, push, deploy. Keep leverage 20×." Cron also reset to 00:00/08:00/16:00.
- **BUILT (research harness):** added MACD (`macd_cross`, `macd_cross_ct`, `macd_zero`, `macd_hist`)
  + MA-cross (`sma/ema_cross_*`, periods 9/21…50/100) algos to scripts/algo_search.py (inline MACD/EMA
  from closes, same method as the live pattern). Backtested maker, walk-forward OOS + LOCKBOX.
- **RESULT — the first cross-era-positive signal in project history:**
  - **macd_cross_ct (trend-aligned MACD signal cross) @ 1h: +EV in BOTH eras** — recent expR +0.13
    (51% win), lockbox expR **+0.17** (52% win), R/R 1.2, IS→OOS positive both. Per-pair lockbox
    POSITIVE on all 4 with data (DOGE +0.29, XRP +0.26, ADA +0.20, SOL +0.09); recent positive on
    4/6 (BTC +0.21, ETH +0.16, DOGE +0.12, ADA +0.17).
  - **sma_cross_9_21 (wide) @ 1h** corroborates: recent +0.17 / lockbox +0.12, R/R ~1.35 (same fast-
    momentum family).
  - REFUTED (data-mined, +recent/−lockbox): `ema_cross_12_26`, `ema_cross_9_21`, `macd_zero`. All
    SLOW MA crosses (20/50, 50/100) −EV both. MACD @ **5m** −EV (cost floor) — edge is TF-specific.
- **HONEST CAVEATS (no overclaim):** modest (expR +0.1–0.17), wins <55% (fails the known-wrong §30
  win bar — but stop-cond #2 uses expectancy/PF), and lives at **1h, NOT the mandated 5m**. Clean
  "positive-in-BOTH-eras" pairs are ~2–3 (DOGE/ADA firm; SOL/XRP lockbox-only; BTC/ETH recent-only).
  PF/deflated-Sharpe borderline. → a STRONG LEAD to FORWARD-TEST, NOT a confirmed stop-#2 edge.
- **DEPLOYED (owner wanted a deploy + reset):** built `macd_cross` as a LIVE registered pattern
  (signal/patterns.py + `_macd_lines` helper + SELF_DIRECTING; config.py macd_fast/slow/signal params
  + params.json contract + unit tests). Added a **6-bot 1h macd_cross cohort** (BTC/ETH/SOL/DOGE/XRP/
  ADA) ALONGSIDE the untouched 238-bot 5m fleet — §13 permits high-TF as a research-comparison arm,
  so this honors "don't change the 5m fleet" + "deploy a new strategy". Fleet 238→**244**. Exit = the
  validated harness "tight" bracket (tp 1.4/sl 1.0 ATR/max_hold 4, trailing off). Dedup: 6 NEW + 238
  SEEN (baseline retained as controls). Leverage UNCHANGED 20×.
  - Known live-vs-backtest divergence (noted): live pipeline still applies volume_confirm (1.1, the
    floor) + the 0.01 risk cap, which the raw backtest did not — minor, acceptable for a forward-test.
- **SHIP:** ruff format+check clean · mypy clean · macd+registry+patterns/config/detector suites green
  (120 local) · _EXIT sync test green · committed+pushed main · CI green · rebuilt image (code change)
  · FULL RESET (wipe dev slate, KEEP candles, restart, heartbeats wiped after) · system event.
- **STOP CHECK:** NOT met — macd_cross is a forward-test lead, not a confirmed stop-#2 edge (modest,
  1h, per-pair borderline). Continue; let the live 1h cohort + the OOS backtests accumulate evidence.
- **MICROSTRUCTURE DATA LAYER (owner-authorized, same session):** owner asked about bid/ask + order
  flow — Kestrel had NEVER used it (OHLCV + candle-volume only). Built `scripts/record_microstructure.py`
  (standalone Layer-3 recorder, no trading): banks gate order-book depth (top-5/20 + imbalance),
  spread, and aggressor trade-delta every 10s for 6 pairs into its OWN `microstructure` table (NOT in
  frozen db/schema.py). Runs as the `microstructure-recorder` override.yml service (restart:
  unless-stopped). **Can't be backtested (no historical L2) → records LIVE; validate a sweep/imbalance
  signal after weeks accumulate.** GOTCHA caught+fixed: image entrypoint hardcodes the daemon & ignores
  `command:` → the service first ran a 2nd daemon (contaminated slate, re-reset); fix = override
  `entrypoint`. See memory [[project_microstructure_recorder]]. Honest: order flow is the most credible
  untested scalp lever BUT also the most latency-disadvantaged for retail — not a guaranteed win.

### Iteration 17 — 2026-06-21 (owner-triggered ~90min after iter-16 — health/cadence pass; cadence 8h→2h)

- **TRIGGER:** owner ran it manually — "8h is too long, run it now." (The session-only cron only
  fires when the REPL is fully idle, which isn't reliable while the owner is active, so firings have
  effectively been manual. Addressed below.)
- **MEASURE:** 367 closed · **25.1% win · −$18.05 · PF 0.32** (38 open, 238 live, healthy). Only
  **26 new closes** since iter-16 (~90min) — same −EV book, same −$0.07/trade. iter-15 poll fix
  STILL holding: **0 CRITICAL in 90min**. No infra fault, no structural change in 90 min (expected).
- **DIAGNOSE:** nothing new — too little time elapsed for new signal; strategy converged (iter-14
  search exhausted, iter-16 leverage shown variance-not-edge). The loop's ongoing value is now
  primarily OPERATIONAL: keep the fleet healthy + catch infra faults FAST (as iter-15's feed-flap
  catch proved) + accumulate the live paper sample. A 2h cadence serves that monitoring better than
  8h; it will NOT find edge faster (there is none to find) — it tightens fault-detection + activity
  visibility, which is what the owner actually wants from "run it more often."
- **MAINTAIN — JUSTIFIED HOLD on strategy; OPERATIONAL change = cadence 8h→2h.** No deploy/reset
  (fleet byte-identical to live). Re-armed the loop cron to every 2h (was 8h).
- **STOP CHECK:** NOT met (25.1% win ≪ 70%; net −$18.05). Continue, now every 2h.
- **OWNER-DIRECTED RESET (post-iter-17, owner: "keep the leverage but I don't see any reset"):**
  leverage CONFIRMED unchanged at 20× (§4 human-gated, never touched). Performed a FULL dev reset —
  the slate had grown across iters 13–17 and (per iter-15) spanned TWO feed regimes (throttled ws +
  poll), so it wasn't clean. Wiped 413 trades / 1126 signals / 76,238 events / 228,870 trade_context
  / 0 pattern_memory; **candles KEPT** (369,687 5m rows, fresh → no backfill needed); restart →
  `DELETE FROM heartbeats` AFTER restart (orphan-safe). Clean slate now accumulates entirely under
  the stable poll feed for a methodologically clean forward measurement. No code/config change, no
  edge created — a clean measurement baseline only.

### Iteration 16 — 2026-06-21 (8h firing — iter-15 poll fix VERIFIED held; leverage quantified as variance-not-edge; justified hold)

- **MEASURE:** 347 closed · **25.4% win · −$16.26 · PF 0.34** (24 open, 238 live, healthy; swap
  recovered 1602/2047). Close-reason: **stop_loss 84 @ 0% win / −12.2% = −$14.27** (still the whole
  bleed), timeout 192 @ 32.8% / −$2.18, trailing_stop 70 @ 35.7% / +$0.30. Same negative-skew book.
- **VERIFY iter-15 (the poll fix HELD — deliverable #1):** **connection-CRITICAL last 6h = 0** (was
  352/8h on ws). Throttle GONE: the previously-starved small-cap pairs now trade **5.4/pair vs 5.2
  for stable** (was 3.1 vs 9.1) — the §16 reconnect-throttle is removed, the fleet runs at full,
  even activity. The fix did exactly what was predicted. Note: restoring that activity on a −EV book
  is WHY absolute loss grew −$8.86→−$16.26 — honest "activity ≠ profit" in action, not a regression.
- **DIAGNOSE:** nothing is broken (infra clean post-poll); strategy search is exhausted (iter-14
  milestone). Stops remain the entire bleed, structurally, at 20× leverage (−12.2%/stop).
- **LEVERAGE QUANTIFIED HONESTLY (deliverable #2 — corrects the "dominant lever" framing):** PnL
  scales ~linearly with leverage and **win rate is leverage-INVARIANT** (TP/SL are ATR PRICE levels;
  leverage only scales the $/% magnitude, not which exit triggers). From the 347 real trades:
  20x −$16.26 · 10x −$8.13 · 5x −$4.06 · 3x −$2.44 — same trades, same win rate, **−EV at every
  level**. Fees scale with notional too, so the fee-to-PnL ratio is leverage-invariant → leverage
  does NOT help the cost floor either. **CONCLUSION: leverage is a VARIANCE / survival lever, NOT an
  edge lever.** Lowering it = slower bleed AND smaller gains; on a −EV book it just loses more slowly
  (which is the "be slow to lose less" the §6 mandate explicitly rejects as a goal). So even the one
  human-gated lever I kept flagging cannot CREATE edge — it only changes how fast the (negative)
  expectancy compounds. The honest set of edge-creating levers is now **empty** within current
  constraints (spot-only §13 rules out funding-rate; every entry/exit/regime refuted; leverage is
  variance-only). A real edge would need a constraint change (instrument class / structural
  mechanism), which is human-gated §4 — flagged, not started.
- **MAINTAIN — JUSTIFIED HOLD (no deploy, no reset).** No infra fault; no new edge lever exists;
  no structurally-dead cell to prune (compression_breakout + anomaly_fade fire ~0 at 5m but are
  inert, not loss-making — removing them helps nothing; loosening them would only add KNOWN −EV
  activity, which serves the loss column, not the search). Manufacturing a strategy change here would
  be theater (honesty preference #8 > ship-something). Fleet byte-identical to live → skip deploy+reset.
- **STOP CHECK:** NOT met (25.4% win ≪ 70%; net −$16.26; no edge, and now shown leverage can't make
  one). Continue / monitor — the binding constraint is human-gated.

### Iteration 15 — 2026-06-21 (8h firing — INFRA FIX: WS flapping was THROTTLING ⅓ of the fleet → FEED_MODE ws→poll)

- **MEASURE (slate since iter-13 reset, ~?h — iter-14 held/no-reset so it accumulated):**
  **209 closed · 23.9% win · −$8.86 · PF 0.38** (18 open, 238 live). Close-reason: **stop_loss 47 @
  0% win / −13.0% = −$7.43** (the bleed), timeout 128 @ 28.1% / −$2.12, trailing_stop 34 @ 41.2% /
  +$0.69 (only positive). By pattern: momentum_continuation 197 / −$8.09, impulse 6 / −$0.46, wick 6
  / −$0.31. Same negative-skew no-edge book — strategy conclusion unchanged from iter-14.
- **DOMINANT SIGNAL — 352 CRITICAL `connection` events in 8h** (iter-13/14 had ~0–5), ongoing
  (22 in last 30min). ALL "WS feed {PAIR}/5m exceeded max retries (5)" on the SMALL-CAP gate streams
  (SEI/ATOM/APE/CHZ/APT/BCH/TIA/ETC/FIL/OP/ARB/DOT). Telegram NOT flooded (TELEGRAM_SUPPRESS_CONNECTION
  still on, iter-12b) — DB-only noise.
- **DIAGNOSE (read-first, iter-11 lesson):** NOT a full outage — 33/35 pairs FRESH (candles closed
  4.4min ago); only TIA stale (29min). gate's ccxt.pro WS *flaps* on the 34 small-cap streams,
  recovers, re-flaps. The iter-12 premise "WS pushes each 5m rollover reliably" is EMPIRICALLY
  REFUTED. **Second-order harm (the real cost):** each WS reconnect trips §16 "no orders within 60s
  of reconnect" → the flapping pairs are THROTTLED. Confirmed: **flapping pairs 3.1 trades/pair vs
  stable pairs 9.1/pair (3× less)** — SEI/ATOM/APE/BCH each only 1 trade. So the WS instability is
  actively SUPPRESSING ⅓ of the fleet's activity = directly anti-mandate.
- **MAINTAIN (infra fix, evidence-driven — NOT strategy, NOT thrash):** `FEED_MODE ws→poll` in
  docker-compose.override.yml (host-local). poll = the iter-9 proven-stable REST transport; it
  removes the reconnect churn AND the §16 reconnect-throttle, RESTORING activity on the small-caps.
  The ~60s entry-lag tradeoff is acceptable for a no-edge research fleet (stability+completeness >
  microsecond scalp timing). Config-only → `docker compose up -d kestrel` (recreate, no rebuild,
  memory-safe under the full-swap pressure). **NO reset** (infra fix, not a new algorithm — iter-10
  precedent; the 209-trade sample stays valid, just spans the pre/post-fix feed boundary).
- **HONEST:** this does NOT create edge — the book is still −EV (no-edge truth unchanged). It fixes
  an OPERATIONAL throttle so the fleet actually runs at full activity (the owner's core ask).
- **STOP CHECK:** NOT met (23.9% win ≪ 70%; net −$8.86; no edge). Continue.

### Iteration 14 — 2026-06-20 (8h firing — iter-13 stop-widen REFUTED; high-ADX entry gate REFUTED; lockbox is data-infeasible at 5m)

- **MEASURE (slate since the iter-13 reset + an intervening session OOM/auto-recover, ~?h):**
  **63 closed · 19.0% win · −$4.87 · PF 0.14** (20 open, 238 live, 0 crash-loop). Close-reason:
  **stop_loss 23 @ 0% win / −14.3% avg = −$3.71 (the whole bleed)**, timeout 26 @ 34.6% / −$0.85,
  trailing_stop 14 @ 21.4% / −$0.31. take-profit still NEVER hits. By pattern: momentum_continuation
  58 / −$4.45 (the bulk), wick_rejection 3 / −$0.24, impulse_retracement 2 / −$0.17.
- **DIAGNOSE #1 — iter-13's stop-widen (sl 0.9→1.3 ATR) is REFUTED by live data.** Stop-out SHARE
  of closes is unchanged (iter-13 52/145=36% → iter-14 23/63=37%); win rate did NOT improve
  (29%→19%, small-sample noise but certainly not up); per-trade net is slightly WORSE
  (−$0.059→−$0.077, because each wider stop loses more % when hit). Widening the stop reshaped
  variance without cutting the stop-out rate or lifting win — the bleed is ENTRY-EDGE, not
  stop-distance. (Same conclusion as the Wave/Trailing/Risk-shaping ledger entries, now confirmed
  at 5m.) Did NOT thrash the stop back to 0.9 (that was also −EV) — left at 1.3 (R/R 1.46 ≥ Rule 3).
- **HYPOTHESIZE + BACKTEST (the arbiter) — high-ADX "better entries" lever, the one in-scope entry
  knob not yet recorded.** The live fleet runs `adx_strong_min=20` (lowered iter-12 for max fires),
  so momentum fires on weak trends. Tested raising the bar via `--regime trending` (high-ADX
  subset) vs all-regime baseline, 5m, maker, 4 pairs, walk-forward OOS (okx, 6048 candles, OOS=2420):
  - baseline:  mom_adx/wide n=279 win 40.9% avg −$0.0072 R/R 1.20 expR +0.06 · triple_mom/wide n=167 win 41.9% −$0.0080
  - trending:  mom_adx/wide n=235 win 40.0% avg −$0.0073 R/R 1.26 expR +0.08 · triple_mom/wide n=141 win 41.1% −$0.0073
  → **REFUTED:** the high-ADX gate cuts trade count ~15% but per-trade economics are flat
  (≈ −$0.007/trade either way), win% is slightly LOWER, 0/4 clear §30. It is "fewer trades, same
  −EV" = shrink-activity-with-no-benefit (mandate-forbidden, and pointless). Do NOT raise the gate.
- **DIAGNOSE #2 — STRUCTURAL: lockbox validation is DATA-INFEASIBLE at 5m.** The proper arbiter
  (stop-cond #2 = prior-year untouched lockbox) cannot be run at 5m: gate returns "Candlestick too
  long ago", kraken caps at 721 candles, okx serves only ~21d of 5m. **No exchange retains year-old
  5m history.** The lockbox method that refuted 4h/1h momentum simply has no data at 5m. So the
  iter-12 pivot to 5m means the fleet can NEVER produce stop-cond-#2 evidence by backtest — only the
  live paper slate accumulates. Recent-window-only backtests are data-mining-prone (no out-of-era
  check). This is a real tension between the owner's 5m hyper-scalp mandate and rigorous validation.
- **MAINTAIN — JUSTIFIED HOLD (no deploy, no reset).** The one new in-scope lever tested (high-ADX
  entry) is refuted; deploying it would cut activity for no benefit (anti-mandate). No param/
  enforcement gap; cell-viability needs structurally-dead cells (momentum is the activity driver,
  ✗ prune). Nothing new is byte-worthy → fleet unchanged (238 bots, adx_strong_min 20, stop 1.3).
  Per protocol step 9, skip reset when nothing deployed. Deliverable this firing = the two
  refutations recorded (real knowledge), not a churn deploy.
- **RE-ESCALATE (the binding lever, human-gated §4):** the book is negative-skew at **20× leverage**
  — stops are 0% win / −14.3% each. Lowering leverage to 5–10× would shrink each stop's % hit and
  reshape the skew; it is the dominant remaining lever and is `.env`/§4 human-only. The other real
  lever is a non-directional STRUCTURAL edge (funding-rate harvesting), a large build, also flagged.
  Both stay flagged, not started.
- **STOP CHECK:** NOT met (19% win ≪ 70%; net −$4.87; lockbox edge unprovable at 5m). Continue.

### Iteration 13 — 2026-06-20 (8h cron — first real scalp data; widen stop to cut the bleed)

- **MEASURE (first real scalp slate, 238-bot 5m fleet, ~7h):** **145 closed · 29.0% win · −$8.58 ·
  PF poor.** By pattern (all −EV): trend_momentum 61 tr −$3.90 (PF 0.34, worst & most active),
  mom_adx 44 −$2.49, triple_mom 31 −$1.02 (least-bad), impulse_retracement 6 −$0.86 (0% win),
  wick_rejection 3 −$0.30; compression_breakout + anomaly_fade 0 closed (rare setups). 0 crashes,
  238 live, Telegram reconnect spam suppressed (iter-12b).
- **DIAGNOSE (dominant bleed):** STOP-OUTS. Close-reason mix: **stop_loss 52 @ −$0.18 = −$9.47**
  (the killer) vs **timeout 51 @ +$0.04 = +$2.13** (slightly positive) vs trailing_stop 42 @ −$0.03.
  **take-profit NEVER hit (0 closes)** — winners come only from the trail. The 0.9-ATR stop sits
  INSIDE 5m noise and gets picked off before trades develop (same lesson as the iter-5 1-ATR fix,
  now at 5m).
- **MAINTAIN (fix exits, ✗ shrink fleet — per hyper-scalp mandate):** widened the stop bracket
  **sl 0.9→1.3, tp 1.4→1.9 ATR** (R/R 1.46 ≥ Rule 3's 1.2) to let trades breathe and convert
  premature stops into trail/timeout survivors — which also lifts win rate toward the owner's 70%
  bar. SINGLE-variable change (the stop bracket); fleet/activity unchanged (238 bots). Did NOT prune
  trend_momentum despite being the worst cell — it is the primary activity driver and the mandate
  forbids pruning-to-slow; the bleed is an exit problem, not a "too many bots" problem.
- **SHIP:** promote_to_staging._EXIT synced (sync test), registry resharded, lint+CI green,
  committed+pushed, restart (config) + FULL RESET (wiped 155 trades, candles kept, 238 live,
  trades=0, transient candle_processor_error during reset self-healed).
- **HONEST:** this RESHAPES variance / cuts the stop-out bleed — it does NOT create edge (the book
  is ~coin-flip). Next pass measures whether win rate + net actually improved on the wider stop.
- **STOP CHECK:** NOT met (29% win ≪ 70%; net −$8.58; no lockbox edge). Continue.

### Iteration 12 — 2026-06-20 (owner pivot: HYPER-SCALP fleet, hundreds of bots)

- **TRIGGER:** owner — "yes like before, we built it for this; no point building a bot if not
  hyper-speed scalping with hundreds of bots; if slow I can do it myself and won't need you.
  If important, update CLAUDE.md." Explicit authorization to edit the frozen CLAUDE.md.
- **CLAUDE.md v2.1 (owner-authorized):** §6 Purpose set to HIGH-FREQUENCY SCALPING FLEET; §13
  Timeframes → 1m–5m scalp (5m default), Fleet scale → hundreds of bots, maker fees REQUIRED.
  Honest no-edge/paper caveat preserved (✗ delete).
- **FLEET REBUILD:** `build_momentum_lab.py` → **238 bots = 7 patterns × 5m × 34 liquid pairs**
  (pairs VERIFIED on gate; leveraged tokens excluded). Activity drivers: `trend_momentum`
  (permissive ~9% of candles) + `mom_adx`/`triple_mom`; 4 shape patterns add breadth. Scalp exit:
  tp 1.4 / sl 0.9 ATR (R/R 1.55), max_hold 4 (20 min), trail 0.5R, adx_strong_min 20 (low end →
  more 5m fires), maker on. WS feeds shared per (pair,5m) = 34 streams for 238 bots.
- **INFRA:** override.yml → FEED_MODE poll→**ws** (proven 5m transport), postgres 1g→**2g** +
  kestrel 1g→**2g**/cpus 3 (238 bots write far more at 5m; pre-empt the iter-10 OOM at scale).
- **LOOP MANDATE REALIGNED:** banner → HYPER-SCALP MAINTENANCE; pruning-to-slow is now forbidden;
  cell-viability NARROWED to structurally-dead cells only; scaling activity UP is encouraged.
- **HONEST FRAME (unchanged):** more activity ≠ more profit; 5m scalping bleeds fees faster with no
  proven edge. This is the owner's directed design for PAPER research; the job is to hunt edge at
  speed+scale, not to pretend speed creates it.
- **SHIP:** lint → commit+push main → CI green → deploy (up -d, recreate w/ 2g + ws + new bots.json)
  → FULL RESET (clean slate) → verify ~238 heartbeats / trades=0 / activity firing / 0 errors.
- **STOP CHECK:** not met. Continue.

### Iteration 11 — 2026-06-20 (8h cron — justified NO-OP; infra held; feed-stale alarm was a measurement artifact)

- **MEASURE:** ~1h after the iter-10 clean reset (23:47). Infra fixes HELD: postgres 22% of 1g
  (calm), kestrel restarts=0 (no crash), **0 errors**, 120 heartbeats. 0 trades / 0 signals so far.
- **NEAR-MISS (logged so I don't repeat it):** the per-TF "candle freshness" read 1h=114min /
  4h=294min stale, which looked like the feed had died again. **It had NOT.** Candle `ts` is the
  candle's OPEN time; the poll feed (`new_closed_rows`) emits a candle only once it has CLOSED
  (`ts + period ≤ now`). The latest 1h candle `ts=23:00` CLOSED at 00:00 — 59 min ago, perfectly
  fresh (next close 01:00). Measuring staleness from open-ts inflates it by a full period. The poll
  feed even wrote the 23:00 candle live after the 23:47 backfill — it is working. Reading the source
  (src/data/providers/polling.py) before acting prevented a wrong "revert the feed" fix.
  **DIAGNOSTIC FIX:** freshness must be `now − (max(ts) + period_ms)`, NOT `now − max(ts)`.
- **DIAGNOSE:** no bleed; 0 trades is simply ~1h post-reset + only ONE 1h close (00:00) + zero 4h
  closes yet + selective patterns (ADX>25 etc.) not firing on a single candle. Expected.
- **MAINTAIN — justified NO-OP:** cell-viability needs ≥50 closed (have 0); nothing to prune; no
  param/enforcement gap; no marginal variants (exhausted). No fleet/code change → no deploy/reset.
- **STOP CHECK:** not met. Continue; let the clean run accumulate.

### Iteration 10 — 2026-06-19 (incident: FIXED a Postgres OOM crash — root cause)

- **TRIGGER:** user — "suddenly not working and getting worse, so many telegram error
  notifications, everything is collapsing, fix it all."
- **MEASURE/TRACE:** three distinct issues, none of them the strategy:
  (1) the Telegram flood = the iter-9 WS outage (370 CRITICAL, 11:00–16:25), already fixed by
  WS→poll — 0 new feed errors since.
  (2) "collapsing now" = a **Postgres crash at 23:27**: daemon log `asyncpg …
  CannotConnectNowError: the database system is in recovery mode`; pg log `all server processes
  terminated; reinitializing` + `database system was not properly shut down; automatic recovery`.
  **Root cause: `docker stats` showed `kestrel-postgres` at 249.1MiB / **256m** (97%)** — the base
  cap (docker-compose.yml, sized for a 1GB prod VPS) is far too small for 120 bots × the connection
  pool → a backend was OOM-killed → crash recovery → every bot's DB conn dropped → watchdog restart
  (120 `daemon_ready`, ONE bucket, not a crash loop). The "frozen feed" was just the daemon
  re-initialising seconds before measurement.
  (3) "75% win / 4 trades / no 15%" = the honest no-edge reality (3W/1L, net +$0.09, statistically
  meaningless; 15%/day remains impossible).
- **MAINTAIN (real fix):** added a `postgres: mem_limit: 1g` override in
  `docker-compose.override.yml` (gitignored/host-local; the committed 256m prod-safe default stays)
  → `docker compose up -d postgres` (pgdata volume persists, NO data loss) → restart kestrel for a
  clean reconnect → `backfill_history.py` to heal the gap. **NO `reset_dev`** — infra fix, sample kept.
- **VERIFY (recovered + stable):** postgres now **283.7MiB / 1g (27.7%)**, i.e. it needed >256m all
  along; 0 new recovery/OOM in logs; daemon restarts only at 23:27 (crash) + 23:30 (fix), none
  after; 120 heartbeats; **8 trades + 306k candles KEPT**; 0 errors; 1h/4h candles fresh.
- **STOP CHECK:** not met. Continue. The crashes were INFRASTRUCTURE (feed transport iter 9 + DB
  memory iter 10), now root-fixed; the lack of returns is the unchanged no-edge truth.

### Iteration 9 — 2026-06-19 (8h cron — FIXED a feed outage: WS→poll, root cause)

- **MEASURE:** ~16h runtime. 4 closed trades (3W/1L, net +$0.095, n far too small to mean anything),
  2 open. BUT the dominant signal was **370 CRITICAL `connection` errors** (was 0 at iter 8):
  `WS feed {pair}/{tf} exceeded max retries (5) … gate … TimeoutError`, ~18 per (pair,tf) × 20
  feeds, concentrated 11:00–12:00 UTC (362/370), last at 16:25. Daemon stayed alive (heartbeats
  fresh) but the **1h candle went 114 min stale** — feeds were starved.
- **DIAGNOSE (root cause, not symptom):** `FEED_MODE=ws`. ccxt.pro WebSocket is reliable only for
  LOW TF (5m); at 1h/4h its sparse rollover pushes get missed, the per-(pair,tf) subs hit max
  retries (5) and **permanently give up** → candle closes stop arriving (0 errors in the last
  15 min = not still failing, but *dead*). This is the documented WS-vs-poll history; the `ws`
  setting was leftover from the old 5m labs. The diversity fleet is 1h/4h → wrong transport.
- **MAINTAIN (real fix):** switched `FEED_MODE: ws → poll` in `docker-compose.override.yml`
  (gitignored/host-local, NOT a frozen file) → `docker compose up -d kestrel` (compose env change
  needs recreate, not restart) → `backfill_history.py --source gate` to heal the candle gap.
  **NO `reset_dev`** — a feed-transport fix is operational, not a strategy change, so the
  accumulated sample (4 closed + 2 open + 6 signals) was KEPT on purpose.
- **VERIFY (recovered):** poll active, container healthy, **0 new errors**, 120 heartbeats live,
  1h/4h candles 61 min fresh (within one period; was 114), 2 open positions reconciled, candles
  kept. The old WS CRITICALs remain in history (pre-fix) but nothing new accrues.
- **FLAG:** at go-live on bingx, 4h candles must also come from poll/aggregation (bingx WS 4h
  unsupported) — already noted in the override. No §4 item touched.
- **STOP CHECK:** not met. Continue.

### Iteration 8 — 2026-06-19 (8h cron — justified NO-OP, trades open but none closed)

- **MEASURE:** diversity fleet ran ~7.9h (one cron cycle). 120/120 live, **0 errors**. **5 signals
  fired + 1 rejected → 5 positions OPEN, 0 CLOSED.** Firing bots: `mom_adx` (3), `triple_mom` (1),
  `impulse_retracement` (1), all 1h. Open ages 3–7h; oldest (mom_adx ADA 1h) just hitting its
  6-candle timeout boundary (~414 min ≈ 6×1h from a mid-candle entry) — consistent, not a stuck pos.
- **DATA-QUIRK confirmed (not a bug):** the signals `pattern` column logs the PatternType enum, so
  `mom_adx`/`triple_mom` bots show `pattern='momentum_continuation'`. The real bot identity is
  `split_part(bot_id,'-',4)`. Verified each signal's bot matches its strategy — no cross-firing.
- **DIAGNOSE:** no realized PnL yet (0 closed) → no bleed to cut, and the cell-viability rule
  (≥50 closed) can't evaluate any cell. WATCH-ITEM (not actionable at 8h): 3 of 6 patterns
  (`compression_breakout`, `anomaly_fade`, `wick_rejection`) have NOT fired — expected (they need
  VOLATILE/RANGING regimes that may not have occurred yet), but track vs the "patterns never fire"
  risk; if still zero after the fleet has closed a meaningful sample, that's a real signal.
- **MAINTAIN — justified NO-OP:** nothing to prune/enforce on 0 closed trades; no marginal variants
  (exhausted). Re-validation not weekly-due. No fleet/code change → no deploy/reset.
- **STOP CHECK:** not met (0 closed trades). Continue.

### Iteration 7 — 2026-06-19 (8h cron — justified NO-OP, fleet too fresh)

- **MEASURE:** the iter-6 diversity fleet (120 bots = 6 patterns × 2 TF × 10 pairs) was deployed
  THIS session and is brand-new: heartbeat age ~0 min, **0 closed trades, 0 signals** (no 1h/4h
  candle has closed since the restart), **120/120 live, 0 errors**. Slate is the post-reset clean.
- **DIAGNOSE:** no bleed exists yet — the fleet hasn't traded. Every (pattern × TF) cell has 0
  trades, so the cell-viability rule (≥50 closed) cannot fire on anything.
- **MAINTAIN — justified NO-OP:** nothing to prune (no losers yet), no param/enforcement gap
  surfaced by data, and the protocol forbids manufacturing marginal directional variants
  (refuted/exhausted). Re-validation is not weekly-due — extensive walk-forward+lockbox validation
  ran through iter 6. Correct action is to let the diverse fleet accumulate so the NEXT pass has
  real per-pattern cells to compare (esp. whether the 4 new shape-specific patterns fire enough at
  1h/4h — the historical "patterns never fire" risk). No fleet/code change → no deploy/reset.
- **STOP CHECK:** not met (0 trades; no lockbox edge). Continue.

### Iteration 6 — 2026-06-19 (user-directed: pivot to hypothesis diversity)

- **TRIGGER:** user — "with current 40 bots setup is not effective, we didn't learn more about
  anything, is it good if we keep 120 bots?" + "split [the registry], create directory, check this
  over and over during the loop, don't make the same mistakes."
- **DIAGNOSE (the registry made it concrete):** the 40-bot fleet = 2 patterns (mom_adx,
  triple_mom) × 2 TF × 10 pairs — WIDE on instruments, NARROW on ideas (both refuted momentum). So
  nothing new is learned. `bot_registry` showed `wick_rejection` / `compression_breakout` /
  `anomaly_fade` have **NEVER been deployed as bots** → genuinely untested hypotheses. The fix for
  "learn more" is DIVERSITY, not count: the old 120 (3 momentum × 4 TF) would be re-running SEEN
  −EV cells; a DIVERSE 120 adds real information.
- **REGISTRY (the "don't make the same mistakes" ask):** sharded the 1.3 MB monolith into
  `bot_registry/` (one shard per instrument + `_index.json`, 35 shards). Wired the dedup guard to
  run **every firing** (step 5): prefer NEW configs, retain SEEN only as deliberate controls.
- **ACTION:** rebuilt `build_momentum_lab.py` as the DIVERSITY lab — **120 bots = 6 patterns × 2
  TF (1h/4h) × 10 pairs**: 2 momentum controls (SEEN) + 4 under/never-tested patterns spanning all
  regime buckets (80 registry-NEW). Uniform risk-shaped exit so the comparison isolates each
  pattern's signal. Retired the now-redundant `exp_h1tp` cohort. Dedup guard: 80 NEW + 40 SEEN.
- **HONEST FRAME:** this does NOT create edge (directional discovery is exhausted) — it maximises
  what each bot TEACHES per unit of fleet. Still paper, still no proven edge, still §18-gated.
- **SHIP:** lint clean · committed+pushed main · CI green · redeploy (bots.json bind-mount → restart)
  · FULL RESET (heartbeats wiped up-front, backfill, restart, verify 120 / trades=0) · system event.

### Iteration 5 — 2026-06-18 (user-directed active maintenance)

- **TRIGGER:** user flagged "everything now worse" + expanded the mandate ("you can replace
  unproductive bots, add new parameters or any statistical enforcements").
- **MEASURE:** slate since iter-4 reset = 138 trades · **26.9% win · −$6.04 · PF 0.28** (worse).
  By strategy: `trend_mom` −$3.60 (86 tr, worst), `mom_adx` −$1.75, `triple_mom` −$0.69. By TF:
  5m −$4.88 (110 tr), 15m −$1.45 (12.5% win), 1h +$0.29 (75% win, the cohort). 0 errors, healthy.
- **DIAGNOSE:** the bleed is the short-TF / `trend_mom` baseline cells kept for activity — all
  below the cost floor, structurally −EV. The 1h cohort is the only green.
- **ACTION (active maintenance, not discovery):** codified the **cell-viability rule** (≥50 closed
  trades AND net<0 AND PF<1 → prune) as the loop's standing statistical enforcement, and applied
  it: pruned **5m + 15m + `trend_mom`** from `build_momentum_lab.py`. Baseline 120 → **40** (mom_adx
  + triple_mom × 1h/4h × 10). Fleet = 40 baseline + 8 cohort = 48. Re-activated cron to 8h + moved
  the loop to ACTIVE-MAINTENANCE mode.
- **RITUAL:** lint (format+check) clean → commit/push → CI green → restart (config-only) → full
  reset → event. No edge created — exposure/bleed cut by removing the dead cells.
- **CHECK STOP:** not met.

### Iteration 4 — 2026-06-18 (scheduled firing)

- **MEASURE:** 113 trades since iter-3 reset · 54.3% win · +$1.55 · PF 1.58 — but this is 7h of
  variance on the no-edge baseline (all baseline; cohort thin), NOT edge. `exp_tod` seasonal fired
  0 (correctly — 08:48 UTC, outside the 18:00–00:00 window; 0 window candles since the 01:14 reset).
  Fleet healthy, 0 errors.
- **DIAGNOSE:** no validated edge; the seasonality lead is the only positive signal and needs a
  rigorous robustness test before it earns more (the queued step).
- **HYPOTHESIZE/BACKTEST:** validated the EXACT deployed window per-pair (`--validate`, non-overlap
  significance, 6 pairs, recent + lockbox) — one pre-specified hypothesis, not a 120-combo search.
  Result: gross drift real (pooled non-overlap t≈4.8 recent / 2.2 lockbox) but **effect ~0.05%
  gross < maker cost** → pooled net-maker LOCKBOX **−0.008%** (negative); only DOGE robust (1/6).
- **DECIDE:** seasonality **REFUTED** as a net-of-cost edge (the iter-3 "8/120" was a
  multiple-testing artifact). Baseline untouched. Added to refuted ledger + declared the
  directional/seasonal search EXHAUSTED (milestone) → escalate to user (structural or leverage next).
- **APPLY:** retired `exp_tod`; cohort → `exp_h1tp` 1h momentum only (128 bots). `session_seasonal`
  pattern kept registered (tested) but undeployed. Config-only → restart.
- **CHECK STOP:** not met.

### Iteration 3 — 2026-06-18 (scheduled firing)

- **MEASURE:** 98 trades since iter-2 reset · 32.6% win · −$5.03 · PF 0.30. Two anomalies found &
  resolved: 16 stale heartbeats (iter-2 reset-race orphans) cleaned → 136; stop_loss avg −33.9%
  traced to `trend_mom` 15m @ 20× on volatile candles (real, not a bug). Cohort (1h) still thin.
- **DIAGNOSE:** stop-outs remain the entire bleed, now fat-tailed from `trend_mom`/short-TF/leverage.
- **HYPOTHESIZE:** time-of-day / overnight seasonality (web-researched; genuinely new, non-momentum).
- **BACKTEST** (`scripts/backtest_seasonality.py`, 1h, 6 pairs, recent + lockbox): the US-afternoon/
  overnight window (≈18:00–00:00 UTC) is net-MAKER-positive in BOTH windows — **the first
  lockbox-survivor in project history**. But marginal: maker-only (negative at taker),
  +0.01–0.05%/trade lockbox, 8/120 combos (multiple-testing risk).
- **DECIDE:** does NOT clear stop-cond #2 → **baseline untouched**. Best lead → cohort forward-test.
- **APPLY:** built the `session_seasonal` pattern (config.py PatternType + 2 Params, patterns.py +
  SELF_DIRECTING, regime.py permits, params.json, + unit tests). Rotated cohort → `exp_tod`
  (seasonal) + `exp_h1tp` (1h momentum, kept). 134 bots. Code change → rebuild. Validated the new
  code in-container before rebuild (registry/regime/params/load/fire-time all pass); 161 signal
  unit tests green.
- **CHECK STOP:** not met (seasonality marginal/maker-only; not lockbox-validated to the #2 bar).

### Iteration 2 — 2026-06-17 (first scheduled/triggered firing)

- **MEASURE:** post-reset slate, ~6h: 81 trades · **39.1% win** · net −$3.04 · PF 0.35. Baseline
  67 closed (37.3% win, −$3.14); cohort still thin (2 closed, both wins). Close-reason: `stop_loss`
  19 / **0% win** / −12.4% each (dominant bleed, ~28% of closes); `trailing_stop` 27 / 59.3% / +1.06%;
  `timeout` 23 / 47.8% / −0.51%. 0 errors, fleet healthy.
- **DIAGNOSE:** same signature — the new exit profile works (trailing exits now +EV), but with no
  entry edge ~28% of trades run straight to the full 1.5-ATR stop (0% win, −12% each) and sink it.
- **HYPOTHESIZE:** 1h momentum — the one structural lever flagged but never lockbox-tested (only 4h
  was). Web research queued time-of-day/overnight seasonality (Quantpedia) as a future arm (needs a
  new pattern).
- **BACKTEST (maker, 6 pairs, walk-forward OOS + lockbox):** 1h `mom_adx`/wide recent-year **+EV**
  (n=747, win 45.6%, net +$5.03, expR +0.10, R/R 1.37, IS→OOS +0.009) but **lockbox breakeven/neg**
  (win 43.0%, net −$0.50, expR +0.02); tight variants −EV both windows. 0/4 clear §30 in both.
- **DECIDE:** 1h does NOT validate (lockbox not positive) → **baseline untouched**. 1h added to the
  refuted ledger as "least-bad, still not an edge." Best candidate = 1h momentum (cohort only).
- **APPLY:** rotated cohort → two 1h arms (`exp_h1tp` bank-early vs `exp_h1run` let-run), dropped the
  dead 5m `exp_qual`. 136 bots (120 baseline + 16 cohort). Config-only → restart.
- **CHECK STOP:** not met (best win 45.6% < 70%; lockbox not positive → no validated edge).

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
