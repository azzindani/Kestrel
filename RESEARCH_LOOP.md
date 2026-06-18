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
   the 120-bot **baseline** too (`build_momentum_lab.py`/`params.json`) **only if it fully
   validates** (beats baseline AND clears the lockbox without IS→OOS collapse). If the best
   candidate is *identical* to what's already deployed, say so in the log and skip the
   deploy+reset (let the slate accumulate) — otherwise proceed.
6. **LINT** — run BOTH (CI runs both; `feedback_local_lint_must_match_ci`):
   `ruff format --check src/ tests/` **and** `ruff check src/ tests/`. Fix with
   `ruff format src/ tests/` before continuing. (The format check is the easy-to-forget half.)
7. **COMMIT + PUSH** — commit DIRECTLY to `main`, never a branch (`feedback_no_branches_commit_to_main`);
   `git push origin main`. Then **verify CI is green** (`gh run list --limit 1` until
   `completed/success`). Do not call the iteration done on a red/in-progress CI.
8. **REDEPLOY** — code change → `docker compose up -d --build kestrel`; config-only
   (bots.json/params.json) → `docker compose restart kestrel`. Confirm container `(healthy)`.
9. **FULL RESET (the ritual I kept missing — `feedback_reset_after_new_algorithm`)** — whenever a
   new/changed config was deployed this iteration: `reset_dev.py --yes` (wipe trades/signals/
   events/trade_context/pattern_memory) → wipe `heartbeats` → `backfill_history.py --source gate`
   → `docker compose up -d --build`/`restart`. **KEEP candles.** Verify clean: `trades=0`,
   heartbeats back to full count, cohort present, `errors=0`. (Skip ONLY when step 5 deployed
   nothing new.)
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

- **Iteration 3 (deployed 2026-06-18):** 14 bots, 2 arms. `exp_h1tp` = 1h momentum (mom_adx +
  triple_mom × {BTC,ETH,SOL,DOGE}, bank-early exit) — kept to accumulate. **`exp_tod` = the NEW
  `session_seasonal` pattern** (time-of-day long, 18:00–00:00 UTC window) × {BTC,ETH,SOL,DOGE,
  BNB,XRP}, 1h, time-based exit (hold4, wide TP/SL, trail off). Watch `exp_tod_seas` rows.
- ~~Iter 2: `exp_h1tp` + `exp_h1run`~~ retired the let-run A/B; ~~Iter 1: `exp_qual` + `exp_htf`~~.

## BASELINE (set 2026-06-17, before iteration 1)

- Deployed config: momentum lab — 120 bots = 3 strategies (`mom_adx`, `triple_mom`,
  `trend_mom`) × 4 TF (`5m`, `15m`, `1h`, `4h`) × 10 pairs.
- Exit profile: `tp_atr=2.4 / sl_atr=1.5 (R/R 1.6) / max_hold=6 / trail arms +0.5R trails 0.5R
  / max_loss_pct=0.01`. Leverage 20×. MAKER sim on. Portfolio guard ±10%.
- **Live metrics (PRE-reset diagnostic that motivated the cohort):** 124 trades · **31.5% win**
  · net **−$3.25** · profit factor 0.55.
- **FULL RESET performed 2026-06-17** after the cohort deploy (standing preference
  `feedback_reset_after_new_algorithm`): dev slate wiped (124 trades / 203 signals / 7924 events
  / 57k trade_context / heartbeats cleared, **candles kept**), all 136 bots relaunched clean.
  → Each MEASURE step now **re-baselines from the fresh slate** — do not expect the 124-trade
  numbers above; they are history. The clean-slate evaluation starts here.

## CURRENT BEST

- Baseline unchanged (no candidate has FULLY validated).
- **Most promising LEAD (iter 3): time-of-day / overnight seasonality** — the project's first
  signal to survive the untouched lockbox (US-afternoon/overnight window net-maker-positive in
  both windows, 6 pairs). **But marginal**: maker-only (negative at taker), +0.01–0.05%/trade in
  the lockbox, 8/120 combos survived (multiple-testing risk). Now forward-testing live as cohort
  `exp_tod`. To PROMOTE it needs: a deflated-Sharpe / multiple-testing correction, per-pair
  robustness, and a taker-survivable or genuinely maker-fillable execution — none proven yet.

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
- **1h momentum** (iter 2) — the LEAST-BAD TF but still NOT an edge: `mom_adx`/wide is recent-year
  OOS +EV (n=747, win 45.6%, net +$5.03, expR +0.10, R/R 1.37, maker) but the **lockbox
  (prior year) is breakeven/negative** (win 43.0%, net −$0.50, expR +0.02); tight variants clearly
  −EV both windows. Same "wins recent / dies prior" data-mining signature as 4h → not promoted.
  Kept as the cohort testbed only.

**Levers confirmed real but human-gated / structural (outside agent scope or untried):**
maker fees (confirmed big, already on in sim) · **leverage** (.env/§4, human-only) ·
**funding-rate harvesting** (structural, untried) · instrument class (no broker keys).

---

## ITERATION LOG

<!-- newest first; each firing appends one entry -->

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
