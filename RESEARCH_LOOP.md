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
  redeploy → **SCOPED reset (see §RESET POLICY)** → verify clean) → log a `system` event →
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
3. **Reset only what CHANGED** after a deploy — `feedback_reset_after_new_algorithm`, NARROWED
   (owner 2026-06-25 "the cron resets everything every 8h"): the old full-nuke-every-deploy was
   *destroying the forward-test* — most deploys are ADDITIVE (a new cohort = new bot_ids, already
   empty) yet the full wipe erased the running leads' history every time, so the live slate never
   accumulated past a day. NEW POLICY (see §RESET POLICY): additive deploy → **no reset**; a config
   change to an existing bot_id → `reset_dev.py --strategy <changed> --yes` (that cohort only,
   pattern_memory kept); **staging is NEVER reset**. KEEP candles + microstructure always. The
   forward-test sample is the POINT — stop wiping it.
4. **Ensure CI passes** — lint to CI scope, push, and confirm `gh` shows `completed/success`
   before declaring done.
5. **Commit & push to `main`** directly — never a branch (`feedback_no_branches_commit_to_main`).
6. **Redeploy** — rebuild (code) or restart (config) and confirm the container is healthy.
7. **Make it visible in Grafana** — the reset zeroes the panels and the cohort rotates, so the
   dashboard changes; also log the `system` event marker.
8. **Be honest about edge** — the cohort/visibility is a live testbed, not a profit claim; the
   project still has no proven edge. Do not fake an edge to satisfy the bar.
9. **SCOPED reset, NOT full-nuke each firing** (SUPERSEDES the old iter-21 "always full-reset";
   owner 2026-06-25 caught that resetting everything every 8h destroys the forward-test). The 8h
   cron is the CHECK-IN rhythm, NOT the strategy DECISION window. **Never apply or remove a strategy
   off an 8h read.** A NEW algo deploys only if it survives the untouched prior-year LOCKBOX
   (+expectancy, ≥3 pairs) — that gate, not elapsed time, is the fluke-killer. An ALREADY-DEPLOYED
   live lead is judged on TRADE COUNT not clock (~30+ to read, ~100+ to trust; at 1h that is weeks)
   — **which means its history MUST survive the next deploy, so additive deploys reset nothing.**
   The owner's "reset except the offer and bid data" stands: KEEP both `candles` AND the
   `microstructure` (bid/offer) table — and now KEEP the unchanged cohorts' trades too.

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
9. **§RESET POLICY — SCOPED, not full-nuke (`feedback_reset_after_new_algorithm`, narrowed
   2026-06-25).** The reset's only job is to keep a CHANGED config from being judged on stale rows.
   It is NOT supposed to erase the running leads' forward-test — doing that every 8h is exactly what
   kept the live slate stuck at <1 day of data.
   **BACKUP FIRST (owner 2026-06-25 "store the database"):** before ANY reset (scoped or full),
   and at least once every firing regardless, run `python3 scripts/backup_db.py` (LEAN, ~29 MB,
   excludes re-fetchable candles → the irreplaceable trades/trade_context/microstructure/signals/
   events/pattern_memory). It rotates (keep 14) and has a disk guard (host is ~94% full — candles
   are the 800 MB bulk and re-fetchable, so lean keeps the safety net cheap). Take a `--full --keep 3`
   snapshot occasionally for a complete portable copy. The dumps are the dataset future data-analytic
   SELECTION runs on, so a scoped reset's deleted cohort is preserved in that firing's lean dump.
   Then decide reset by deploy KIND:
   - **Additive deploy** (this iteration only ADDED new bot_ids — a new cohort/pair/pattern): those
     rows don't exist yet → **reset NOTHING.** Just `backfill_history.py --bots bots.json --source
     gate` the NEW bot_ids (they need candles), restart, and `DELETE FROM heartbeats` AFTER restart
     (orphan-safe spot, see below). The existing fleet keeps accumulating.
   - **Config change to an EXISTING bot_id** (same bot_id, different params/exits): surgical only —
     `reset_dev.py --strategy <changed_strategy[,…]> --yes` (wipes just those cohorts' dev rows;
     **pattern_memory KEPT** because it is global/shared), then backfill (if new ids) + restart +
     post-restart heartbeat wipe.
   - **Full `reset_dev.py --yes`** (wipes ALL dev + pattern_memory) is now reserved for a DELIBERATE
     whole-program restart the owner asks for — NOT a routine per-deploy step.
   - **STAGING is NEVER reset** (it is the curated multi-week forward-test accumulation tier — step
     10b already churns it only when the *selection* changes). KEEP candles + microstructure always.
   *Heartbeat-orphan lesson (bit me twice):* the OLD container keeps writing heartbeats until the
   restart kills it, so the `DELETE FROM heartbeats` must come AFTER the restart (a pre-restart wipe
   repopulates; a post-restart `ts < now-90s` delete misses the seconds-old new rows). After the
   restart wait ~40s, then verify: scoped/unchanged cohorts' trades intact, changed cohort `trades=0`,
   heartbeats == intended fleet size, expected patterns present, `errors=0`. (Skip ALL of step 9 when
   step 5 deployed nothing new — the common case now that leads are in WATCH mode.)
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
10b. **PHASE-2 STAGING MAINTENANCE (owner directive 2026-06-23 — every firing).** Staging is the
    curated **best-performers** pool: the **`staging` profile** of the one `kestrel` compose project
    (folded in 2026-06-28, owner — was its own `kestrel-staging` project; now the whole stack groups
    as ONE in Docker), ENV=staging, `STAGING_ENGINE=sim` until BingX VST keys exist, sharing postgres
    + Grafana, rows isolated by env='staging' + the `staging-` bot_id prefix. Its Grafana dashboard
    (`kestrel-staging`, regenerated by `build_staging_dashboard.py` as a FULL clone of the phase-1
    board) has the SAME verbosity as phase 1. Each firing, re-select and replace unproductive bots:
    ```
    # 0. refresh the in-container script FIRST — scripts are baked into the image, so a
    #    config-only iteration (no rebuild) leaves a STALE promote in the container (bit me
    #    iter-29: the baked promote lacked _LOCKBOX_SEED_PAIRS → would have ballooned staging
    #    6→68 bots). cp makes the loop self-correcting regardless of image staleness.
    docker compose cp ./scripts/promote_to_staging.py kestrel:/app/scripts/promote_to_staging.py
    # 1. re-select best performers: cells that BOTH win >50% AND are net-positive (owner
    #    criterion 2026-06-23), ranked by expectancy. Falls back to the lockbox leads
    #    (macd_cross/macd_rsi) as the seed when no dev cell qualifies yet. (Win-rate floor
    #    is ON TOP of net>0 so it never promotes a high-win/net-negative loser like a 50%/−$ cell.)
    docker compose exec -T kestrel python3 scripts/promote_to_staging.py --min-win 50 --stdout > bots.staging.json.new
    # 2. only churn staging if the selection CHANGED (else let it accumulate — same skip-if-identical
    #    discipline as the dev fleet):
    if ! diff -q bots.staging.json bots.staging.json.new; then
      mv bots.staging.json.new bots.staging.json
      docker compose cp ./bots.staging.json kestrel:/app/bots.staging.json
      docker compose exec -T kestrel python3 scripts/backfill_history.py --bots bots.staging.json --source gate  # NEW staging bot_ids need candles (iter-20 lesson)
      docker compose --profile staging up -d staging   # load refreshed fleet (staging profile of the kestrel project)
    else rm bots.staging.json.new; fi
    ```
    Promotion to Phase 3 (real money) stays **human-gated (§18)** — never automate it. When the owner
    adds BingX VST keys: set `STAGING_ENGINE=live` + real keys + `TESTNET=true` in `.env.staging` to
    upgrade staging from sim to demo-money execution (one env change; see [[project_three_phase_architecture]]).
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

- **ACTIVE — `exp_ensemble` (iter 52, EXPANDED iter 53):** **11 bots** = `ensemble_3of4` (new registered
  pattern: fires only when >=3 of the 4 deployed 1h leads agree on direction at the same candle) ×
  {ETH,DOGE,PEPE,XRP,SOL,ADA,CHZ,FET,GALA,TIA,WLD}, MEDIUM exit (tp2.0/sl1.0/hold6). First genuinely new
  structural signal in a while: backtest (1h maker, walk-forward OOS + lockbox) recent expR +0.07 /
  lockbox expR +0.14 (net +$2.63, R/R 1.68 — best R/R on record, breadth 6/7 pairs on the original 7-pair
  set). +EV pooled BOTH eras but recent margin thin, per-pair cross-era-robust core only 2 pairs
  (DOGE/XRP) — thinner evidence than cci_mom/sma_cross's 5-pair cores. **Iter 53 swept the remaining 28
  fleet pairs**: broad expansion REFUTED at the pooled level (recent +EV 18/28 pairs but LOCKBOX
  NEGATIVE, 8/26 pairs — data-mined), but 5 pairs are genuinely cross-era-robust (CHZ/FET/GALA/TIA/WLD) →
  added additively. NOT a confirmed edge (win <55% both eras). **Iter 54: only 2 closed trades total
  (100% win, +$0.36) — still far too thin to read; one of the 2 had been silently jammed as a ghost
  position (see iter 54's Daemon.stop() fix) and is only counted now that it's been recovered.** Watch live.
- **ACTIVE — `exp_robustwide/sma_cross` (iter 44, EXPANDED iter 45):** **14 bots** = sma_cross/wide ×
  {ETH,DOGE,PEPE,XRP,BNB,AVAX,ATOM,DOT,ETC,FIL,INJ,LINK,UNI,XLM}, WIDE exit (tp3.0/sl1.5/hold8). Still
  accumulating (38 trades as of iter 52, net −$1.06) — below the n=50 retirement bar. NOT a confirmed edge.
- **RETIRED `exp_robustwide/cci_mom` (iter 44→52):** the wide-exit cci_mom arm — 52 closed trades, 26.9%
  win, net **−$1.84**, profit factor **0.46** — crossed the iter-48 structurally-dead retirement bar
  (>=50 trades AND net<0 AND PF<1.0). Underperformed the medium-exit baseline throughout (iter 48-51).
  Rotated out for `exp_ensemble`. (Its 52 trades kept as history.)
- **RETIRED `exp_flowgate` (iter 5→44):** the 5m order-flow-alignment-gate arm — 30 live trades, 34.5%
  win, −$1.61 net = the gate does NOT rescue 5m (matches the iter-33/34 sub-cost microstructure finding).
  Conclusively refuted live; rotated out for `exp_robustwide`. (Its 30 trades kept as history.)
- ~~Iter 6: folded `exp_h1tp` into baseline~~; ~~Iter 4: `exp_h1tp`~~; ~~Iter 3: `exp_tod` seasonal~~; ~~Iter 2: `exp_h1run`~~; ~~Iter 1~~.

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

- **POINTS-PROGRAM LEAD (iter 55, owner-directed): `ensemble_3of4` + `hiwin33` inverted-geometry exit
  (tp 0.5×ATR / sl 1.5×ATR / hold 6) @ 1h maker — THE STRONGEST CROSS-ERA RESULT ON RECORD.** Under the
  points scoreboard (docs/13-points-framework.md): recent **76.2% win / +7.67 bps gross** (n=302, 8/10
  pairs+) · lockbox **77.8% / +13.59 bps** (n=338, 7/10 pairs+), 6-pair both-eras core
  (DOGE/XRP/SOL/ADA/BNB/AVAX), and **net-of-maker DOLLARS positive in both eras** (+$0.0031/+$0.0032/t).
  11 more cells clear the joint bar cross-era (all ensemble hiwin variants, macd_cross_ct/macd_rsi/
  sma_cross hiwin+scratch). Meets the §6.3 backtest legs (win≥70, ≥+4bps, ≥5 pairs, both eras).
  **BLOCKED for live by risk Rule 3 (R/R≥1.2, frozen §24 — owner decision)**; remaining evidence legs =
  ≥100-trade live forward test + points-DSR + taker stress. NOT yet a confirmed edge — the closest
  approach so far.
- **LEAD #5 (iter 52): `ensemble_3of4` (cross-signal voting confluence — >=3 of the 4 deployed 1h leads
  must agree) @ 1h, maker.** The FIFTH cross-era +EV signal and the first genuinely new confluence
  mechanism (leads voting against EACH OTHER, not gated by a regime/timeframe like every prior filter).
  Best-ever R/R on record (1.68 lockbox) and best-ever lockbox breadth (6/7 pairs), but the recent-era
  margin is thin (expR +0.07, n=218, breadth only 3/7) and the per-pair cross-era-robust core is just 2
  pairs (DOGE/XRP) — thinner evidence than cci_mom/sma_cross's established 5-pair cores. **Status: live
  FORWARD-TEST** — 6-bot 1h cohort (medium exit) on the robust core (ETH/DOGE/PEPE/XRP/SOL/ADA). NOT a
  confirmed edge (win <55% both eras). The 2-vote variant (`ensemble_2of4`) failed the pooled-recent bar
  despite decent per-pair breadth (4/7) — stays tested-but-undeployed, like vwap_revert.
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
- **LEAD #3 (iter 31): `cci_mom` (CCI(20) momentum breakout through ±100) @ 1h, maker.** The THIRD
  cross-era +EV signal and the FIRST genuinely-new family in a while: recent expR +0.12, lockbox
  **+0.07**, R/R 1.46, lockbox-positive on **4/6 pairs** (ETH/SOL/DOGE/XRP; BTC marginal, ADA −), and
  **~3× the activity of macd** (1182 vs 364 trades/yr) — the high activity serves the scalp mandate +
  gives a fast forward-test. **Status: live FORWARD-TEST** — 34-pair 1h cohort (medium exit) +
  6 in staging. Momentum-BREAKOUT works; the mean-reversion sibling `cci_revert` and `supertrend` were
  refuted same iter. Same modest-lead caveats (win <50%, 1h, ADA −). NOT a confirmed edge.
- **LEAD #4 (iter 32): `sma_cross` (9/21 SMA golden/death cross) @ 1h, maker.** The FOURTH cross-era
  +EV signal. Swept 14 UNDEPLOYED breakout/momentum algos at 1h; `sma_cross_9_21` was the ONLY one
  +EV in BOTH eras: recent expR **+0.14** (net +$4.01, R/R 1.48, n=466), lockbox **+0.12** (net +$3.92,
  R/R 1.32, n=535), OOS>IS both, +EV on **ETH/DOGE/XRP in BOTH eras** (≥3-pair breadth; BTC − like
  macd_rsi). Every other breakout (ema_cross +0.16 recent→−0.03 lockbox, donch_break, breakout_vol…)
  was data-mined. **Status: live FORWARD-TEST** — 34-pair 1h cohort (medium exit) + staging seed.
  Same modest-lead caveats (win <50%, BTC −, 1h not 5m). NOT a confirmed edge. Distinct from macd_cross
  (SMA vs EMA(12/26) signal-cross; fires on different bars) — diversifies the validated momentum-cross
  family. **This was the first deploy under the new SCOPED-reset policy: additive (34 NEW bot_ids), so
  it reset NOTHING — the existing fleet's 309 trades were PRESERVED through the deploy (proof the
  no-reset policy works; under the old full-nuke this would read dev=0).**
- **LIVE PSR + medium-exit DSR, corroborating (iter 49):** the ACTUAL deployed exit (medium, never
  before put through the rigorous test) is WEAKER than wide — recent DSR 0.873→0.549 (N20→200, all FAIL),
  lockbox DSR 0.088→0.004 (near-total collapse). Live forward-test PSR on 296 real trades across the 4
  medium leads: none clear even the raw (non-deflated) 0.95 bar (best macd_cross 0.810; sma_cross now
  NEGATIVE live at −0.198/PSR 0.107). Backtest's top cell (sma_cross) is the OPPOSITE of the current live
  top performer (cci_mom/macd_cross) — backtest-best and live-best disagreeing this sharply is itself the
  signature of noise, not a hidden edge. Two independent lenses now corroborate iter-47 decisively.
- **DEFLATED SHARPE — the formal stop-#2 verdict (iter 47):** `sma_cross_9_21/wide` is the closest signal ever
  found: per-trade Sharpe **+0.164**, **PSR(>0)=1.000** (Sharpe genuinely positive in-sample). But the **Deflated
  Sharpe** (multiple-testing-adjusted) is **0.976 at N=20 → 0.922 at N=60 → 0.813 at N=200**; the project's true
  search breadth is N≥60-200 (hundreds of backtests over 47 iters), so recent DSR < the 0.95 bar — and the untouched
  **lockbox FAILS at every N** (DSR 0.66 even at N=20, different winning param) ⇒ **stop-#2's "deflated Sharpe>0" is
  NOT met cross-era**. The strongest cell is statistically indistinguishable from the best-of-many-random-tries.
  Computed via the new `algo_search.py --deflated-sharpe` (Bailey & López de Prado). This is the loop's first
  proper implementation of its own formal edge bar — confirms LEAD, not edge.
- **FILL-MODEL ROBUSTNESS (iter 46):** `sma_cross/wide` survives even TAKER fees (~0.18%/trade) cross-era
  (+$0.0027 recent / +$0.0070 lockbox, 6-7/9 pairs) = the most FILL-ROBUST cell on record — does NOT need
  maker fills, the safest real-money candidate. `cci_mom/wide` DIES at taker (maker-dependent) — its edge
  is contingent on post-only limits actually filling. Both +EV at maker (live sim model); conviction
  ordering sma_cross > cci_mom.
- **PER-PAIR BREADTH (iter 43, `--by-pair`):** +EV under maker in BOTH eras — **sma_cross: 5 pairs
  (ETH/DOGE/PEPE/XRP/BNB)**, **cci_mom: 5 (ETH/DOGE/PEPE/XRP/AVAX)**, macd_rsi: 3 (SOL/DOGE/ADA),
  **macd_cross: 1 (DOGE only — the weakest lead, recent breadth 1/10)**. Shared robust core =
  **ETH/DOGE/PEPE/XRP**. **BTC is −EV for ALL leads in BOTH eras** (universal momentum-loser → dilutes
  the fleet; the pair to drop first). Breadth is real (≥3 pairs for 3 of 4 leads) but the aggregate
  still fails the formal PF/deflated-Sharpe bar (0/4) → concentrated, not yet a confirmed edge.
- **GROSS-EDGE DECOMPOSITION (iter 42):** with the new `--fees none` mode, ALL 4 leads are GROSS-positive
  in BOTH eras (sma_cross +0.0145/+0.0137, cci_mom +0.0114/+0.0067, macd_rsi/macd_cross weakly + recent /
  solidly + lockbox) → the 1h directional edge is REAL; the wall is purely the ~4bps fee. Under maker,
  **sma_cross + cci_mom are the two ROBUST leads (net-positive cross-era)**; **macd_cross/macd_rsi go
  net-NEGATIVE in the recent year** (gross edge < fee, survive only in lockbox) — they are the weaker
  pair. A sub-fee venue (§4) would ≈DOUBLE sma_cross's net edge (+0.0065→+0.0145). All still below the
  formal deploy bar (win <50%) → marginal, not a confirmed edge.
- The 5m hyper-scalp baseline is unchanged and remains −EV (no edge at 5m for any indicator incl.
  MACD — the cost floor dominates short TF). The earlier seasonality lead stays REFUTED (iter 4).
- **Note:** the 5m search is exhausted; the NEW frontier is INDICATOR strategies at 1h (owner opened
  macd/rsi/MA). macd_cross is the first hit. Next: validate per-pair deflated-Sharpe / PF, and test
  more indicator confluences (RSI+MACD, MA-cross variants) at 1h.

---

## REFUTED LEDGER (do not re-try these without a materially new variant)

- **Mean-reversion-FADE family, FINAL (iter 55c — tested at its NATURAL high-win geometry, the last
  materially-new variant it had):** rsi2_ct/raw · stoch_revert · cci_revert · bb_fade · wick_revert ·
  spike_fade · vwap_revert · compress_fade × 4 hiwin exits × 10 pairs, 1h maker, points scoreboard,
  recent + lockbox. 12/48 cells clear the joint GROSS bar cross-era but **ZERO clear maker-viable
  (≥ +4 bps) in both eras**; cci_revert/compress_fade/wick_revert gross-negative BOTH eras, spike_fade
  recent-negative, rsi2 lockbox-only. Corrected rule: fades carry a small real gross drift (+1..+3 bps)
  that is permanently sub-fee-shelf — **signal-only, never deployable at any geometry**. This is
  UNCONDITIONAL: no further fade re-tests; only a sub-1.3 bps venue (§4) could reopen it.
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
- **MACD family at 4h (`macd_cross_ct`, `macd_rsi`)** (iter 27) — recent-4h looked BETTER than 1h
  (macd_cross_ct/tight 52% win, expR +0.20) but the prior-year LOCKBOX is NEGATIVE on all variants
  (−0.06..−0.14), recent→lockbox collapse = data-mined (same signature as mom_adx 4h). The MACD edge
  is TF-SPECIFIC to 1h (+EV BOTH eras at 1h, dies at 4h). Don't deploy 4h MACD; don't re-test for activity.
- **CCI mean-reversion (`cci_revert`) + Supertrend (`supertrend`)** (iter 31) — 1h, maker, recent +
  lockbox. `cci_revert` (fade ±100): lockbox ≤0 (mean-rev fails again, consistent with RSI-2/stoch/
  wick/bb_fade). `supertrend` (ATR trend-flip): lockbox ≤0 / breakeven both exits. Neither deployable.
  (The SIBLING `cci_mom` — CCI BREAKOUT not fade — DID validate cross-era and was deployed; see CURRENT
  BEST. Momentum-breakout works, mean-reversion-fade doesn't — the recurring pattern.)
- **Pairs / stat-arb ratio mean-reversion** (iter 37) — 1h, 8 cointegration-candidate ratios (ETH/BTC,
  SOL/ETH, AVAX/ETH, …), z>±2σ entry. RECENT 4/8 +EV (looked good, 56–67% win); LOCKBOX **0/8** — all
  collapse, several −30..−50 bps/trade. Classic data-mining + the mean-reversion-fade death (decent win%
  but huge tail losses when the spread trends). 2-leg cost (8 bps) doesn't save it. Don't re-test ratio
  fades; mean-reversion-FADE of any kind dies in the lockbox (the consistent rule).
- **Cross-asset lead-lag (BTC just-closed bar → alt next bar)** (iter 36) — 1h, recent + lockbox, 7 alts.
  Contemporaneous BTC↔alt corr 0.72–0.86 (co-move, NOT tradeable); LEAD IC ~0 recent (0.00–0.018),
  NEGATIVE lockbox (−0.02..−0.04) → no cross-era lag; 0/7 net-positive after 4 bps cost both eras. At 1h
  the alt has already co-moved within the bar; lead-lag lives at sub-minute TFs where it hits the same
  sub-cost wall as order-flow (and isn't lockbox-testable at 5m). Don't re-test at ≥1h.
- **Microstructure order-flow imbalance, standalone** (iter 33-34) — real signal (depth_imb5 IC 0.14 BTC)
  but 0/60 cost-aware cells net-positive; gross 0.3–1.5 bps vs 4 bps maker fee. Needs round-trip < ~1.3 bps
  (venue/§4), not a signal to re-search. Aggressor `trade_delta` ≈ 0 (resting book predicts, flow doesn't).
  (Re-confirmed iter 38 + DEPLOYED as a live entry GATE — see the order-flow-gate deploy entry; the gate is
  a risk-quality filter that needs LIVE accumulation, NOT a backtest-able edge.)
- **Last untested momentum algos: `compress_vol_break`, `pullback_trend`, `body_go`** (iter 38) — 1h, maker,
  recent + lockbox, 6 pairs. `compress_vol_break/medium` was the recent-year STAR (expR +0.18, net +$4.36,
  IS→OOS +0.018) but **COLLAPSED in the lockbox** (−$1.18, avg −0.3 bps, IS→OOS −0.004) = textbook data-mining,
  same signature as the macd-4h / stat-arb rejects. `compress_vol_break/wide` is +EV in BOTH eras but MARGINAL
  (expR +0.10→+0.06, ~0.3–0.5 bps/trade) — below the deploy bar, the same non-promotable category as `stoch_ct`.
  `pullback_trend` (trend-continuation pullback) breakeven/marginal both eras; `body_go` recent-NEGATIVE /
  lockbox-positive = noise, not a cross-era edge. 0/6 clear §30. These were the last momentum-family algos in
  algo_search never run cross-era → the breakout-CROSS leads (macd_cross/macd_rsi/cci_mom/sma_cross) remain the
  ONLY cross-era survivors; momentum-fade and most breakout variants die in the lockbox (the consistent rule).
- **Breakout family minus sma_cross (`ema_cross`, `donch_break_10/20`, `breakout_vol`, `bb_break`,
  `compress_break`, `mom_align`, `mom_volexp`, `streak_go_3`, `macd_hist`, `rsi_cross_50`,
  `sma_cross_20_50`)** (iter 32) — 1h, maker, recent + lockbox. ALL +EV-ish in the recent year
  (ema_cross the TOP at expR +0.16) but NEGATIVE / breakeven in the prior-year lockbox (ema_cross
  −0.03, donch_break −0.00/−0.02, breakout_vol −0.01) → classic recent-year data-mining, the same
  false-kin shape as the macd leads' rejects. Only `sma_cross_9_21` survived BOTH eras (deployed; see
  CURRENT BEST). Momentum-BREAKOUT-cross can validate, but most breakout variants are single-era flukes
  — the lockbox is the only thing that tells them apart. Don't deploy these for activity.
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

- **Blanket ensemble_3of4 expansion to all 34 fleet pairs** (iter 53) — swept the 28 untested pairs
  (1h maker medium, recent + untouched prior-year lockbox). Pooled RECENT is +EV (avg $0.0049/t, 18/28
  pairs+) but pooled LOCKBOX is NEGATIVE (avg −$0.0061/t, only 8/26 pairs+) — the classic recent-improves/
  lockbox-collapses data-mining signature seen throughout this ledger (ema_cross, vwap_mom, 4h MACD,
  compress_vol_break…). A blind "deploy everywhere" expansion is REFUTED. The productive version is
  per-pair: only CHZ/FET/GALA/TIA/WLD are +EV in BOTH eras (deployed, see CURRENT COHORT) — concentrate,
  don't blanket-deploy. Don't re-try a full-fleet ensemble expansion without a materially new filter.
- **Higher-timeframe (4h) EMA9/21 trend confirmation on the 1h leads** (iter 51) — `--htf-confirm` in
  algo_search.py, 1h maker medium exit, robust core (ETH/DOGE/PEPE/XRP/BTC), recent + lockbox. Filtering
  entries to agree with a genuinely SEPARATE higher-timeframe's trend (not a same-TF regime gate like ADX/
  volatility) lifts 3/4 leads in the recent year but **sma_cross — the project's best-ever cell — craters
  in the lockbox (+0.0112→+0.0028)**; macd_cross gets WORSE recent; only macd_rsi lifts in both (small,
  noisy, ~35% smaller sample). 0/4 clear win>55% filtered or unfiltered. Same recent-improves/lockbox-
  collapses signature as every other confluence filter (ADX iter-23, volatility-floor iter-39, session
  iter-50). Closes the same-TF-vs-cross-TF confluence question — don't re-test HTF trend filters on the
  current or future leads without a materially new variant (e.g. a different HTF indicator, not EMA9/21).

- **UTC session breakdown on the CURRENT 1h leads (`cci_mom`, `macd_cross`)** (iter 50) — the iter-3/4
  seasonality test only covered the old 5m/4h `mom_adx`/`triple_mom` patterns; this re-tested session on
  the leads actually deployed today. 3-way triangulation (recent backtest / lockbox backtest / live
  forward-test) each nominated a DIFFERENT best/worst session — recent liked London/Asian, lockbox liked
  US/London with opposite signs, live liked London for unrelated small-n reasons. No session is
  consistently good or bad across all three views = noise, not a lever. Don't re-test session/time-of-day
  filters on any current or future lead without a materially new angle.

- **VWAP volume-weighted anchor (`vwap_mom`, `vwap_revert`)** (iter 41) — 1h, maker, recent + lockbox,
  10 pairs. The one institutional level never tested (all 40 prior families were price-only). `vwap_mom`
  (reclaim a rising rolling-VWAP) was the recent-year STAR (+$0.0073/t, win 43.5%, n=898, PF 1.45, expR
  +0.11) but **collapsed in the lockbox** (−$0.0046/t, expR +0.01) = data-mined, same recent-+/prior-−
  signature as ema_cross / macd-4h / compress_vol_break. `vwap_revert` (fade >1 ATR from VWAP) NEGATIVE
  both eras (−$0.0036/t) = the mean-reversion-fade death again. 0/4 clear the bar both eras. The volume
  anchor doesn't beat the cost floor; both algos stay in algo_search (tested) but undeployed. Don't
  re-deploy VWAP for an edge.

- **The 1h cross-leads at 15m (TF down-shift for activity)** (iter 40) — 15m, maker, recent + lockbox,
  10 pairs (15m lockbox data IS available, ~35k candles/pair — not the 5m wall). macd_cross/macd_rsi/
  cci_mom/sma_cross at 15m fire **~20× more** than at 1h (n=5334 recent / 3494 lockbox vs ~200) but go
  **net-negative-to-flat in BOTH eras** (best −$0.0020/t recent, $0.0000/t lockbox, win 38–42%, 0/4
  clear the bar). The 1h cross-edge is **TF-SPECIFIC** (like 4h MACD dying; 1h the only survivor TF) —
  it does NOT extend down to 15m; the lower TF just multiplies the ~4bps cost drag. Don't re-deploy
  the leads at 15m for activity — more trades, faster bleed.

- **Cost-clearing VOLATILITY-FLOOR filter on the 1h leads** (iter 39) — 1h, maker, recent + lockbox,
  10 pairs. Restricting macd_cross/cci_mom/sma_cross to the high-volatility regime (`--regime
  volatile`, ATR14>ATR50×1.5 — the "only fire when the move clears the fee" idea iter-1's NEXT
  flagged) does NOT lift expectancy and **starves the sample to n=3–6 trades/yr across 10 pairs**
  (RECENT macd_cross −$0.06/t n=3; LOCKBOX cci_mom +$0.05/t n=6) — statistically unusable, the same
  thinness that killed `--regime volatile` at 5m. SAME firing measured the **unfiltered leads are now
  flat** (best sma_cross +$0.0096/t recent / +$0.0100/t lockbox, win 44–47%, 0/3 clear the bar) =
  regressed-to-break-even, original +EV was a single-regime artifact. Closes iter-1's last NEXT item:
  no volatility/regime gate rescues the leads. Don't re-test a volatility floor as an edge lever.

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

### Iteration 58 — 2026-07-12 (RETIRED `exp_robustwide/sma_cross` — crossed the structurally-dead bar; attempted an additive REPLACEMENT expansion of `ensemble_3of4/hiwin33` onto its freed pairs — recent-era backtest looked promising on 5/10 pairs, but the LOCKBOX fetch failed three times across all 3 fallback exchanges (infra outage, not a refutation); expansion DEFERRED as an open lead, not deployed, not refuted; fleet 184 → 170)

- **MEASURE:** fleet healthy pre-action (7/7 services up). Overall dev 760 closed / 43.3% win /
  net −$8.15 (classic baseline, unchanged shape). `exp_hiwin` at 6 closed trades (still too early to
  read) grew to **13 by the time this iteration's redeploy landed** (macd_rsi 6/66.7%, sma_cross
  4/100%, macd_cross 2/100%, ensemble_3of4 1/100%, net +$0.78 pooled) — encouraging direction, still
  nowhere near the 30-trade read bar. **`exp_robustwide_sma_cross` crossed 50 closed trades (52 at
  measure time, 57 by redeploy) with net −$2.33 and PF 0.44** — meets the MODE §Cell-viability
  structurally-dead bar (≥50 trades AND net<0 AND PF<1.0) for the first time this loop has had to
  act on it.
- **DIAGNOSE / RETIREMENT DECISION:** per the Cell-viability rule, prefer FIXING over removing — but
  no plausible fix exists within `exp_robustwide`'s own wide-exit frame (tp3.0/sl1.5, R/R2.0): that
  IS the "fix" that was already tried (iter 44, widen the stop to reduce premature stop-outs), and
  the points program (iter 55-57) has since produced direct, cross-era-validated evidence that
  `sma_cross`'s real edge (such as it is) lives in the INVERTED hiwin geometry, not the wide one —
  `exp_hiwin_sma_cross` is already live on the same signal with better evidence behind it. Retiring
  the wide variant is not "cutting activity to lose less" (forbidden by MODE) — it is dropping a
  cell that its own successor has already superseded. **Removed all 14 `exp_robustwide_sma_cross`
  bot_ids from `bots.json` via `exp_candidate.json`; its 57 trades stay in the DB as permanent
  history (nothing deleted).** `exp_robustwide_cci_mom` (already retired iter 52, kept as frozen
  history) and the 4 `exp_hiwin` arms + `exp_ensemble` kept byte-identical.
- **HYPOTHESIZE / BACKTEST — additive replacement attempt:** retiring the 14 robustwide bot_ids frees
  10 pairs never tested under hiwin geometry (ETH, PEPE, ATOM, DOT, ETC, FIL, INJ, LINK, UNI, XLM).
  Ran `ensemble_3of4/hiwin33` (the most fee-robust arm per iter 57) on all 10, `--points --fees
  realistic`, walk-forward OOS + lockbox:
  - **Recent era (succeeded cleanly):** pooled −2.35 bps, joint bar NOT met at the pooled level —
    but **5/10 pairs individually clear +EV**: ETH +10.5 bps@81%(n31), DOT +9.6 bps@77%(n31), FIL
    +14.9 bps@76%(n37), LINK +13.0 bps@82%(n33), UNI +16.7 bps@79%(n33). The other 5 are negative
    (PEPE −2.6, ATOM −1.4, ETC −6.6, INJ −31.5, XLM −67.8 bps@52% — XLM catastrophically so).
  - **Lockbox era: BLOCKED, not negative.** Three separate fetch attempts (full 10-pair ×2, narrow
    5-pair ×1 targeting just the recent-positive set) **all failed identically** — every pair, every
    attempt, across all 3 fallback exchanges: `okx` RequestTimeout, `kraken` RequestTimeout /
    BadSymbol / "only 721 candles", `gate` `BadRequest: "Candlestick too long ago"` specifically on
    the offset-365 lockbox window. **This is very likely transient, not structural** — iter 57's
    lockbox fetch succeeded cleanly one day earlier on a different (already-cached) pair set using
    the identical exchange fallback chain, and the SAME-DAY recent-era fetch for these exact 10 pairs
    worked fine. Reads as an exchange-side outage or rate-limit hitting the specific 2-years-back
    date range, not a pair-availability wall.
- **DECISION — deferred, not refuted:** the loop's own standard requires walk-forward OOS AND an
  untouched lockbox before a deploy; recent-only is explicitly insufficient (the project's whole
  REFUTED LEDGER is built on catching exactly this recent-good/lockbox-bad pattern). Forcing a deploy
  on recent-only evidence, or worse, writing this off as refuted when the lockbox was never actually
  measured, would both be dishonest. **NO ensemble_3of4 expansion deployed this iteration.** Logged
  as an **OPEN LEAD for the next firing**: re-run the lockbox fetch for {ETH, DOT, FIL, LINK, UNI} ×
  `ensemble_3of4/hiwin33` once exchange access recovers; deploy additively only if the intersection
  of recent-positive ∩ lockbox-positive is non-empty. **Explicitly NOT added to the REFUTED LEDGER** —
  the evidence is incomplete, not negative.
- **APPLY:** net effect this iteration is a **pure retirement** — fleet 184 → 170 bots. Dedup guard
  (`bot_registry.py check`) — 0 NEW / 170 SEEN (expected: nothing new added, only removed).
  `build_exp_cohort.py` iteration 10→11.
- **LINT/COMMIT/CI:** no `src/`/`tests/` changes (config-only); `ruff format --check` + `ruff check`
  clean regardless. Committed `exp_candidate.json` + `bots.json` (`bf086a0`), CI green.
- **REDEPLOY / RESET:** `backup_db.py` run from host first (154 MB lean dump). Config-only change →
  `docker compose restart kestrel` (no rebuild). Verified: heartbeats settled to exactly 170 fresh
  rows within ~90s of restart, 0 stale `exp_robustwide_sma_cross` rows remaining, 0 errors. Per
  §RESET POLICY this is a pure removal (not a config change to a surviving bot_id) — **no scoped
  reset needed**, nothing to re-baseline.
- **10b STAGING:** re-selection grew 3→4 bots (added `staging-DOGEUSDT-1h-cci_mom-01`, n=15, 53% win,
  net +$0.52 — DOGE re-qualified after dipping below the bar at iter 57); nothing removed. Backfilled,
  restarted, healthy.
- **CHECK STOP:** neither condition holds. Owner's 70%-win/100-trade/15%-daily bar (condition 1) is
  untouched by a pure retirement. Condition 2 (deflated Sharpe > 0) still fails per iter 56's
  measurement — this iteration didn't re-run DSR (no new signal was validated to test), so that
  verdict stands unchanged. Loop continues; next firing should retry the lockbox fetch for the open
  lead before anything else.

### Iteration 57 — 2026-07-11 (BUILT the REALISTIC fee model: a real backtest-vs-live-sim gap found and closed — the flat "maker" mode in algo_search.py was optimistic vs `src/execution/simulation.py`'s actual live behaviour; re-ran the S1 hiwin survivors under it — the edge survives (unlike iter-56's pure-taker stress), `ensemble_3of4` emerges as the most fee-robust arm; NO deploy, exp_hiwin live leg (38h old, 6 trades) left untouched)

- **MEASURE:** fleet healthy (7/7 services up, `kestrel` 38h uptime). Overall dev 709 closed / 42.7%
  win / net −$10.50 (classic baseline, unchanged picture). **`exp_hiwin` now has its first live trades:**
  6 closed (macd_cross 1, macd_rsi 3, sma_cross 2; ensemble_3of4 still 0) — 5/6 exited via `take_profit`,
  1 via `timeout`, net +$0.26. Far below the 30-trade read bar, but directionally consistent with the
  backtest thesis and confirms the cohort fires normally. `exp_robustwide_sma_cross` at 48 closed trades
  (net −$2.31, PF 0.41) — approaching but not yet at the ≥50-trade structurally-dead bar; flagged for
  next iteration, not actionable yet.
- **DIAGNOSE (the finding that drove this iteration):** inspecting exp_hiwin's first live fee charges
  (`fee_entry_usdt`/`fee_exit_usdt` relative to notional, not margin — a live 20x $10 bucket has $200
  notional) showed the correct maker entry rate (0.02%) on all 6, but the ONE `timeout`-closed trade paid
  0.04% (taker) on its exit while the 5 `take_profit`-closed trades paid 0.02% (maker) on exit. Traced
  this to `src/execution/simulation.py::close_position()` (lines ~146-160): maker exit treatment applies
  **ONLY to `take_profit`** — stop_loss/timeout/liquidated/trailing_stop/manual market out and pay taker
  fee + 0.05% slippage even in maker mode ("you cannot post-only your way out of an adverse move").
  **`scripts/algo_search.py`'s existing `--fees maker` mode does NOT replicate this** — it flat-rates
  EVERY exit at the maker fee with zero slippage regardless of `close_reason` (confirmed in
  `src/backtest/runner.py::_simulate_close`, which the header comment claims is "identical to the live
  simulation engine" — that claim is false for the maker path specifically). This matters most for
  hiwin's inverted geometry (wide SL, narrow TP): the SL-side losses are exactly the exits the flat-maker
  backtest was undercounting, so the S1/S1b maker-mode numbers were somewhat rosier than what live will
  actually charge.
- **HYPOTHESIZE / BUILT:** a `--fees realistic` mode in `scripts/algo_search.py` (frozen `runner.py`
  untouched — same runtime-monkeypatch precedent as the existing fee patches): keeps the always-maker
  entry treatment (correct, matches live), but monkeypatches the exit-settlement function so ONLY
  `take_profit` exits get maker fee + zero slippage; every other close reason pays taker fee (0.04%) +
  slippage (0.05%), replicating `simulation.py` exactly. This closes a real backtest-fidelity gap, not
  a re-test of anything in the REFUTED LEDGER.
- **BACKTEST — S1 hiwin survivors (4 leads × 4 hiwin exits, 9-pair union, 1h, walk-forward OOS +
  untouched lockbox) under `--fees realistic`:**
  - **Recent: 13/16 clear the points joint bar (5 maker-viable ≥+4bps)** — top cell `ensemble_3of4/
    hiwin33` 76.0%/+6.80bps (n=271, vs +8.42bps under flat-maker — a ~1.6bps haircut, small because
    76% of exits genuinely ARE take_profit for this geometry). `ensemble_3of4/scratch` 73.1%/+11.12bps.
  - **Lockbox: 16/16 clear, 14 maker-viable** — top cell `ensemble_3of4/hiwin33` 79.3%/+15.35bps (n=299,
    vs +16.66bps flat-maker — only ~1.3bps haircut). `macd_rsi/hiwin33` 78.9%/+12.48bps.
  - **Cross-era maker-viable intersection (both eras ≥+4bps): 5/16 — ALL FOUR `ensemble_3of4` exits
    (scratch/hiwin50/hiwin33/hiwin43) + `macd_rsi/scratch`.** This is essentially unchanged from the
    flat-maker cross-era set — **the realistic fee model does NOT kill the hiwin edge** (unlike iter-56's
    pure-taker stress, which collapsed 15/16 → 1/16). The asymmetry cuts the OTHER way from what a naive
    "maker mode was too generous" worry would predict, because hiwin's high win rate means most trades
    really do exit at the cheap maker leg.
  - **Refinement: `ensemble_3of4` is the most fee-robust of the 4 hiwin arms** — it holds maker-viable
    status on ALL its exit variants in both eras under the realistic model, while `macd_cross` and
    `sma_cross`'s hiwin variants (maker-viable under flat-maker) drop to signal-only (<4bps) in the
    recent era specifically. `macd_rsi` sits in between (2/4 exits stay maker-viable cross-era).
- **HONEST READ:** this is a validation of the backtest tooling, not a new edge claim — it closes a
  fidelity gap and the answer came back reassuring rather than damning (unusual for this project's fee
  research — see `project_maker_fee_meanrev_research`'s taker-refute of the OLD entries — but this is a
  DIFFERENT geometry with a genuinely high win rate, so the asymmetry works in its favor here). The
  iter-56 DSR failure is UNCHANGED by this finding (DSR tests statistical significance of the return
  series' magnitude, and realistic fees only trim the top cell's edge by ~1.3-1.6bps — nowhere near
  enough to flip a 0.57-0.76 DSR into the 0.95+ pass zone). The live leg remains the only evidence type
  that isn't subject to either haircut.
- **APPLY:** no bots.json change — `exp_hiwin`'s live leg (38h old, 6 trades, nowhere near the 30-trade
  read bar) stays untouched per standing pref #9. This iteration's shipped artifact is the `--fees
  realistic` capability in `scripts/algo_search.py` itself (a genuinely new, reusable research tool,
  not a re-run) — dedup guard (`bot_registry.py check`) not applicable since no bot config changed.
- **LINT/COMMIT/CI:** `ruff format --check` + `ruff check` on both `scripts/algo_search.py` (clean,
  outside CI's src/tests scope) and the CI scope `src/ tests/` (unaffected, clean). Reports archived:
  `reports/algo_search_20260711-{003338,003541}.md`.
- **CHECK STOP:** neither condition holds. `exp_robustwide_sma_cross` is 2 trades from the structurally-
  dead retirement bar — watch at next firing.

### Iteration 56 — 2026-07-10 (QUEUED S1b RUN: points-DSR + taker fill-stress on the S1 hiwin survivors — the best signal in project history FAILS the formal multiple-testing bar in BOTH eras, and its edge is almost entirely maker-fee-dependent; NO deploy, live leg untouched)

- **MEASURE:** fleet healthy (184/184 heartbeats fresh, 0 errors, all 7 compose services up). Overall
  dev slate 642 closed / 42.7% win / net −$9.83 / PF 0.82 (classic-exit baseline, unchanged picture).
  Close-reason mix: timeout 44.7%, stop_loss 28.0%, manual 13.7% (redeploy-restart artifacts), take_profit
  12.3% — still the familiar "TP too far away, times out first" shape on the OLD exit geometry, exactly
  what the hiwin program targets. **`exp_hiwin` (14-23h old, deployed iter 55b): 0 closed trades, 0
  signals of ANY outcome (not even rejected) across all 23 bots.** Verified this is NOT a repeat of the
  iter-20/iter-25 dark-cohort bugs: candles fresh (latest closed 1h candle present), heartbeats
  `running`, patterns already fire fine in sibling cohorts (baseline macd_cross/macd_rsi/sma_cross/cci_mom
  all fired 3-15 times combined in the same 14h window) — the fires just landed on OTHER pairs (UNI, HYPE,
  ETC, LTC, APT, FET, OP) that don't overlap exp_hiwin's specific both-eras-robust pair cores. At the
  observed baseline firing rate, zero fires on a 5-6-pair subset over 14h is well within Poisson noise.
  **Confirmed correctly wired, genuinely too early — do not misread as dead.**
- **DIAGNOSE / HYPOTHESIZE:** with exp_hiwin's live leg untouchable this early (§9), this iteration's
  research value comes from the QUEUED S1b task left open at iter 55c: the points program has never been
  checked against (a) the formal multiple-testing correction (deflated Sharpe, built iter 47) or (b) a
  taker-fee fill-stress (built iter 46) — both existing tools, never run on the hiwin survivors specifically.
- **BACKTEST — three runs, `scripts/algo_search.py --points`, 1h maker/taker, 9-pair union of the 4 arms'
  both-eras cores, walk-forward OOS + untouched lockbox:**
  - **Points bar (both eras, maker):** recent 15/16 combos clear · lockbox **16/16** clear, ALL
    maker-viable — lockbox top cell `ensemble_3of4/hiwin33` hits **79.4% win / +16.66 bps** (n=301),
    the single best cross-era cell ever measured on this project. Consistent with iter 55's finding,
    even stronger on a tighter pair set.
  - **Deflated Sharpe (stop-#2's actual test, both eras independently):** BEST cell each era —
    recent `ensemble_3of4/scratch` Sharpe +0.123 (PSR=0.965); lockbox `ensemble_3of4/hiwin33` Sharpe
    +0.100 (PSR=0.946). **Both FAIL the DSR>0.95 bar at every trial-count assumption tested** —
    recent DSR 0.762(N=16)/0.667(N=48)/0.567(N=160); lockbox DSR 0.743/0.659/0.572. Same failure
    signature as iter 47's sma_cross/wide (the previous closest signal) — **no signal in this
    project's history has ever cleared the formal multiple-testing bar**, including the strongest one.
  - **Taker fill-stress (§13's "maker path REQUIRED" claim, tested directly):** replacing maker with
    taker fees collapses the survivor set from 15/16 → **1/16** (only `ensemble_3of4/scratch` survives,
    barely, signal-only shelf +3.38 bps) — every hiwin-exit cell goes net-negative under taker (−2 to
    −9 bps). **The entire hiwin edge is maker-fee-dependent**, confirming §13's design constraint is
    load-bearing, not decorative — live fill quality (real maker-fill rate, not the sim's assumption)
    is now the single biggest swing factor for whether exp_hiwin's live leg succeeds.
- **HONEST READ:** backtest evidence alone — even at its best, cross-era-robust, points-bar-clearing —
  does NOT statistically distinguish this signal from a data-mined artifact at 95% confidence. This
  doesn't refute exp_hiwin (DSR failing is the norm for real trading signals with modest per-trade
  edges over finite samples, not proof of nothing-there), but it means the **live ≥100-trade forward
  test is not a formality — it's the only evidence type left that isn't subject to this haircut.**
  Watch item added: if live maker-fill rates run materially below the sim's ~90-100% assumption
  (`project_maker_fee_meanrev_research`), the taker-stress result says the edge likely does not survive.
- **APPLY:** no deploy. Redeploying/rotating `exp_hiwin`'s bot_ids now would restart its <1-day-old
  live leg — directly against standing preference #9 ("never apply or remove a strategy off a short
  read"). Nothing else cleared bar to justify a NEW cohort. **10b staging maintenance DID apply**
  (routine, does not touch dev/exp_hiwin): re-selection added 1 bot (`staging-TRXUSDT-1h-cci_mom-01`,
  n=10, 70% win, net +$0.77) to the existing 2-bot pool (APT/DOGE cci_mom); backfilled, staging
  profile restarted, healthy.
- **LINT/COMMIT/CI:** RESEARCH_LOOP.md-only change; `ruff format --check` / `ruff check` unaffected
  (no src/tests touched). Reports archived: `reports/algo_search_20260710-{003422,003625,003853}.md`.
- **CHECK STOP:** neither condition holds — no config has reached 70%/100 live trades (owner target),
  and the DSR test just formally confirmed condition 2 (deflated Sharpe > 0) is NOT yet met by any
  candidate, hiwin included. Loop continues; next queued item is simply time — let exp_hiwin's live
  leg accumulate toward its 30-trade read / 100-trade trust bar.

### Iteration 55c — 2026-07-09 (S2 mean-rev re-audit + S4 marginal revival under the points bar: 12/48 cells clear the joint gross bar cross-era but ZERO clear maker-viable in both eras → NO deploy; the "fade always fails the lockbox" rule REFINED, four fade algos permanently refuted; Grafana points row shipped)

- **SCOPE (docs/13 §5 S2 + S4, completing task #8):** 12 algos — the full mean-rev/fade family
  (`rsi2_ct`, `rsi2_raw`, `stoch_revert`, `cci_revert`, `bb_fade`, `wick_revert`, `spike_fade`,
  `vwap_revert`, `compress_fade`) + the marginal-revival candidates (`stoch_ct`, `compress_vol_break`,
  `vwap_mom`) × the 4 hiwin exits × 10 pairs × 1h maker, `--points`, walk-forward OOS recent
  + untouched prior-year lockbox. 48 combos/era. Legacy §30 verdict on the same runs: 0 survivors
  (structurally blind to g<1.2, as expected).
- **RESULT — the joint gross bar (pwin ≥65% AND avg_bps >0, n≥30) cross-era:** recent 16/48,
  lockbox 24/48, **intersection 12/48** — bb_fade/{hiwin33,hiwin43,scratch}, stoch_revert/{hiwin33,
  scratch}, vwap_mom/{hiwin33,hiwin43,scratch}, compress_vol_break/{hiwin33,hiwin43},
  vwap_revert/hiwin33, stoch_ct/scratch. **BUT zero cells clear the maker-viable shelf (≥ +4 bps
  gross) in BOTH eras** — best cross-era cells sit at +2..+4 recent / +1..+3 lockbox
  (stoch_revert/scratch +4.04 recent but +1.90 lockbox; rsi2 family +4.6..+5.0 lockbox but
  NEGATIVE/flat recent = single-era regime artifact). Gross R-expectancies hover 0.00–0.04 —
  roughly HALF the S1 momentum survivors' magnitude.
- **VERDICT: NO deploy candidate.** The S1 momentum leads (iter 55: 12 cells at ≥ +4 bps BOTH eras,
  top cell +7.7/+13.6) strictly dominate everything the fade family produces; exp_hiwin stays the
  sole live cohort of the points program, unchurned.
- **THE REFINEMENT (honest update to the ledger's oldest rule):** "mean-reversion-fade always fails
  the lockbox" was partly a GEOMETRY artifact — at classic R/R≥1.2 brackets the fades were
  gross-negative; at their natural inverted geometry several are gross-POSITIVE cross-era. The
  corrected rule: **fade signals carry a small real gross drift (+1..+3 bps) that is permanently
  sub-fee-shelf — signal-only, never deployable**; momentum-breakout remains the only family that
  clears the maker floor cross-era.
- **PERMANENT REFUTATIONS (ledger):** `cci_revert`, `compress_fade`, `wick_revert` — gross-NEGATIVE
  in both eras even at natural geometry (nothing left to rescue); `spike_fade` — recent-negative
  (−7..−10 bps, all exits), lockbox marginal = single-era; `rsi2_*` under hiwin — lockbox-only.
- **ALSO SHIPPED (task #8's other half):** Grafana **Points Scoreboard** section on the main board
  (9 panels: fleet points win %, points expectancy vs the 65/70 + 0/+4 thresholds, aggregate points
  today, exp_hiwin live-leg trade counter toward 100, rolling points win-rate vs the 70% line,
  per-lead expectancy, per-arm exp_hiwin table; 153 → 162 panels, commit `b7f980d`).
- Reports: `reports/algo_search_20260709-104429.md` (recent) + `-104957.md` (lockbox). No fleet
  change → nothing reset, no rebuild. S1b (points-DSR + taker stress on the S1 survivors) stays
  the queued next research step; the exp_hiwin ≥100-trade live leg accumulates.

### Iteration 55b — 2026-07-09 (OWNER AUTHORIZED RULE 3 + DEPLOYED exp_hiwin: "i authorize you to amend rule 3, deploy it" — the live leg of the points program begins)

- **RULE 3 AMENDED (CLAUDE.md v2.6 FIRST per §24, then `src/risk/manager.py`):** `_MIN_RR` 1.2 → **0.25**.
  The floor now rejects only degenerate brackets; strategy quality gates on the docs/13 §6.1 points joint
  bar at the research layer. §26 `tp_atr_multiplier` range floor 0.8 → 0.4 (needed by the hiwin presets).
  Rule-3 tests rewritten (floor boundary 0.25, hiwin geometry passes, classic 1.2 geometry unaffected,
  zero-SL still rejected) — full suite green, lint green.
- **DEPLOYED `exp_hiwin` (23 NEW bots, dedup 23 NEW / 161 SEEN, fleet 161 → 184):** each arm on its own
  both-eras-points-positive core from the S1 `--by-pair` tables —
  `ensemble_3of4`/hiwin33 × {DOGE,XRP,SOL,ADA,BNB,AVAX} · `macd_rsi`/hiwin50 × {ETH,DOGE,SOL,ADA,BNB,ATOM}
  · `macd_cross`/hiwin50 × {ETH,PEPE,SOL,ADA,BNB} · `sma_cross`/hiwin50 × {ETH,DOGE,PEPE,SOL,ADA,BNB}.
  exp_robustwide + exp_ensemble kept VERBATIM (additive deploy → **reset nothing**, backup taken).
  Rebuilt image (risk/manager.py is baked), verified `_MIN_RR = 0.25` live in-container, backfilled the
  23 new bot_ids (720 × 1h each), **184/184 heartbeats fresh · 0 stale · 0 errors**. Commit `2106e00`.
- **PURPOSE:** the ≥100-trade LIVE leg of the §6.3 program target (points win ≥ 70%, expectancy ≥ +4 bps,
  breadth — both eras ✓ done, live ✗ starts now). Expected activity ~25-30 closed/wk across the 4 arms →
  first readable sample in ~2 weeks, 100 trades in ~4. Known accepted divergences: live volume_confirm 1.1
  floor; Rule 4 keeps the conservative taker fee bar (low-ATR candles reject `fee_not_viable`).
- **WATCH RULES:** judge on TRADE COUNT not clock (≥30 to read, ≥100 to trust — standing pref #9); the
  cohort is judged on the POINTS scoreboard (§2.3); retirement bar if it goes structurally dead per the
  MODE rule. Queued next: points-DSR + taker stress (S1b), S2 mean-rev re-audit, Grafana points row.

### Iteration 55 — 2026-07-09 (OWNER-DIRECTED: the POINTS FRAMEWORK + S1 HiWin sweep — the strongest cross-era result in project history: 12 cells clear the joint points bar in BOTH eras at maker-viable gross; ensemble_3of4/hiwin33 hits 76%/78% win + net-of-maker-$-positive both eras; LIVE DEPLOY BLOCKED on risk Rule 3 (§24 owner decision))

- **TRIGGER:** owner — "change the target not based on profit but on the pips/points and the winrate,
  with no considering of profit value; we need scalability; write everything to docs/." Then "lets try
  your way, create to do list, then start to ship it." Framework written to **docs/13-points-framework.md**
  (+ all stale docs refreshed to the iter-54 state, commit `ad4f7ee`). Scoreboard = gross points (bps of
  entry price) + win rate; win rate shown to be an exit-geometry property (win ~ 1/(1+g)); §6.1 joint bar
  = points win >= 65% AND points expectancy > 0 (win alone is purchasable and never counts).
- **BUILT:** (1) `algo_search.py --points` — gross-points scoring (win%, avg/median bps, points PF, gross
  R-expectancy, per-pair points breadth) + 4 inverted-geometry exit presets (hiwin50 tp0.6/sl1.2/h4,
  hiwin43 0.6/1.4/h4, hiwin33 0.5/1.5/h6, scratch 0.5/2.0/h3) + a runtime bypass of risk Rule 3's R/R>=1.2
  floor (research-process-only monkeypatch, frozen file untouched — same precedent as the fee patch);
  (2) `scripts/analyze_excursions.py` — the S3 MFE/MAE miner over the live `trade_context` corpus.
- **S3 EXCURSION FINDINGS (482 live trades, uncensored post-entry paths):** the leads' favorable drift is
  REAL and FRONT-LOADED — macd_cross e-ratio 1.40 at k=1 / 1.55 at k=2 (median +50 bps favorable in the
  FIRST candle vs −36 adverse); macd_rsi similar (1.13–1.26); cci_mom weak (<1 early); **sma_cross enters
  AGAINST the initial move** (e-ratio 0.35 at k=1 — explains its persistent live-vs-backtest divergence).
  Pooled median MFE@4 = +95 bps vs median |MAE|@4 = 91 bps. The empirical brackets it derives (g~0.4–0.7)
  independently corroborate the hiwin presets.
- **S1 BACKTEST (1h, maker, 5 leads × {4 hiwin + medium control} × 10 pairs, walk-forward OOS +
  untouched prior-year lockbox, points scoreboard):**
  - **RECENT: 18/25 combos clear the joint bar. LOCKBOX: 19/25.** Cross-era (both eras, maker-viable
    >= +4 bps gross): **12 cells** — every ensemble_3of4 hiwin variant, macd_cross_ct/{hiwin50,scratch},
    macd_rsi/{hiwin50,hiwin33,scratch}, sma_cross/{hiwin50,hiwin33,hiwin43}.
  - **Top cell `ensemble_3of4/hiwin33`: recent 76.2% win / +7.67 bps gross (n=302, 8/10 pairs+) ·
    lockbox 77.8% / +13.59 bps (n=338, 7/10 pairs+)** — 6 pairs positive in BOTH eras
    (DOGE/XRP/SOL/ADA/BNB/AVAX; BTC negative both, the usual). Runner-up ensemble/hiwin50:
    +9.29@69.3% / +8.92@67.8%.
  - **NET-OF-MAKER DOLLARS are positive in BOTH eras for the ensemble hiwin cells** (recent
    +$0.0061/t hiwin50, +$0.0031 hiwin33; lockbox +$0.0013/+$0.0032) with IS→OOS positive — i.e. this
    is not points-only cosmetics; the geometry crosses the fee floor. The legacy §30 verdict on the SAME
    run reads "0/25 clear" because R/R>=1.2 structurally cannot see a g<1.2 result — the points bar was
    built precisely for this blind spot.
  - Weakest lead under hiwin: cci_mom (only hiwin33 clears both eras, <4 bps recent) — consistent with
    its weak S3 e-ratio. The medium control behaves exactly as always (42–46% win, the old picture).
- **AGAINST THE §6.3 PROGRAM TARGET:** ensemble_3of4/hiwin33 meets the backtest legs — win >= 70% ✓ (76/78),
  expectancy >= +4 bps ✓ (+7.7/+13.6), breadth >= 5 pairs ✓ (6 both-eras-positive), both eras ✓. Remaining
  leg: the **>= 100-trade live forward test** — which is BLOCKED, because risk Rule 3 (tp/sl >= 1.2,
  `risk/manager.py` FROZEN §24) rejects every hiwin bracket at the door (`rr_below_minimum`).
  **Deploying the validated geometry live requires the owner to amend Rule 3** — flagged as THE decision;
  deploying a g=1.2 approximation instead would not be the validated config (refused as dishonest).
- **HONEST CAVEATS (kept sharp):** (1) gross points are pre-fee by design — the net-$ cross-check above is
  what makes this more than relabeling; (2) 25 combos were tried — but entries were pre-validated leads and
  only 5 exit geometries were swept, and the both-eras requirement is the fluke-killer; a formal points-DSR
  is still owed (follow-up); (3) maker-fill realism at tight TPs is untested live — a taker stress test of
  the survivor cells is queued (S1b); (4) live-vs-backtest divergence is a known failure mode (iter 49) —
  hence the 100-trade live leg before any promotion; (5) ensemble activity is LOW (~0.9 trades/pair/week at
  1h) — the live test needs either patience or breadth.
- **APPLY:** cohort NOT rotated (the validated geometry cannot fire under live Rule 3 — a dark cohort
  writing rejection rows serves nothing). Shipped: --points mode + presets + miner + docs framework +
  this record. Next actions queued: S2 (mean-rev re-audit under hiwin geometry — its natural payoff
  shape), S1b (taker stress), points-DSR, Grafana points row.
- **CHECK STOP: neither condition met** (stop-#2 needs the live leg + formal DSR; stop-#1 needs live 70%
  over 100+ trades — the backtest now shows a path but live evidence is zero). The binding item moved from
  "no signal" to **"§24 Rule-3 owner decision + live forward test."**

### Iteration 54 — 2026-07-09 (CRITICAL DATA-INTEGRITY FIX: Daemon.stop() never persisted position closes to DB — 79/161 dev bots (49%) were permanently jammed with ghost open positions, some 278h+ old; fixed + cleaned up. No new signal search this firing.)

- **CONTEXT:** fleet 161 dev / 2 lab / 2 staging, 0 errors 24h, 0 stale heartbeats.
- **MEASURE (live, split_part(bot_id,'-',4)):** while pulling the usual per-algo split, checked open-position
  ages for the first time in the loop's history (previously MEASURE only ever read CLOSED trades) — found
  **79 open dev positions**, many far past their 6-8 candle intended max_hold: the oldest **278.5 hours**
  (11.6 days) open, dozens at 230-245h, the newest only 1.5-28.5h (the just-deployed exp_ensemble bots).
- **DIAGNOSE (root cause, §5 protocol — read source, trace the pipeline):** traced one stuck position
  (`dev-XRPUSDT-1h-exp_ensemble_ensemble_3of4-01`, open 28.5h) — its events showed a `position_closed_on_stop`
  entry from the iter-53 restart (00:44:08 UTC 07-08) with `trade_id=NULL`, yet the trades row still had
  `exit_ts=NULL` right now. Read `src/engine/daemon.py` `stop()`: it called `execution.close_position(pair,
  "manual")` directly and only wrote an EVENT — never called `db.close_trade()`. Compare to the correct
  internal helper `_close_position()` (used for every TP/SL/timeout close, and already correctly used by
  `force_close_all()`, the portfolio-guard path) which DOES call `db.close_trade()` — its own inline comment
  even warns "without this the row stays exit_ts=NULL forever ... blocking future signals via bucket_limit."
  `SimulationExecution.reconcile()` is documented as "nothing persists across restarts" (in-memory only, per
  its own docstring) — so on EVERY restart, any position still open at that moment gets marked closed at the
  execution layer (in-memory) and logged, but its DB row is orphaned forever; the NEXT process has no memory
  of it either, so nothing ever revisits it. This is a direct violation of CLAUDE.md §11 (position state
  authoritative in DB) and §16 (STOP must "write final state"). **Impact quantified:** risk Rule 1
  (`active_positions < max_active_buckets`, default 1) means a bot with a ghost position can NEVER open
  another trade — **79 of 161 dev bots (49%) were silently, permanently dead** for durations up to 11.6 days,
  invisible to every prior MEASURE step (which only ever read closed-trade aggregates, never open-position
  counts). This likely explains a chunk of the "sharp live swings" flagged as noise in iters 49-53 (e.g.
  cci_mom's −$2.59 single-iteration reversal, iter 52) — the live sample wasn't just noisy, the effective
  trading fleet size was silently shrinking between reads as more bots got jammed by each restart.
- **FIX (src/engine/daemon.py):** `stop()`'s shutdown-close loop now calls `self._close_position(pos["pair"],
  "manual", None)` — the same tested helper `force_close_all()` already used — instead of duplicating a
  broken subset of its logic. Added `tests/unit/engine/test_daemon_stop.py` (4 tests): the close persists
  trade_id/close_reason/pnl to DB, `_open_trade_ids` is popped, a no-open-positions stop writes nothing, and
  a reconciled position with no matching `_open_trade_ids` entry (an already-ghosted one) doesn't raise.
- **CLEANUP (one-time SQL recovery, `env='dev'` only, backup_db.py run first — 128 MB lean dump):** closed
  all 79 orphaned trades using the same economics `close_position()` applies to a non-take-profit exit
  (manual closes always market out: taker fee 0.04% + slippage 0.05%, regardless of maker_execution), marked
  at each bot's LATEST available candle close price (the most honest available "if closed now" mark),
  `close_reason='manual'`, `hold_candles=NULL` (genuinely unknown — not fabricated). Tagged each with a
  `position_closed:manual_ghost_recovered` event carrying its trade_id for auditability. **Result: 79 trades
  recovered, net −$3.16** (small — the bulk of the damage was the jammed buckets sitting idle, not directional
  loss on the stale marks). Verified 0 dev orphans remain post-cleanup.
- **ALSO FOUND, NOT TOUCHED (out of scope, flagged for owner):** the same latent bug affects `lab` (2 orphans)
  and `staging` (5 orphans) — both share the identical `Daemon.stop()` code path. Left alone this firing
  (lab is the owner's own sandbox via `lab.py`; staging is never-reset by standing policy) — worth a follow-up
  cleanup pass, either owner-directed or a future 10b-adjacent step.
- **SHIP:** ruff format+check clean, full test suite green (was 349 passed pre-iteration + 4 new = clean),
  committed+pushed main `168bdbb`, **CI green**, rebuilt image (src/ code change) via
  `docker compose up -d --build kestrel`. **No bots.json/exp_candidate.json change this iteration** (pure bug
  fix, no cohort rotation) → `bot_registry.py build` not needed. Verified post-rebuild: 161 dev heartbeats
  fresh, 0 stale, 0 errors, **0 open-position orphans** (the restart that shipped the fix ran on the
  OLD/buggy container code one last time, but nothing was open in that exact window, so it created no new
  ghosts — confirmed by re-checking `exit_ts IS NULL` count = 0 immediately after).
- **HONEST FRAME:** this is a data-integrity/observability fix, not a new signal — it does not create edge.
  Whole dev fleet net-of-ghosts is now **612 closed trades, 42.0% win, net −$10.59** (was −$7.42 before the
  79 recovered ghosts were counted) — same order-of-magnitude no-edge picture as always, now measured
  correctly instead of on an artificially-shrinking active fleet. `exp_ensemble` still has only 2 closed
  trades total (100% win, +$0.36 — meaningless n=2) — far too thin to read; unaffected by this fix since
  neither of its 2 open positions had been orphaned before now (one WAS a ghost, recovered in this cleanup).
- **10b STAGING:** selection unchanged (still APT/DOGE cci_mom) — no churn.
- **CHECK STOP:** **neither condition met.** No new signal was searched this firing — deliberate, matching
  the loop's own precedent for dedicated infra-fix iterations (9, 10, 15, 19, 20, 21, 48): a real,
  quantified, fleet-wide correctness bug outranks a marginal signal tweak. The fix restores the loop's own
  measurement integrity going forward. Loop continues.

### Iteration 53 — 2026-07-08 (EXPAND exp_ensemble 6->11 pairs — swept the 28 untested fleet pairs; blanket expansion REFUTED at pooled level, 5-pair cross-era-robust core found and deployed: CHZ/FET/GALA/TIA/WLD)

- **CONTEXT:** fleet 156 dev / 2 lab / 2 staging, 0 errors 24h, 0 stale heartbeats.
- **MEASURE (live, split_part(bot_id,'-',4)):** `macd_cross` remains the standout, now 76t/50.0%win/
  **+$1.67** (up from +$1.21 at iter 52). `cci_mom`(medium) continued its slide from iter 52: 164t/44.5%/
  **−$1.76** (was −$1.14/158t — a 3-iteration downtrend from +$1.45→−$1.14→−$1.76, though still just a
  live read, not cross-validated — the same discipline that flagged sma_cross's earlier swings as noise
  applies here). `sma_cross`(medium) 71t/36.6%/−$1.17 (down from −$0.67). `macd_rsi` 82t/43.9%/−$0.41
  (flat). `exp_robustwide/sma_cross`(wide) 34t/−$1.19, still below the n=50 retirement bar. `exp_ensemble`
  (new iter 52): **0 closed trades yet** — 1h cross-signals + medium exit take time, not a fault.
  Whole dev fleet 509 trades, 41.1% win, net **−$6.31** (worse than iter 52's −$5.49 — consistent with
  regression toward the established no-edge baseline as the earlier lucky streak unwinds).
- **DIAGNOSE:** cci_mom(medium)'s sustained live decline is the one real thing to note (3 consecutive
  reads all negative and worsening), but with no backtest cross-validation this stays an observation, not
  an action — the loop's own history is full of live swings (sma_cross −1.33→−0.49→−1.17) that later
  proved to be noise. The more actionable item: `exp_ensemble` is brand new with only 6 pairs and hasn't
  traded yet — the natural, low-risk, evidence-based next step (mirroring iter 45's cci_mom/sma_cross wide
  expansion) is a per-pair breadth check on the remaining 28 fleet pairs before it accumulates further.
- **HYPOTHESIZE + BACKTEST (1h, medium exit, maker, 28 untested pairs, `--by-pair`, walk-forward OOS +
  untouched prior-year lockbox):**
  - RECENT: pooled avg **+$0.0049/t**, win 43.3%, n=913, expR +0.09 — **18/28 pairs +EV**.
  - LOCKBOX: pooled avg **−$0.0061/t**, win 38.3%, n=853, expR −0.02 — only **8/26 pairs +EV** (2 pairs,
    HYPE/SEI, lack 2-yr history — the known iter-45 data-availability gap).
  - Pooled lockbox is NEGATIVE despite pooled recent being positive — the textbook recent-improves/
    lockbox-collapses data-mining signature (ema_cross, vwap_mom, 4h MACD, compress_vol_break, …).
  - **Per-pair overlap of BOTH-eras-positive pairs: CHZ (+0.0072/+0.0439), FET (+0.0189/+0.0135), GALA
    (+0.0012/+0.0367), TIA (+0.0518/+0.0132), WLD (+0.0257/+0.0304) — 5 pairs**, a genuine cross-era-
    robust core (matching the established ~5-pair pattern from cci_mom/sma_cross, iter 43). BTC recent
    +0.0011/lockbox −0.0151 — consistent with its known universal-loser status (sanity check passed).
- **DECIDE:** **blanket 28-pair expansion REFUTED** (pooled lockbox negative) — added to the refuted
  ledger. **Concentrated 5-pair expansion DEPLOYED** (evidence-based, mirrors the iter-43/45 playbook):
  additive, same `exp_ensemble` label/params → original 6 bot_ids untouched, 5 new added. Fleet
  156→161. Dedup-guard: 5 NEW / 156 SEEN.
- **APPLY:** rebuilt `bots.json` via `build_exp_cohort.py`, validated load, lint+tests green (no src/
  change this iteration — config-only), committed+pushed, CI green, `docker compose restart kestrel`
  (config-only, no rebuild), backfilled the 5 new bot_ids, verified 161 heartbeats fresh / 0 errors / 0
  stale, `bot_registry.py build` folded the snapshot in.
- **10b STAGING:** selection unchanged (still APT/DOGE cci_mom, n=10 each) — no churn, let it accumulate.
- **CHECK STOP:** **neither condition met.** ensemble_3of4 now has 11 evidence-backed pairs; still a
  forward-test LEAD not a confirmed edge. Loop continues.

### Iteration 52 — 2026-07-07 (DEPLOY: ensemble_3of4 cross-signal voting confluence — the 5th cross-era +EV signal and best-ever R/R/lockbox-breadth, but thin recent margin — NOT a confirmed edge; RETIRE structurally-dead exp_robustwide/cci_mom)

- **CONTEXT:** fleet 161 dev / 2 lab / 24 staging, 0 errors 24h, 0 stale heartbeats — clean since the
  cron cadence change to daily 00:00 UTC.
- **MEASURE (live, split_part(bot_id,'-',4)):** `macd_cross` still the standout, 71t/47.9%win/**+$1.21**.
  `cci_mom`(medium) FLIPPED hard negative: 158t/44.9%win/**−$1.14** (was +$1.45/134t at iter 51 — a
  −$2.59 swing over 24 new trades, the sharpest single-iteration reversal on record, itself evidence of
  noise not a real edge). `macd_rsi` 77t/44.2%/−$0.38 (down from +$0.03). `sma_cross`(medium) 68t/38.2%/
  −$0.67 (down slightly). **`exp_robustwide/cci_mom` (wide) crossed n=52** trades, 26.9% win, net
  **−$1.84**, PF **0.46** — meets the iter-48 structurally-dead bar (>=50 trades AND net<0 AND PF<1.0)
  exactly. `exp_robustwide/sma_cross` (wide) 33t, −$0.62, not yet at the bar. Whole dev fleet 489 trades,
  41.1% win, net −$5.49 (worse than iter 51's −$0.64 — consistent with regression to the established
  no-edge baseline once a lucky streak unwinds).
- **DIAGNOSE:** two things converge this firing: (1) a concrete, rule-based PRUNE action is now due
  (cci_mom/wide hit the exact retirement threshold set 4 iterations ago) — the MODE section's
  cell-viability rule says act on this, not wait; (2) the confluence-filter search (same-TF regime gates,
  cross-TF HTF trend) is now exhausted (iters 23, 39, 51 all refuted) — the one adjacent idea never tried
  is gating leads against EACH OTHER (an ensemble/voting mechanism) rather than against a regime or a
  different timeframe.
- **HYPOTHESIZE + BUILT:** `ensemble_Kof4` in algo_search.py (`_make_ensemble`) — calls the 4 deployed
  leads' own registered entry functions at each candle and requires >=K to agree on direction. Registered
  as its own algo so it flows through the identical run_backtest/risk/exit pipeline (no separate
  simulation logic).
- **BACKTEST (1h, medium exit, maker, 7 pairs incl. robust core + SOL/ADA, walk-forward OOS + untouched
  prior-year lockbox):**

  | variant | recent avg$ (breadth) | lockbox avg$ (breadth) | R/R (lockbox) |
  |---|---|---|---|
  | ensemble_2of4 | −0.0006 (4/7) | +0.0085 (5/7) | 1.55 |
  | **ensemble_3of4** | **+0.0013 (3/7)** | **+0.0105 (6/7)** | **1.68** |
  | cci_mom (control) | +0.0029 | +0.0054 | 1.49 |
  | sma_cross_9_21 (control) | +0.0086 | +0.0091 | 1.43 |

  ensemble_3of4 is +EV pooled in BOTH eras (best lockbox avg$/trade AND best R/R on record) but the
  recent margin is razor-thin and per-pair overlap of both-era-positive pairs is only DOGE/XRP (2).
  ensemble_2of4 FAILS the pooled recent bar (negative) despite 4/7 pairs individually positive — the
  drag comes from a few larger-magnitude losers (BTC, PEPE). 0/4 clear win>55% either variant.
- **APPLY (DEPLOY + PRUNE):** registered `ensemble_3of4` in `src/signal/patterns.py` (self-directing +
  permitted in all non-QUIET regimes, per the iter-25 lesson), added unit tests (full suite green).
  Deployed as `exp_ensemble` (6 bots, medium exit, ETH/DOGE/PEPE/XRP/SOL/ADA). **Retired
  `exp_robustwide/cci_mom`** (11 bots, structurally dead). Kept `exp_robustwide/sma_cross` unchanged
  (same label/pairs/params → same bot_ids, its history keeps accumulating uninterrupted — verified 33→38
  trades survived the rebuild). Dedup-guard: 20 NEW (14 retained sma_cross + 6 new ensemble) / 136 SEEN
  baseline. Rebuilt the image (src/ code change), backfilled the 6 new bot_ids, verified 156 heartbeats
  fresh / 0 errors / 0 stale post-restart, cleaned 11 orphaned cci_mom/wide heartbeats. `bot_registry.py
  build` folded the new snapshot in.
- **10b STAGING:** first time `promote_to_staging.py` found REAL qualifying dev cells (win>=50%, net>0,
  n>=10) instead of falling back to the lockbox-leads seed: APTUSDT/cci_mom (n=10, 70% win, +$0.77) and
  DOGEUSDT/cci_mom (n=10, 50% win, +$0.17). Selection changed → applied per the standing 10b policy
  (24-bot fallback seed → 2 bots). **Caveat flagged honestly:** n=10/cell is thinner than this loop's own
  established noise floor (n=30-50+ before a read is trustworthy) — this could easily reverse next
  firing. Worth reconsidering promote_to_staging's minimum-n floor in a future iteration, not changed here
  (out of scope for a routine 10b step).
- **CHECK STOP:** **neither condition met.** ensemble_3of4 is the best-ever R/R/lockbox-breadth signal
  but still fails win>55% and has a thin recent-era margin — a live FORWARD-TEST lead, not a confirmed
  edge. Loop continues.

### Iteration 51 — 2026-07-06 (fresh angle: HIGHER-TIMEFRAME (4h) trend confirmation on the 4 deployed 1h leads — recent-era lift is real but inconsistent per-lead and CRATERS in the lockbox for the best cell (sma_cross); REFUTED, HOLD)

- **CONTEXT:** fleet 161 dev / 2 lab / 24 staging, 0 errors last 24h (cron now daily at 00:00 UTC per the
  owner's cadence change). Housekeeping: found + deleted one orphaned `heartbeats` row
  (`labalpha-ETHUSDT-1h-cci_mom-01`, stale ~20h, status still "running") left over from the multi-sandbox
  `lab.py` verification in the prior session — the sandbox container itself was already torn down cleanly;
  only its heartbeat row was orphaned. `backup_db.py` run (lean dump, 106 MB, rotation held at 14).
- **MEASURE (live, split_part(bot_id,'-',4) — trades.pattern is a constant `momentum_continuation` label,
  NOT per-algo; confirmed the dashboard/analysis convention of reading the algo from bot_id is correct and
  necessary):** `cci_mom` 134t/48.5%win/**+$1.45** and `macd_cross` 69t/47.8%win/**+$1.45** are now the live
  co-leaders (both up from iter 50's +$1.22/+$1.14); `macd_rsi` 64t/43.8%/**+$0.03** (down slightly, noise);
  `sma_cross` 56t/37.5%/**−$0.49** (partial recovery from iter 49-50's −$1.33, still net-negative). Wide
  cohort (`exp_robustwide`): sma 26t/−$0.62, cci 42t/−$0.87 — **cci is closing in on the n=50 retirement
  bar** (iter 48's rule: wide retires if it stays net-negative past ~n=50/cohort) but not there yet. Whole
  dev fleet 421 trades, 42.5% win, net −$0.64 — flat, consistent with every prior iteration's "noise, not
  edge" read.
- **DIAGNOSE:** the live picture keeps oscillating around breakeven per-lead with no persistent winner
  (sma_cross's swing from −$1.33→−$0.49 in 9 trades is itself evidence of noise, not a real recovery).
  Chose a genuinely NEW backtest angle: every confluence filter tried so far (ADX floor iter-23,
  volatility floor iter-39, UTC session iter-50) gated on the **SAME timeframe** as the entry. A
  structurally different, never-tried idea — confirm the 1h entry against the trend on a **genuinely
  higher, separately-fetched timeframe** (4h) — was still open.
- **BUILT (visible artifact):** `--htf-confirm {4h,1d}` in `algo_search.py` — fetches the higher timeframe
  independently (respecting `--offset-days` for lockbox parity), computes an EMA9/21 trend direction per
  HTF bar (no lookahead: only the most-recently-CLOSED htf bar strictly before entry_ts is used), and
  reports both the unfiltered and htf-agreement-filtered leaderboards side by side.
- **BACKTEST (1h, medium exit, maker, robust core ETH/DOGE/PEPE/XRP/BTC, walk-forward OOS + untouched
  prior-year lockbox, 4h EMA9/21 confirmation):**

  | lead | recent avg$ (all→htf) | lockbox avg$ (all→htf) | kept% |
  |---|---|---|---|
  | cci_mom | +0.0052 → +0.0064 | +0.0078 → +0.0076 (flat) | ~54/~54% |
  | macd_cross | −0.0040 → **−0.0051 (worse)** | +0.0048 → +0.0070 | ~51/~49% |
  | macd_rsi | −0.0034 → +0.0016 | +0.0076 → +0.0089 | ~65/~64% |
  | sma_cross_9_21 | +0.0123 → +0.0142 | +0.0112 → **+0.0028 (craters)** | ~44/~43% |

  0/4 clear win>55% either filtered or unfiltered (best win_htf 47.1%, recent sma_cross).
- **FINDING:** no lead improves consistently in BOTH eras. sma_cross — the project's best-ever backtest
  cell (iters 32-47) — looks better in the recent year under the filter but **collapses from +0.0112 to
  +0.0028 in the lockbox**, the same recent-improves/lockbox-collapses signature as every data-mined filter
  before it (ADX confluence, volatility floor, UTC session). macd_cross even gets WORSE in the recent year.
  Only macd_rsi shows a same-direction (small) lift in both eras, but from an already-marginal base and on
  a ~35% smaller sample — within noise, not a robust confluence. **REFUTES cross-timeframe trend
  confirmation as a lever**, closing the one confluence-filter family (same-TF vs cross-TF) not yet tested.
- **APPLY: HOLD.** No candidate clears the bar filtered or unfiltered; nothing to rotate to. `exp_robustwide`
  stays as-is (cci at 42/50 toward retirement, sma at 26/50) — kept accumulating, no churn off a
  now-24h read (standing pref #9). Fleet byte-identical (only `scripts/algo_search.py`, a research-harness
  file, changed) → **no reset, no redeploy** (consistent with iter 41/43/46's precedent for harness-only
  changes).
- **CHECK STOP:** **neither condition met.** The confluence-filter search (same-TF and cross-TF) is now
  exhausted; every filter family tried refutes the same way. Loop continues; cost-side (§4 owner) remains
  the only un-exhausted lever. Cron cadence changed to **daily at 00:00 UTC** this session (was every 8h) —
  next firing ~24h from this one.

### Iteration 50 — 2026-07-05 (fresh angle: UTC-session/time-of-day breakdown of the CURRENT leads (cci_mom/macd_cross) — 3-way triangulation (recent/lockbox/live) each picks a DIFFERENT best session — REFUTED, extends the iter-4 seasonality finding to the new leads; HOLD)

- **CONTEXT:** fleet 161/25/24/2, 0 errors 8h, 0 stale heartbeats — 8h cadence firing cleanly. Live nearly
  unchanged since iter 49 (only a few hours passed): cci_mom +$1.22/121t, macd_cross +$1.14/66t (up from
  +$0.89/65t), macd_rsi +$0.18/63t, sma_cross still negative −$1.33/47t; wide cohorts still below n=50
  (sma 23, cci 38). Whole fleet 388 trades, 41.8% win, net −$1.89 — flat, unchanged from iter 49.
- **DIAGNOSE:** given so little live drift, chose a genuinely NEW backtest angle rather than re-running
  iter-49's checks. The project's only time-of-day test (iter 3-4) was on the OLD 5m/4h `mom_adx`/
  `triple_mom` patterns and was refuted — but nobody has checked whether UTC session matters for the
  CURRENT 1h leads (`cci_mom`, `macd_cross`).
- **HYPOTHESIS + BACKTEST:** built a trade-capture wrapper (monkeypatches `run_backtest` to record every
  trade's `entry_ts`+pnl, independent of algo_search's IS/OOS pooling) and bucketed by UTC hour into
  Asian(00-08)/London(08-16)/US(16-21)/US_late(21-24), 1h medium exit, maker, 9-pair robust core, recent
  + lockbox (IS+OOS pooled, ~6-8k trades/era — an exploratory scan, not yet OOS-isolated):
  - RECENT: cci_mom best=London(+$0.0051) worst=US(−$0.0092); macd_cross best=Asian(+$0.0047)
    worst=London(−$0.0089).
  - LOCKBOX: cci_mom best=US(+$0.0065) worst=London(−$0.0018); macd_cross best=London(+$0.0025)
    worst=US(−$0.0029).
  - **LIVE** (the real forward-test, cross-checked independently): cci_mom best=**London**
    (+$0.0483/t, 56.3% win, n=48) worst=US_late (−$0.0825); macd_cross best=**London** (+$0.0571/t,
    62.5% win, n=24) worst=Asian (−$0.0915, 0% win, n=6).
- **FINDING:** three independent samples (recent, lockbox, live) each nominate a **different** best/worst
  session for the same two algos — recent liked London/Asian, lockbox liked US/London (opposite signs on
  London!), live liked London but for different reasons (small n, high variance). No session wins or
  loses consistently across all three views — the textbook signature of noise, identical to the iter-3/4
  seasonality refutation and every other filter tested in this project (ADX confluence, volatility floor).
  **REFUTES session/time-of-day as a lever for the current 1h leads** — extends the old finding to new
  signals. (Caveat: this scan pooled IS+OOS for sample size; a formal OOS-only version isn't needed since
  the cross-era/live disagreement is already decisive — a real effect wouldn't need OOS isolation to show
  SOME consistency, and this shows none.)
- **APPLY:** **HOLD.** No session filter to add; nothing new to deploy; fleet byte-identical → no reset.
  Wide cohorts still below their n=50 retirement bar (23, 38) — keep accumulating, no action off an 8h read.
- **CHECK STOP:** **neither condition met.** Loop continues; cost-side remains the only un-exhausted lever.

### Iteration 49 — 2026-07-05 (loop RESUMED after a manual pause; LIVE PSR check + medium-exit DSR both corroborate iter-47: no lead clears even the raw bar; backtest-best (sma_cross) now diverges from live-best (cci_mom/macd_cross) — the signature of noise, not edge; HOLD)

- **CONTEXT:** the loop had been paused (owner request) and is resumed this firing. fleet 161 dev / 25
  robustwide / 24 staging / 2 lab. **Incident check:** the iter-48 network fix held — 0 stale heartbeats,
  0 errors in the last 24h (the 66 errors in the 48h window are ALL pre-fix, 03:48–05:50 on 07-03).
- **MEASURE (live, grown since iter 48):** `cci_mom`(medium) is now the strongest live performer (121
  trades, 48.8% win, net **+$1.22**); `macd_cross` +$0.89 (65t); `macd_rsi` +$0.18 (63t, near-flat);
  **`sma_cross`(medium) flipped NEGATIVE** (47t, 31.9% win, net **−$1.33**) — a reversal from its
  standing as the strongest backtest cell. Both wide cohorts remain negative (sma −$0.61/22t,
  cci −$0.66/37t) — still below the n=50 retirement bar set in iter 48.
- **DIAGNOSE (per-pair, sma_cross/medium):** the loss concentrates in 10 never-per-pair-validated
  pairs (LTC/UNI/FET/INJ/FIL/AVAX/XLM/TIA/SOL/AAVE), each n=1–5, all 0% win — *suggestive* of the
  iter-43 finding that only 5/34 pairs were ever cross-era robust for sma_cross, but n is far too thin
  (1–5/pair) to treat as proof rather than noise. Checked BTC specifically (flagged a "universal loser"
  in iter 43): live BTC is actually net **+$0.38** across all 4 leads (11 trades) — contradicts the
  backtest label, but again n too small to mean anything. **Whole dev fleet: 385 closed trades, 41.8%
  win, net −$1.90 (~−0.5bps/trade avg) — statistically flat, consistent with no edge.**
- **NEW CHECKPOINT #1 — live Probabilistic Sharpe (never done before; always backtest-only until now):**
  computed per-trade Sharpe + PSR(>0) directly on the 296 pooled live trades across the 4 medium leads:
  `sma_cross` Sharpe **−0.198**, PSR=0.107 · `macd_rsi` +0.021, PSR=0.566 · `cci_mom` +0.074, PSR=0.796
  · `macd_cross` +0.106, PSR=0.810 · **pooled all-4: Sharpe +0.024, PSR=0.662**. NONE clear even the raw
  (non-deflated) 0.95 bar in real live data — corroborates iter-47's backtest verdict using the actual
  forward-test, not a backtest assumption.
- **NEW CHECKPOINT #2 — DSR at the ACTUAL DEPLOYED exit (iter 47 only tested `wide`; medium was never
  put through the rigorous test):** 1h, medium, maker, 9-pair robust core, 20-algo trial set:
  - RECENT: best = `sma_cross_9_21/medium`, Sharpe +0.126, PSR=1.000, **DSR 0.873@N20 → 0.732@N60 →
    0.549@N200 — FAILS at every N** (weaker than the wide-exit iter-47 result, which passed at N=20).
  - LOCKBOX: best = `sma_cross_9_21/medium`, Sharpe +0.065, PSR=**0.942** (below even the raw bar),
    **DSR collapses to 0.088@N20 → 0.021@N60 → 0.004@N200** — a near-total failure, markedly worse
    than the wide-exit lockbox (iter 47: 0.659@N20).
- **FINDING:** the medium exit (what's actually live) is LESS statistically robust than the wide exit
  (iter 46-47's test subject) — the live fleet is running the weaker-by-DSR configuration. AND the
  backtest's top cell (`sma_cross`) is the OPPOSITE of the current live top performer (`cci_mom`/
  `macd_cross`) — when backtest-best and live-best disagree this sharply, it is the signature of noise
  dominating a real signal, not evidence of one. Two independent lenses (live PSR, medium-exit DSR)
  now both corroborate iter-47: **stop-#2 remains unmet, decisively.**
- **APPLY: HOLD.** No candidate clears the bar in either exit config; nothing to rotate to. Wide
  cohorts stay below their n=50 retirement threshold — keep accumulating, don't churn off an 8h/one-off
  read (standing pref #9). Fleet byte-identical → no reset, no redeploy needed.
- **CHECK STOP:** **neither condition met.** The picture is now unusually well-triangulated (backtest
  OOS+lockbox at two exits, live PSR) and all say the same thing honestly: no edge yet. Loop continues;
  the wall stays cost-side (§4 owner).

### Iteration 48 — 2026-07-03 (INCIDENT RECOVERY: daemon was DOWN 87min on a host-reboot network race — fixed; live shows the wide cohort UNDERPERFORMING the medium baseline; HOLD)

- **MEASURE → found a LIVE INCIDENT:** the fleet was **DOWN**. Container `kestrel-kestrel-1` was `Up (unhealthy)`,
  newest heartbeat ~87 min stale (05:08), and the DB showed 2 CRITICAL `daemon_crash` + 1 `candle_processor_error`
  at 05:49-05:50 (empty payloads). Logs = an endless `postgres:5432 - no response` retry loop — yet `psql` worked.
- **DIAGNOSE (root cause):** both containers were **recreated at 06:09** today (host-reboot race); `kestrel-kestrel-1`
  came up **attached to NO network** (`getent hosts postgres` = DNS FAIL, net list empty) while postgres was healthy
  on `kestrel_net`. So the entrypoint's wait-for-DB never resolved → no boot → no heartbeats → unhealthy, with no
  auto-recovery (Docker doesn't restart an *unhealthy-but-up* container). The other 6 services reattached fine.
- **FIX (verified):** `docker compose up -d kestrel` → recreated on `kestrel_net` → DNS OK → `PostgreSQL ready` →
  161 heartbeats fresh (26s), container `healthy`. Full stack re-checked: all 7 containers running/healthy on
  `kestrel_net`. **Follow-up flagged (not done):** a host-reboot can leave a container up-but-detached with no
  auto-heal — an `autoheal`-style restart-on-unhealthy sidecar would close the gap (additive infra, owner call).
- **FINDING (research — live forward-test now has data):** the `exp_robustwide` **wide-exit** cohort is running
  **NEGATIVE**: sma_cross/wide −$0.25 (16 trades, 43.8% win), cci_mom/wide −$0.69 (30 trades, **26.7% win**) —
  while the **medium-exit** leads on the same signals are ~break-even-positive: +$0.37 (140 trades, 47.1% win). The
  wide-exit concentration bet (iters 44-45) is **underperforming the medium baseline live**, corroborating iter-47's
  deflated-Sharpe verdict (no real edge). n is still small (16/30) — variance, not yet a clean refutation.
- **APPLY:** **HOLD** the cohort. No new +EV candidate exists (iter 47 deflated-Sharpe: nothing clears the bar), so
  there is nothing to rotate TO; forcing a churn would be noise. Let the wide A/B accumulate — **if wide stays
  net-negative past ~n=50/cohort it gets retired** in favour of the medium baseline. Visible artifact this tick =
  the incident recovery (fleet down→healthy) + this recorded live result. Fleet byte-identical → no reset.
- **CHECK STOP:** **neither condition met.** Loop continues; the wall stays cost-side (§4 owner).

### Iteration 47 — 2026-06-30 (DEFLATED SHARPE: the first rigorous stop-#2 test — sma_cross/wide is the closest signal ever, but FAILS the multiple-testing bar at the project's true search breadth; HOLD)

- **CONTEXT:** fleet 161, 0 errors. exp cohort = `exp_robustwide` (25 bots).
- **MEASURE (live):** robustwide produced its **first 2 closed trades — both winners** (sma +$0.14, cci +$0.09);
  n=2 = noise, but the wide A/B is finally generating data. Medium leads drifted slightly negative
  (69 trades, 46.4% win, −$0.37). macd_rsi 60.9% / macd_cross 56.3% (small n). 0 errors.
- **DIAGNOSE:** stop-condition #2 literally requires **deflated Sharpe > 0** across ≥3 pairs OOS+lockbox — but
  for 47 iterations the loop used the §30 **win>55% proxy** and NEVER computed the deflated Sharpe. The strongest
  signal (sma_cross/wide, iters 42-46: cross-era +EV, fill-robust, PF~1.4) has never been put to its actual test.
- **BUILT (visible artifact):** `--deflated-sharpe` in algo_search.py — Probabilistic + **Deflated Sharpe Ratio**
  (Bailey & López de Prado 2014). It runs a BROAD algo set, uses the cross-sectional Sharpe variance + trial count
  N as the multiple-testing haircut, and asks: is the best Sharpe higher than the EXPECTED MAX of N random tries?
- **BACKTEST (1h, WIDE, maker, 20-algo trial set, robust pairs, RECENT):**
  - best by Sharpe = **sma_cross_9_21/wide**, per-trade Sharpe **+0.164** (T=667, skew +0.44, kurt 1.93).
  - **PSR(>0) = 1.000** — the Sharpe is genuinely, robustly positive IN-SAMPLE (before any data-mining haircut).
  - **DSR @ N=20 = 0.976 (PASS)** · **N=60 = 0.922 (FAIL)** · **N=200 = 0.813 (FAIL)** (bar = DSR>0.95).
- **BACKTEST (LOCKBOX, okx transiently down → only ETH+INJ survived the 2-yr-deep fetch, DOGE skipped):** the
  best Sharpe variant was **sma_cross_10_30/wide** (+0.187, T=81) — note a DIFFERENT sma param won the lockbox
  (mild data-mining tell), Var(trial Sharpe) higher (0.0056 vs 0.0022 → harsher haircut), **PSR(>0)=0.957** (weaker;
  small T). **DSR @ N=20 = 0.659 (FAIL)** · N=60 = 0.541 · N=200 = 0.426 — fails the bar at EVERY N.
- **FINDING (the honest capstone):** sma_cross/wide is the **closest any signal has come** — its recent Sharpe is
  real and clears the deflated bar IF you'd only searched ~20-30 configs. But (a) this project has run **hundreds**
  of backtests over 47 iterations (true N ≥60-200, where recent DSR drops to 0.92→0.81), and (b) the untouched
  **lockbox FAILS at every N** (DSR 0.66 even at N=20) with a different winning param. ⇒ **stop-#2's "deflated
  Sharpe > 0" is NOT met, cross-era** — even the best cell is statistically indistinguishable from the
  best-of-many-random-tries once you honestly count the search. The rigorous, long-overdue confirmation of what the
  loop kept saying loosely: a forward-test LEAD, not a confirmed edge.
- **APPLY:** **HOLD** — no candidate clears the rigorous bar, so there is nothing new to deploy; forcing a cohort
  churn would be noise. Visible artifact = the reusable `--deflated-sharpe` adjudicator + this verdict. Fleet
  byte-identical → no reset.
- **CHECK STOP:** **neither condition met** (stop-#1 owner target: no; stop-#2 deflated Sharpe: DSR<0.95 at the
  project's true N). The loop's own formal edge test now has a proper implementation and a clean negative result.
  Loop continues — the only un-exhausted lever stays cost-side (sub-1.3bps venue / funding perps, §4 owner).

### Iteration 46 — 2026-06-29 (FILL-MODEL STRESS TEST: sma_cross/wide survives TAKER (fill-robust); cci_mom is maker-dependent; HOLD)

- **CONTEXT:** fleet 161, 0 errors. exp cohort = `exp_robustwide` (25 bots, expanded iter 45).
- **MEASURE (live):** robustwide still **0 closed trades** ~1 day post-deploy — the 1h cross signals + wide
  exit (hold up to 8h) make it SLOW to close; the live A/B vs medium needs more time (not a fault, just the
  nature of 1h crosses). Medium leads (sma+cci) **break-even live** (54 trades, 50% win, +$0.0003/t). 0 errors.
- **DIAGNOSE → HYPOTHESIZE:** the cohort can't be re-backtested (needs live time), so stress its key
  ASSUMPTION: every validation used MAKER fills, but CLAUDE.md flags post-only limits may not fill on a
  breakout. Does the robust cohort survive TAKER (worst-case fill, ~0.18%/trade ≈ 4.5× maker)?
- **BACKTEST (1h, WIDE, TAKER, recent + lockbox, --by-pair, robust core 9 pairs):**
  - **`sma_cross/wide` SURVIVES taker cross-era** — recent **+$0.0027/t (6/9 pairs)**, lockbox **+$0.0070/t
    (7/9 pairs)**. Its wide exit (tp3.0/sl1.5) captures moves big enough to clear even the taker cost →
    the most FILL-ROBUST cell the project has found; does NOT depend on maker fills.
  - **`cci_mom/wide` DIES at taker** — recent +EV only 1/9 (ETH barely), lockbox 6/9 but tiny. It fires
    ~2× more often with a thinner per-trade edge, so the higher taker cost wipes it → **MAKER-DEPENDENT**.
- **FINDING:** sma_cross is the high-conviction, fill-robust signal (real-money-safer — survives if maker
  fills miss); cci_mom's edge is contingent on getting maker fills (fragile to the fill model). Both stay
  +EV at MAKER (the live sim's model), so both remain valid forward-test arms — but the conviction ordering
  is now sma_cross > cci_mom on robustness grounds.
- **APPLY:** **HOLD** — no fleet change. The cohort expanded last iteration and needs live accumulation; this
  finding is analytical (refines conviction), it does NOT validate a new config to deploy. Visible artifact:
  this record + CURRENT BEST/COHORT updated + a `system` event. Fleet byte-identical → no reset.
- **CHECK STOP:** **not met** (0/4 clear the formal bar; win <55%). But sma_cross/wide is now the most robust
  signal on record (+EV at BOTH fee models, BOTH eras, 6-7/9 breadth) — the leading real-money candidate IF
  an edge ever clears the bar. Loop continues.

### Iteration 45 — 2026-06-29 (EXPAND: robustwide cohort 11→25 — 14 new cross-era-robust pairs from the untested fleet 24)

- **CONTEXT:** fleet 147, 0 errors. exp cohort = `exp_robustwide` (deployed iter 44, 11 bots).
- **MEASURE (live):** the iter-44 robustwide cohort has **0 closed trades yet** (1h + wide exit = hold up
  to 8h, slow) — nothing to measure there. Baseline leads unchanged/noisy (cci_mom +$0.31/57.9%, macd_rsi
  +$0.52/61%, sma_cross still live-worst −$0.22/36%). retired flowgate's 30 trades retained as history. 0 errors.
- **DIAGNOSE → HYPOTHESIZE:** robustwide needs live accumulation (can't backtest L2-free), so the
  productive new angle is COVERAGE: only **10 of the fleet's 34 pairs** were ever per-pair cross-era
  validated. The other **24 are deployed (medium) but never validated** — some may be robust enough to
  EXPAND the wide cohort (more activity, owner-mandate aligned, evidence-based).
- **BACKTEST (1h maker, WIDE, recent + lockbox, --by-pair, the 24 untested fleet pairs):** new pairs +EV
  in BOTH eras at wide — **sma_cross: ATOM/DOT/ETC/FIL/INJ/LINK/UNI/XLM (8)**; **cci_mom: ATOM/DOT/ETC/FET/
  FIL/INJ (6)**. **ATOM/DOT/ETC/FIL/INJ are robust for BOTH leads in BOTH eras** (the strongest). LTC/NEAR/
  OP/SEI lack 2-yr lockbox history → cannot be cross-era validated → excluded (honest: not "rejected",
  just unverifiable).
- **APPLY (EXPAND, additive):** grew `exp_robustwide` **11 → 25 bots** = sma_cross/wide on 14 pairs +
  cci_mom/wide on 11 pairs. The original 11 unchanged (same bot_ids); **14 NEW** bot_ids (dedup-guard:
  NEW 14 / SEEN 147 against the iter-44 committed registry). Backfilled the 14 (720×1h candles), restarted,
  verified live (25 heartbeats fresh, 0 errors, no orphans — pure addition). Fleet 147→161. **Reset nothing.**
- **CHECK STOP:** **not met** (0/4 clear the formal bar; win <55%). The wide cohort is now a 25-bot,
  cross-era-validated forward-test across 19 distinct pairs — a faster, broader live read on whether
  wide+concentration beats the medium baseline. Still a LEAD, not a confirmed edge. Loop continues.

### Iteration 44 — 2026-06-29 (DEPLOY: robust-core WIDE-exit cohort — first evidence-backed cohort in many iters; retires refuted flowgate)

- **CONTEXT:** fleet 148, 0 errors, one `kestrel` project. exp cohort = flowgate (5m order-flow gate).
- **MEASURE (live):** flowgate now **30 trades, 34.5% win, −$1.61** — CONCLUSIVELY refuted live (the gate
  does NOT rescue 5m, matching the iter-33/34 sub-cost backtest). 1h leads net-slightly-+ live but noisy
  (cci_mom +$0.47/60%, macd_rsi +$0.52/61%, macd_cross +$0.13/57%, **sma_cross −$0.22/36% — live-worst,
  DIVERGES from backtest-best**; small n). 0 errors.
- **DIAGNOSE → HYPOTHESIZE:** flowgate's forward-test is DONE (refuted) → rotate it out. Build on iter
  42-43: the 1h edge is real-but-fee-bound and concentrates on sma_cross/cci_mom × {ETH/DOGE/PEPE/XRP +
  BNB/AVAX}. Two untested levers on those KNOWN-robust cells: pair-concentration + a WIDER exit (iter-42's
  fee-efficiency idea — pay the fixed fee fewer times per unit profit).
- **BACKTEST (1h maker, recent + lockbox, --by-pair, robust 6-pair set, medium vs wide):**
  - **Concentration ~DOUBLES per-trade net** vs the broad fleet (sma_cross +$0.0065 broad → +$0.013 on
    the robust set).
  - **WIDE exit (tp3.0/sl1.5/hold8) beats medium for sma_cross** — aggregate BEST in BOTH eras: recent
    **+$0.0132/t win 49.8%**, lockbox **+$0.0147/t win 50.8%**, breadth **6/6 recent · 5/6 lockbox**
    (highest win + breadth the project has seen). cci_mom/wide +EV 5/6 both eras (BNB its lone loser → dropped).
  - Still **0/4 clear the formal §30 bar** (win <55%) → forward-test LEAD, not a confirmed edge.
- **APPLY (DEPLOY — first evidence-backed cohort rotation in many iters):** retired `exp_flowgate` (12
  bots), deployed **`exp_robustwide`** = sma_cross/wide × {ETH,DOGE,PEPE,XRP,BNB,AVAX} (6) + cci_mom/wide ×
  {ETH,DOGE,PEPE,XRP,AVAX} (5) = **11 bots**. A clean live A/B vs the deployed MEDIUM-exit leads on the
  SAME robust cells (distinct exp_ bot_ids, params differ → bot_registry NEW; dedup-guard verified all 11
  NEW). Fleet 148→147. **Additive new bot_ids → reset NOTHING** (baseline 136 + history preserved; flowgate's
  30 trades kept, only its heartbeat orphans cleaned post-restart per the orphan lesson). Backfilled the 11
  new bot_ids (720×1h candles each), restarted, verified live (11 heartbeats fresh, 0 errors).
- **CHECK STOP:** **not met** (0/4 clear the formal bar; win <55%). But this is the first cohort grounded in
  a cross-era-VALIDATED signal (not a blind forward-test) — watch live whether wide+concentration beats the
  medium baseline. Loop continues.

### Iteration 43 — 2026-06-28 (PER-PAIR BREADTH: sma_cross/cci_mom have 5 cross-era-robust pairs each; BTC a universal loser; macd_cross weakest; HOLD)

- **CONTEXT:** fleet 148, 0 errors; now ONE `kestrel` compose project (staging+lab folded in last task).
  Built a reusable `--by-pair` mode in `algo_search.py` (per-pair OOS avg$/trade table) — the breadth
  check stop-#2 requires ("≥3 pairs +EV") that the aggregate leaderboard never exposed.
- **MEASURE (live):** cci_mom +$0.51/61.5% (n26) & macd_rsi +$0.48/66.7% (n15) the live winners;
  sma_cross −$0.11 (n9, DIVERGES from backtest — small-sample noise); 5m flowgate still bleeding
  (−$1.34/24t, 33% win). 1h leads ≈ +$0.91 / 56 trades. 0 errors.
- **DIAGNOSE → HYPOTHESIZE:** iter 42 proved the 1h edge is real-but-fee-bound IN AGGREGATE. The rigorous
  follow-up: WHERE does it live per pair, and is it ≥3-pair cross-era robust (the data-mining-resistant
  breadth stop-#2 wants)?
- **BACKTEST (1h, maker, medium, recent + lockbox OOS, --by-pair, 10 pairs / 9 in lockbox):** pairs
  **+EV under maker in BOTH eras**:

  | lead | robust pairs (both eras) | recent breadth | lockbox breadth |
  |---|---|---|---|
  | **sma_cross** | ETH·DOGE·PEPE·XRP·BNB (**5**) | 7/10 | 7/9 |
  | **cci_mom** | ETH·DOGE·PEPE·XRP·AVAX (**5**) | 7/10 | 6/9 |
  | macd_rsi | SOL·DOGE·ADA (3) | 3/10 | 7/9 |
  | **macd_cross** | DOGE only (**1**) | 1/10 | 7/9 |

- **FINDINGS:** (1) **sma_cross & cci_mom have genuine cross-era per-pair breadth (5 robust pairs each)** —
  the edge is real and concentrated, not a fluke; shared robust core **ETH/DOGE/PEPE/XRP**. (2) **BTC is
  −EV for ALL 4 leads in BOTH eras** (a universal momentum-loser — too efficient/liquid; including it
  dilutes the fleet). (3) **macd_cross is the WEAKEST lead** — only DOGE survives both eras (recent
  breadth 1/10; its lockbox 7/9 collapses in the recent year = the half that's data-mined). macd_rsi
  similar but 3 both-era pairs. (4) Still **0/4 clear the FORMAL aggregate bar** (PF/deflated-Sharpe/win
  gate) → real breadth, NOT yet a confirmed stop-#2 edge.
- **DECIDE / APPLY:** **no fleet churn.** The robust cells (sma_cross/cci_mom on ETH/DOGE/PEPE/XRP…) are
  ALREADY deployed in the 34-pair fleet; a "concentrated" cohort would DUPLICATE them (dedup-guard) and
  double-count the same signals — not clean. BTC is a confirmed cross-era loser but live-pruning needs
  the § MODE structural-dead bar (≥50 live trades, PF<1.0) — not met, and ✗ shrink-for-its-own-sake
  (owner). So the finding informs FUTURE pair selection, ✗ a deploy now. Visible artifact: the reusable
  `--by-pair` tool (committed) + this breadth record + CURRENT BEST updated + a `system` event.
- **CHECK STOP:** **not met** (0/4 clear the formal bar; breadth real but aggregate PF/Sharpe below bar).
  Most useful structural takeaway: the edge is alt-concentrated (ETH/DOGE/PEPE/XRP), BTC dilutes, and
  macd_cross is the lead to retire first if/when the fleet is ever trimmed. Loop continues.

### Iteration 42 — 2026-06-28 (GROSS-EDGE DECOMPOSITION: the 1h leads ARE gross-positive cross-era — the wall is purely fees; §4 cost-lever quantified; HOLD)

- **CONTEXT:** fleet 148, 0 errors; lab (2) + staging (24) healthy. Built a new `--fees none` (zero-cost)
  mode in `algo_search.py` to isolate the PURE GROSS directional edge of the leads.
- **MEASURE (live):** 1h leads accumulating net-slightly-positive — cci_mom **19 trades, 68.4% win,
  +$0.64** (live standout), macd_rsi 12/58.3%/+$0.31, sma_cross 7/+$0.17, macd_cross 4/−$0.12 (≈+$1.0
  over 42 trades) — vs the 5m flowgate still bleeding (−$1.41/17 trades, 24% win). Small samples (back-
  test says marginal), but the 1h leads are NOT dead live.
- **DIAGNOSE → HYPOTHESIZE:** the single most decision-critical untested question — are the leads
  **gross-positive** (real directional edge merely EATEN by the ~4bps fee → a sub-fee venue rescues the
  project, the §4 lever) or **gross-negative** (no edge → no venue helps)? CLAUDE.md asserts gross-neg
  but only for 1m–5m; never measured at 1h for the leads.
- **BACKTEST (1h, medium exit, walk-forward OOS + untouched lockbox, 10 pairs; GROSS vs MAKER side-by-
  side, n nearly identical so the delta is a clean fee measurement):**

  | lead | GROSS recent | GROSS lockbox | MAKER recent | MAKER lockbox |
  |---|---|---|---|---|
  | **sma_cross** | +0.0145 | +0.0137 | **+0.0065** | **+0.0087** |
  | **cci_mom** | +0.0114 | +0.0067 | **+0.0047** | **+0.0016** |
  | macd_rsi | +0.0026 | +0.0188 | −0.0034 | +0.0124 |
  | macd_cross | +0.0020 | +0.0158 | −0.0045 | +0.0093 |

  (avg $/trade; PF 1.35–1.56 across the board, IS→OOS positive for sma_cross/cci_mom both eras.)
- **DECIDE / FINDINGS:**
  1. **ALL 4 leads are gross-positive in BOTH eras** → a small but REAL directional edge exists at 1h
     (refines CLAUDE.md §6: gross-negative is a 5m fact, NOT a 1h fact — the wall at 1h is purely fees).
  2. Under realistic **maker (4bps)**, **sma_cross + cci_mom stay net-positive cross-era** (the two
     robust leads); **macd_cross/macd_rsi go net-negative recent** (gross edge too thin to clear the fee
     — they only survive in the lockbox). The maker fee costs **~0.005–0.008/trade**; sma_cross's gross
     edge is ~0.014, so the fee eats ~40–55% of it.
  3. **§4 cost-lever QUANTIFIED:** a zero-fee/rebate venue would lift sma_cross from +0.0065/+0.0087
     (maker) toward +0.0145/+0.0137 (gross) — roughly **DOUBLE** the net edge. This is exactly what the
     owner's cost-side decision buys, now with a number.
  4. **Still 0/4 clear the formal bar** (win <50%, ~0.5–0.9¢/trade) → real but MARGINAL, NOT a confirmed
     stop-#2 edge. Honest: the directional edge is so thin that even halving the fee leaves it below bar.
- **APPLY:** **no fleet change** (sma_cross/cci_mom already deployed; nothing clears the bar to add;
  ✗ shrink the macd pair — owner mandate + they're forward-tests). Visible artifact: the new `--fees
  none` diagnostic (committed) + this decomposition recorded + CURRENT BEST updated + a `system` event.
  Fleet byte-identical → no reset.
- **CHECK STOP:** **not met** (0/4 clear the bar; marginal sub-bar signal). But the most CONSTRUCTIVE
  result in a while: the 1h directional edge is real, the wall is provably fees alone, and the §4 venue
  lever now has a concrete payoff number (≈2× the best lead's net edge). Loop continues.

### Iteration 41 — 2026-06-28 (genuinely-new family: VWAP volume-weighted anchor — vwap_mom data-mined, vwap_revert fade-death, REFUTED; HOLD)

- **CONTEXT:** fleet steady at 148, 0 errors; lab (2) + staging (24) healthy.
- **MEASURE (live, ~1 day of accumulation):** the **flowgate forward-test now has 14 trades** —
  `exp_flowgate_mom_adx` 13 trades 30.8% win **−$1.17** + `exp_flowgate_momentum_continuation` 1
  trade −$0.08 ⇒ the order-flow alignment gate **does NOT turn 5m positive** (still ~28% win,
  net-negative, sub-cost) — LIVE confirmation of the iter-33/34 microstructure finding (gate = a
  risk-quality filter, not an edge; 5m stays below the fee floor). 14 fired / 2 fee_not_viable /
  2 bucket_limit (gate passes signals fine). The 1h leads are mixed tiny samples (cci_mom +$0.52
  /62.5% on n=8, macd_cross −$0.12, others ~flat) = net roughly flat, noise.
- **DIAGNOSE → HYPOTHESIZE:** every entry tested across 40 iters is **price-only** (MA/MACD/RSI/CCI/
  stoch/BB/ADX/supertrend/donchian). The one major institutional reference level never tested is
  **VWAP** — a *volume-weighted* price anchor (where liquidity actually transacted), structurally
  different from all of them. Built two new registered algos in `algo_search.py`: `vwap_mom` (close
  reclaims a RISING rolling-VWAP / loses a FALLING one = trend with the volume anchor) and
  `vwap_revert` (price stretched >1 ATR from VWAP fades back = mean-reversion).
- **BACKTEST (1h, maker, medium+wide exits, walk-forward OOS + untouched prior-year lockbox, 10 pairs):**
  - **`vwap_mom`/medium:** RECENT **+$0.0073/t, win 43.5%, n=898, PF 1.45, expR +0.11** (the best
    recent-year showing in a while, ~4× the cross-leads' activity) → **LOCKBOX −$0.0046/t, win 38.4%,
    expR +0.01** = **collapsed**, textbook data-mining (same recent-+/prior-− signature as ema_cross,
    macd-4h, compress_vol_break).
  - **`vwap_revert`/medium:** NEGATIVE in BOTH eras (−$0.0036 recent / −$0.0036 lockbox, win 40–41%,
    n~4.3k) = the mean-reversion-FADE death, again (consistent with RSI-2/stoch/cci_revert/wick/bb_fade).
  - **0/4 clear the bar in BOTH eras.**
- **DECIDE:** **REFUTED.** The volume-weighted anchor doesn't beat the ~4bps cost floor: the momentum
  variant is a single-era artifact, the fade variant dies like every fade. Added to the refuted ledger.
  **Baseline untouched.** The 2 VWAP algos stay in `algo_search.py` as tested-but-undeployed (a new
  family available to the lab catalog / future search).
- **APPLY:** no live deploy, no reset (the change is research-harness only — `algo_search.py` is not a
  deployed file; fleet byte-identical; flowgate KEEP-guard honored). Visible artifact: the 2 new VWAP
  algos committed + this record + refuted-ledger entry + a `system` event row.
- **CHECK STOP:** **not met.** No edge (best lockbox −$0.0036/t, recent winner collapses cross-era, 0/4
  clear the bar); owner target (≥70% win / ≥15% daily over ≥100 trades) not hit. Levers left are §4
  owner-gated (sub-1.3bps/rebate venue · funding-rate perps) — flagged, not started.

### Iteration 40 — 2026-06-27 (do the 1h cross-leads hold at 15m? activity 20× but net-negative — TF-specific, REFUTED; HOLD)

- **CONTEXT:** fleet steady at 148 (4 leads × 34 pairs @1h + 12 flowgate @5m), 0 errors. No code/config
  change since iter 39; lab (2) + staging (24) healthy.
- **MEASURE:** since the iter-38 reset the dev slate has only **2 closed trades** (cci_mom + macd_rsi,
  −$0.16 each) — the 1h leads are **nearly dormant** (1h market mostly QUIET, the cross signals fire
  rarely). flowgate (5m, order-flow-gated) has 0 closed trades yet (the gate + freshness req is very
  selective). 0 errors. → almost nothing live to diagnose; this firing is a backtest.
- **DIAGNOSE → HYPOTHESIZE:** the current *real* problem is the deployed fleet barely trades — which
  fights the owner's "more ACTIVITY" mandate (§6/§13). The one genuinely-untested, mandate-serving
  angle: do the validated cross-leads (macd_cross/macd_rsi/cci_mom/sma_cross) hold their economics at
  **15m** (the canonical regime-filter TF, ~4× the bars of 1h, above the dead 5m)? Never run cross-era
  for the cross family. (30m was tried first — not in `_TF_MS`, not a canonical Kestrel TF — so 15m.)
- **BACKTEST (15m, maker, walk-forward OOS + untouched prior-year lockbox, 10 pairs; 15m lockbox data
  IS available — 35k candles/pair both eras, unlike the 5m data-infeasibility wall):**
  - **RECENT:** 0/4 clear the bar; best cci_mom/medium **−$0.0020/t**, win 38.4%, **n=5334**.
  - **LOCKBOX:** 0/4; best macd_rsi/medium **$0.0000/t** (exactly break-even), win 42.2%, **n=3494**.
  - Activity is hugely there — **~20× the 1h trade count** (n=3494–5334 vs ~200–230) — exactly what the
    owner wants, BUT the per-trade economics go **net-negative-to-flat in BOTH eras** (win 38–42%).
- **DECIDE:** **REFUTED as a deploy.** The 1h cross-edge is **TF-specific** (same as 4h MACD dying, and
  1h being the only survivor TF) — it does NOT extend down to 15m; the lower TF just bleeds faster
  (more bars × the ~4bps floor). Quantitatively settles "buy activity by dropping to 15m?": yes 20×
  more trades, but it loses money. Added to the refuted ledger. **Baseline untouched.**
- **APPLY:** fleet byte-identical → **no deploy, no reset** (additive-nothing; scoped-reset policy).
  flowgate KEEP-guard honored — order-flow forward-test keeps accumulating. Visible artifact: this
  record + the refuted-ledger entry + a `system` event row (no code change — used existing algos).
- **CHECK STOP:** **not met.** No edge (best −$0.002..$0.000/t, win <55%, 0/4 clear the bar, no
  lockbox-positive candidate); owner target (≥70% win / ≥15% daily over ≥100 trades) not hit. Levers
  left are §4 owner-gated (sub-1.3bps/rebate venue · funding-rate perps) — flagged, not started.

### Iteration 39 — 2026-06-27 (cost-clearing VOLATILITY-FLOOR filter on the 1h leads — REFUTED + leads regressed to flat; HOLD)

- **CONTEXT (this session, before the firing):** built the full owner self-service **lab env**
  (ENV=lab → SimulationExecution; `scripts/lab.py` catalog/add/deploy; Grafana bot catalog +
  Phase-dropdown `lab`; commit `13e032f`) and the Data-Analysis Grafana board over the restored
  archive. None of that touched the dev fleet. Fleet steady at **148** (4 leads × 34 pairs @1h:
  macd_cross/macd_rsi/cci_mom/sma_cross + 12 flowgate @5m), 0 errors, freshly reset → empty dev slate.
- **MEASURE:** dev 148 / lab 2 / staging 24 heartbeats, **0 errors/5m**; dev slate empty (post-reset).
  Nothing live to diagnose yet — so this firing did a BACKTEST re-validation instead.
- **DIAGNOSE:** directional entry search is exhausted (iters 1–38 refuted every momentum + mean-revert
  family, single-rule and confluence, cross-era). The one cost-aware angle iter-1's NEXT flagged but
  never tested: an **ATR%-of-price volatility floor** — only let setups fire when the move is big
  enough to clear the ~4bps maker floor. Tested as the high-volatility-regime proxy (`--regime
  volatile`, ATR14>ATR50×1.5) on the deployed leads — never run on the 1h leads before.
- **BACKTEST (1h, maker, 10 pairs, walk-forward OOS + untouched prior-year lockbox; leads
  macd_cross/cci_mom/sma_cross):**
  - **Baseline (no filter):** RECENT best sma_cross **+$0.0096/t**, win 47.2%, n=231 → **0/3** clear
    the bar. LOCKBOX best sma_cross **+$0.0100/t**, win 44.4%, n=214 → **0/3**. The leads have
    **regressed to flat** (~break-even, ≈0 expectancy) in BOTH eras — their original +EV was a
    data-mined single-regime artifact, exactly as the ledger predicted.
  - **Volatile-only (the cost-clearing filter):** RECENT best macd_cross **−$0.0609/t**, win 33.3%,
    **n=3**; LOCKBOX best cci_mom +$0.0468/t, win 50.0%, **n=6**. The filter does NOT lift expectancy
    and **starves the sample to n=3–6 trades/yr across 10 pairs** — statistically unusable (same
    thinness that killed `--regime volatile` at 5m in iter 1).
- **DECIDE:** **REFUTED.** The volatility-floor filter is dead (no lift + sample collapse) → added to
  the refuted ledger; closes iter-1's last open NEXT item. The flat leads are break-even, **not**
  structurally-dead (PF≈1.0, not <1.0) and **not** bleeding → per the cell-viability rule + owner's
  "✗ shrink to lose less" they STAY as the live forward-test. **Baseline untouched; no new cohort.**
- **APPLY:** fleet byte-identical to live → **no deploy, no reset** (scoped-reset policy: additive
  deploy resets nothing; this iteration is additive-nothing). flowgate cohort KEEP-guard honored —
  let the order-flow forward-test keep accumulating. Visible artifact: committed the iter-31 harness
  (`cci_mom`/`cci_revert`/`supertrend` algos in `algo_search.py`, never committed) that powers this
  backtest, + this record + a `system` event row.
- **CHECK STOP:** **not met.** No edge (best +$0.01/t ≈ break-even, win <55%, 0/3 clear the bar,
  no lockbox-positive candidate); owner target (≥70% win / ≥15% daily over ≥100 trades) not hit.
  Remaining levers are §4 owner-gated (sub-1.3bps/rebate venue · funding-rate perps) — flagged, not
  started. Loop continues in HYPER-SCALP MAINTENANCE / monitoring.

### Iteration 38 — 2026-06-27 (last untested momentum algos — data-mined / below-bar, REFUTED; fleet leaned; HOLD)

- **CONTEXT (this session, before the firing):** built `scripts/analyze_microstructure.py`, deployed the
  order-flow GATE + `exp_flowgate` cohort (see the deploy entry below), and — on the owner's call ("it keeps
  bleeding") — CUT all 238 ungated dead-5m bots (trend_momentum/mom_adx/triple_mom/impulse/wick/compression/
  anomaly), KEEPING leverage at 20×. Fleet **386 → 148** (136 1h leads + 12 flowgate). Bleed ~−$27/day → ~−$2–3/day.
- **MEASURE:** lean fleet, 0 errors; the 48h slate still shows the big 5m bleeders (now 0 live bots — pre-cut
  trades) plus the 1h leads (small −) and flowgate's first 2 (tiny) trades. The bleed is ending by construction.
- **DIAGNOSE:** the directional entry search is ~exhausted (refuted ledger); only 1h breakout-CROSS validates
  cross-era, order-flow is real-but-sub-cost (now the live gate). One thing left untested: 3 momentum-family
  algos in algo_search never run cross-era.
- **HYPOTHESIZE + BACKTEST (1h, maker, recent + untouched prior-yr lockbox, 6 pairs — `pullback_trend`,
  `body_go`, `compress_vol_break`):** RECENT `compress_vol_break/medium` looked +EV (expR +0.18, net +$4.36) →
  **LOCKBOX collapsed it** (−$1.18, avg −0.3 bps) = data-mined. `/wide` +EV both eras but MARGINAL (expR
  +0.10→+0.06, below bar, = stoch_ct category). `pullback_trend`/`body_go` breakeven/noise. **0/6 clear §30.** REFUTED.
- **DECIDE / APPLY:** no candidate validates → baseline untouched, added to the REFUTED LEDGER. Deployed
  **nothing new** — the best live candidate stays the freshly-deployed `exp_flowgate` cohort (KEEP guard; it can
  only be evaluated LIVE, must accumulate). Skipping the cohort rotation also honors the owner's just-made
  decision to keep the fleet lean — adding a marginal-losing arm would re-introduce bleed. No deploy → no reset
  (backup taken regardless). 
- **CHECK STOP:** not met (0/6 clear bar; best is marginal-below-bar; still no edge). The remaining real levers
  stay STRUCTURAL/§4 (sub-1.3 bps venue → makes the live order-flow gate tradeable · funding-rate · leverage).

### Deploy — 2026-06-27 (order-flow alignment GATE + exp_flowgate cohort · user-requested, NOT a cron iteration)

- **WHAT:** Built `scripts/analyze_microstructure.py` and mined the never-analysed 245k-row
  microstructure dataset. Top-5 depth imbalance (`depth_imb5`) is genuinely directionally predictive
  (BTC/ETH spearman ~0.11–0.15, ~+1.3 bps top-vs-bottom quintile at +12s) — the first real directional
  signal in project history — AND on the 329 live dev trades with order-flow coverage, entries WITH the
  book won 37.2% vs 20.5% against (~2× win, ~½ the per-trade loss). Shipped as an optional entry gate.
- **NOT AN EDGE (consistent with iter-37's order-flow refutation):** the ~1.3 bps signal sits UNDER the
  ~4 bps cost floor; the non-overlap sim is −EV on every pair/horizon and the gated live trades are still
  net-negative. This is a risk-QUALITY filter (cuts the 0%-win stop-outs), NOT an edge claim. The cost
  wall is unchanged — only a sub-1.3 bps / rebate venue (§4) makes order-flow tradeable.
- **DEPLOYED:** `flow_gate_enabled`/`flow_gate_min_imbalance` params (off by default → fleet unaffected);
  pure gate in `signal/detector.py` (rejects when signed depth_imb5 < threshold); `db.get_latest_order_flow`
  reader + daemon wiring (L3 supplies the float, 120 s freshness, fail-closed). Cohort `exp_flowgate` =
  momentum_continuation + mom_adx at 5m on the 6 RECORDED pairs (BTC/ETH/SOL/DOGE/ADA/XRP), gate on,
  exits matching the fleet so the gate is the only variable. **12 new bot_ids, baseline 374 untouched**
  (additive → backfilled, reset nothing). Verified: build green, CI success, 386 hb, 0 errors, read-path
  returns fresh imbalance for all 6 pairs. Commit 67b0f75.
- **⚠ KEEP exp_flowgate — DO NOT rotate it out next firing.** The gate can ONLY be evaluated LIVE: a
  backtest has no historical L2, so `order_flow=None` → the gate is a no-op in `algo_search`/`backtest_*`.
  Its whole value is the live win-rate of gated vs ungated, which needs to ACCUMULATE (~30+ gated trades;
  at 5m that is days). Treat `exp_flowgate` as the current exp cohort; `build_exp_cohort.py` would STRIP it,
  so only replace it if a NEW lockbox-validated backtest winner appears (rare) — and note in the log that
  doing so ENDS the gate forward-test. MEASURE it each firing: gated cohort win% vs the ungated 5m
  momentum_continuation baseline (re-run `scripts/analyze_microstructure.py` for the dataset-level read).

### Iteration 37 — 2026-06-27 (genuinely-new family: pairs/stat-arb ratio mean-reversion — data-mined, REFUTED; HOLD)

- **RATIONALE:** 3 families refuted (indicators/order-flow/lead-lag), all hitting the 4 bps fee. Tested
  the one classically-distinct family left untouched, with a DIFFERENT cost profile: pairs/cointegration
  mean-reversion (trade the RATIO of two correlated assets back to its mean). Unlike directional scalping,
  a reverting deviation can capture 50–100 bps — far above even the doubled 8 bps two-leg cost — so it was
  genuinely worth testing, not assuming.
- **BACKUP:** lean, kept 7. **Health:** 392 hb, 0 errors, host 7.4 GB avail, postgres 41% (creeping but
  fine). Fleet net **−$57.26 / 1727 closed** (still bleeding linearly = the t=−7.47 fee floor). No fault.
- **STAT-ARB TEST (1h, 8 cointegration-candidate ratios, z>±2σ entry / |z|<0.3 exit / 24h cap, 8 bps both
  legs, recent + lockbox):**
  - **RECENT: 4/8 net-positive** (ETH/BTC +3.5, AVAX/ETH +6.4, LINK/ETH +5.7, XRP/BTC +0.1 bps; 56–67% win).
  - **LOCKBOX: 0/8 net-positive** — ALL collapse, several catastrophically (XRP/BTC −50, DOGE/BTC −39,
    AVAX/ETH +6.4→−34, ETH/BTC +3.5→−0.6). Textbook data-mining: recent-regime artifact, run over in the
    prior year. REFUTED.
  - **Failure mode = mean-reversion-fade death (again):** win rates stay decent (53–59%) EVEN in the
    lockbox while net is deeply negative — small frequent wins, huge tail losses when a spread TRENDS
    instead of reverting. Same death as RSI-2 / stoch / cci_revert / wick / bb_fade, now on cross-asset
    ratios. (Also: XRP/BTC 59% win + −50 bps/trade = more proof the win>55% bar is broken.)
- **DECISION — HOLD (no deploy, no reset).** FOUR distinct families now refuted. The pattern is total and
  consistent: momentum-breakout = sub-noise; every mean-reversion-FADE (incl. stat-arb) = data-mined,
  dies in the lockbox; order-flow = real but sub-cost. Fleet unchanged (374+18).
- **STOP CHECK:** NOT met. Nothing in candles, order-flow, lead-lag, or stat-arb yields a lockbox-surviving
  +EV edge at 4–8 bps fees. The search space within agent authority is now comprehensively mapped to
  no-edge; the only untested levers are §4 (cost-side venue / funding-rate / leverage) — owner decisions.

### Iteration 36 — 2026-06-26 (genuinely-new family tested: cross-asset BTC→alt lead-lag — REFUTED both eras; HOLD)

- **RATIONALE:** candles + order-flow + 5m fleet all exhausted/confirmed-negative (iter 32-35). Rather
  than re-confirm, tested a signal FAMILY never tried and not in the ledger: cross-asset lead-lag (BTC's
  just-closed 1h bar → alt's NEXT bar). Different kind of signal (cross-sectional, not single-asset
  indicator); crucially alt moves are big enough that a real lead-lag COULD clear the 4 bps fee — unlike
  the ~1 bps order-flow signal. Worth a rigorous shot.
- **BACKUP:** lean, kept 6. **Health:** 392 hb, 0 errors, mem fine. Fleet net **−$45.39** (was −$34.20
  iter-35 → still bleeding, exactly as the t=−7.47 fee-floor predicts). No infra fault.
- **LEAD-LAG TEST (1h, BTC→7 alts, ~8.8k bars/era, recent + lockbox, cost-aware directional net):**
  - **Contemporaneous** BTC↔alt corr is HUGE (0.72–0.86) — alts co-move with BTC, well known, NOT
    tradeable (same-instant info).
  - **Lead** IC (the tradeable part) is ~0 recent (0.000–0.018) and **NEGATIVE in lockbox** (−0.016..
    −0.039) — no cross-era lag signal; the tiny recent IC flips sign in the prior year = noise. By the
    time a 1h bar closes the alt has ALREADY moved with BTC; nothing unreflected remains.
  - Strategy net after 4 bps cost: **0/7 pairs positive in BOTH eras** (−3 to −7 bps). REFUTED.
- **DECISION — HOLD (no deploy, no reset).** Third distinct signal FAMILY refuted (candles, order-flow,
  lead-lag). Lead-lag in crypto lives at sub-minute TFs (already arbitraged by 1h) — and there it hits
  the SAME sub-cost wall as order-flow (tiny moves vs 4 bps fee), and isn't lockbox-testable at 5m. So no
  tradeable corner. Fleet unchanged (374+18).
- **STOP CHECK:** NOT met. The pattern is now overwhelming: every genuinely-new family (indicator,
  order-flow, cross-asset) is either no-signal or real-but-sub-cost. The binding constraint is the 4 bps
  fee, not signal discovery — the unlock is a cost-side venue decision (§4 owner), flagged not actioned.

### Iteration 35 — 2026-06-26 (accumulated slate confirms fleet is statistically NEGATIVE, t=−7.47 — no-reset policy vindicated; HOLD, ✗ prune)

- **RATIONALE:** both in-scope levers now exhausted (candles sub-noise iter 32-33; order-flow real but
  sub-cost iter 33-34). Rather than churn a dead family, this firing did the read the no-reset policy was
  BUILT for: with the slate fully accumulated, has any live cell sharpened into real signal?
- **BACKUP:** lean, kept 5. **Health:** 392 hb, 0 errors, host 7.8 GB avail. No infra fault.
- **MEASURE — per-pattern live t-stat on the accumulated slate (1183 closed trades):** the answer is the
  OPPOSITE of edge — the fleet is now statistically, robustly NEGATIVE:
  - **FLEET TOTAL: net −$34.20, mean −$0.029/trade, t-stat −7.47.** Every cohort significantly negative:
    trend_momentum 5m −$14.59 (n=484, t=−4.8, PF 0.55), mom_adx 5m −$9.84 (n=358, t=−4.2, PF 0.56),
    triple_mom 5m −$6.53 (n=254, t=−3.1, PF 0.60), wick_rejection 5m −$1.43 (t=−3.3, PF 0.22),
    cci_mom 1h −$1.43 (n=13, t=−1.8). 1h macd leads ~flat tiny-sample (−$0.10/−$0.19, n=9).
  - **This is the milestone:** the "+$6.81 green" owner saw at iter-32 (5h window, t=+3.1) has, with full
    accumulation, resolved into a confirmed LOSS (t=−7.47). The no-reset policy did EXACTLY its job —
    let variance wash out, exposed the true negative expectancy that per-deploy resets had been hiding.
    The fee floor wins, now with statistical certainty, not just "no positive edge found."
- **PRUNE? NO (owner §6/§13 directive).** trend_momentum/mom_adx/triple_mom 5m all formally meet the
  cell-viability prune bar (≥50 trades, net<0, PF<1.0). But the owner is explicit: ✗ shrink the fleet or
  steer slow to "lose less" — the active fleet IS the design, losses are simulated/PAPER, the job is to
  find edge WITHIN it. Pruning creates no edge; it's the "slow to lose less" the owner rejected. So HOLD
  the fleet intact.
- **DECISION — HOLD (no deploy, no reset, no prune).** The loop has reached the honest limit of its
  in-scope authority: candles = no signal; order book = real signal killed by fee; 5m fleet = confirmed
  net-negative (fee floor). The remaining lever is COST-SIDE (sub-1.3bps / maker-rebate venue, §4 owner
  decision) — re-confirming no-edge every 8h is low marginal value. FLAG for owner: the autonomous search
  is exhausted; the next move is a venue/cost decision, not more iterations.
- **STOP CHECK:** NOT met (no positive edge; the confirmed result is a negative one). Honest state, sharp:
  there is no tradeable edge in candles or order flow at our 4 bps fee; the fleet's loss is now
  statistically certain (t=−7.47), which is the fee floor, exactly as predicted.

### Iteration 34 — 2026-06-26 (microstructure EXTREME-TAIL test — 0/60 cells beat cost; the wall is the fee, quantified; HOLD)

- **RATIONALE:** candles mined out (iter 32-33), microstructure MEAN edge sub-cost (iter 33). Rather than
  re-sweep a dead family (churn), deepened iter-33: does the EXTREME TAIL of the real imbalance signal
  (rare strong |imb5|, tight-spread pairs, cost-aware directional rule) ever beat the fee floor? That's
  where a tradeable corner would hide.
- **BACKUP:** lean dump, kept 4. **Health:** 392 hb, **0 errors**, 786 dev closes accumulating, mem fine
  (postgres 27%). 1h leads still tiny-sample noise (cci_mom 6 @17%, macd_cross 4 @50%, macd_rsi 3 @67%,
  sma_cross 0 — too slow to read; expected). No infra fault.
- **EXTREME-TAIL ANALYSIS (183k snapshots, 6 majors; |imb5| thresholds 0.3–0.9 × horizons 12s/60s/5min ×
  6 pairs = 60 cells; directional rule sign(imbalance), net = gross − round-trip cost):**
  - **0 / 60 cells NET-positive after cost.** The signal is genuinely directional (gross > 0 in essentially
    every cell — the book predicts), but the move is **0.6–1.5 bps** on the tradeable pairs (BTC/ETH),
    max ~3.5 bps (rare DOGE 5min), vs a **4 bps maker fee + spread**. Best cell DOGE thr0.7/5min gross
    +3.57 → NET −1.73; best BTC/ETH ~+1.3 gross → NET −2.7.
  - **The wall is QUANTIFIED:** the 4 bps maker fee ALONE exceeds the gross edge at every cell. To clear,
    round-trip fees would need to drop below ~**1.3 bps** (on BTC/ETH, where spread is ~0.02–0.06 bps).
    Even then it's a fragile ~1 bps edge that adverse-selection/latency would erode — HFT execution, not
    retail paper.
- **DECISION — HOLD (no deploy, no reset).** Both levers now rigorously exhausted: candles = no signal;
  order book = real signal, killed by fee. Nothing tradeable to deploy at our cost structure. Fleet
  unchanged (374 dev + 18 staging), 4 leads forward-testing. The ONLY genuinely-new direction is
  COST-SIDE (a sub-1.3bps / maker-rebate venue) — a §4 owner/venue decision, FLAGGED not actioned.
- **STOP CHECK:** NOT met. Honest state unchanged and now precise: there is a real order-flow signal worth
  ~1 bps; it does not survive a 4 bps fee. The edge problem is now a COST problem.

### Iteration 33 — 2026-06-25 (PIVOT: owner-directed MICROSTRUCTURE analysis — real signal, but sub-cost at every horizon; HOLD)

- **CONTEXT (owner directive this session):** after iter-32 I analyzed all 4 leads on the FULL backtest
  (year + lockbox, 10 pairs, thousands of trades). Verdict delivered to owner: the 1h candle leads are
  **real-signed but statistically too thin** — cci_mom lockbox t-stat ≈ **0.65** (need ~2); the edge is
  INSIDE the noise even at ~1,900 trades. More data won't rescue a sub-noise edge → **OHLCV indicators
  on candles are MINED OUT** (confirmed). Owner: "testing only is not enough." Pointed me at the one
  unanalyzed dataset — the microstructure recorder. So this firing = analysis, not another candle sweep.
- **BACKUP:** lean 34 MB, kept 3. Mem fine (host 7.4 GB avail, postgres 17%). No deploy → no reset.
- **MICROSTRUCTURE ANALYSIS (183k snapshots, 6 majors, ~4 days @ 12s, order book + tape):** tested
  whether depth-imbalance / aggressor-delta at t predicts the forward mid move.
  - **`depth_imb5` (5-level book imbalance) is a REAL predictive signal** — IC **0.136 BTC / 0.101 ETH**
    at 12s (hugely significant on 30k pts), decaying fast with horizon (mean IC 0.061@12s → 0.016@5min
    → 0.004@30min). depth_imb20 weaker (0.032); aggressor `trade_delta` ≈ 0 (RESTING imbalance predicts,
    executed flow does NOT — notable).
  - **But NOT TRADEABLE at our cost:** the directional quintile move (Q5−Q1 forward return) is only
    **0.3–1.5 bps** across all horizons 12s→60min, vs a round-trip cost ≈ **5 bps** (maker 4 bps + spread).
    Tradeable? **NO at every horizon.** The signal decays BEFORE the move grows past the fee floor — the
    classic HFT squeeze: signal strong where moves are tiny (seconds), moves big where signal is gone.
- **KEY DISTINCTION (the informative part):** candle indicators have **NO signal** (sub-noise, mined out);
  microstructure imbalance has a **REAL signal blocked by COST/latency**, not absence. The wall here is
  FEES, not predictability. Harvesting it needs maker REBATES (negative fees) / colocation / sub-ms
  latency — HFT-firm infrastructure, not retail paper. At 20× lev + 4 bps maker it's underwater.
- **DECISION — HOLD (no deploy, no reset).** No candle candidate beats the lockbox (family exhausted);
  the microstructure edge is real but sub-cost so there's nothing tradeable to deploy. Fleet unchanged
  (374 dev + 18 staging), 4 leads still forward-testing as slow confirmation. ✗ manufacture churn.
- **STOP CHECK:** NOT met (no edge clears the bar). Honest state: candles mined out; order book has real
  but uneconomic structure. Next genuinely-new levers are COST-side (maker-rebate venue / longer holds
  on the few pairs where imbalance Q-spread is largest) — flag for owner, don't churn.

### Iteration 32 — 2026-06-25 (ACTIVE SEARCH WIN: deployed sma_cross — 4th cross-era +EV 1h signal; FIRST additive no-reset deploy)

- **CONTEXT:** between firings the owner had me (a) narrow the reset to SCOPED (additive deploy → reset
  NOTHING; full nuke every 8h was destroying the forward-test) and (b) add automated rotated DB backups
  (`scripts/backup_db.py`, lean 29 MB / full 290 MB, disk-aware) — "store the database for future
  data-analytic selection." This iteration is the first deploy under the new policy.
- **BACKUP FIRST:** lean dump 30 MB, rotation kept 2. (Per new §RESET POLICY, every firing.)
- **MEASURE:** dev slate accumulated to **296→309 closed over ~8h** (the no-reset policy WORKING — data
  not wiped). The earlier green WASHED OUT: +$6.81/t=+3.10 at 5h → **+$0.87/t=+0.34/PF=1.06** at 7.7h —
  variance reversion, now visible ONLY because we stopped resetting. triple_mom 5m +$2.10/53%, mom_adx
  +$1.09/46%, trend_momentum −$1.31/37% (the drag), 1h leads n=1 each (too slow). 0 errors, mem healthy.
- **ACTIVE SEARCH — swept 14 UNDEPLOYED breakout/momentum algos at 1h (maker, 6 pairs, recent+lockbox):**
  - **`sma_cross_9_21` (9/21 SMA golden/death cross) is a WIN — the ONLY one +EV in BOTH eras:** recent
    expR +0.14 (net +$4.01, R/R 1.48, n=466), LOCKBOX **+0.12** (net +$3.92, R/R 1.32, n=535), OOS>IS
    both. Per-pair breadth: ETH/DOGE/XRP +EV in BOTH eras (≥3-pair gate cleared; BTC − both, like
    macd_rsi). The project's FOURTH cross-era +EV signal.
  - REFUTED same iter: the whole rest of the breakout family — `ema_cross` (recent TOP +0.16 →
    lockbox −0.03), `donch_break_10/20` (−0.00/−0.02), `breakout_vol` (−0.01), bb_break/mom_align/
    macd_hist/rsi_cross_50 all breakeven-negative lockbox = data-mined. The lockbox is the only thing
    separating sma_cross from its false kin.
- **DEPLOYED sma_cross** as a live pattern, all THREE firing requirements (iter-25 lesson): patterns.py
  `detect_sma_cross` + `_sma` helper (matches algo_search `_make_ma_cross` bit-for-bit) + SELF_DIRECTING
  + regime_permits in all non-QUIET regimes + config `sma_cross_fast`=9/`sma_cross_slow`=21 + params.json
  contracts + TestSmaCross + regime-permit test. 34-pair 1h cohort (medium exit). promote_to_staging
  `_LOCKBOX_LEADS += sma_cross`. Fleet **340→374** (34 NEW).
- **SHIP (first ADDITIVE no-reset deploy):** ruff+mypy clean · patterns/regime/scripts tests green ·
  dedup **34 NEW + 340 SEEN** · committed+pushed `04cff12` · CI green · rebuilt image · **NO RESET
  (additive)** — backfilled ONLY the 34 new sma_cross bot_ids (720×34, needed a re-run for a 10-pair
  gate rate-limit gap) · 374 heartbeats · **existing dev trades PRESERVED (309, even grew from 296)** ·
  0 errors · sma_cross VERIFIED armed (registered/self-direct/permitted=True). This is the policy proof:
  deployed a new strategy WITHOUT wiping the forward-test.
- **HONEST:** sma_cross is a forward-test LEAD like the other three — modest (win <50%, BTC −, 1h not
  5m), clears the deploy gate but NOT a confirmed stop-#2 edge. ✗ overclaim. Four cross-era leads now
  forward-testing; the 5m green washing out to breakeven is the honest current state.
- **STOP CHECK:** NOT met. Continue — four 1h leads accumulating; the scoped-reset + backups mean the
  evidence now survives between firings for real data-analytic selection.

### Iteration 31 — 2026-06-25 (ACTIVE SEARCH WIN: deployed cci_mom — the 3rd cross-era +EV 1h signal, high-activity)

- **MEASURE:** macd forward-test ~13 closed (macd_cross 9 @ 66.7% win regressing from 75% as
  predicted; macd_rsi 4 @ 50%), staging 4 @ −$0.67 — all tiny-sample noise. 5m fleet −$16 (variance).
  306+12 hb, 0 errors, postgres 22% (creeping but fine). Accumulating slowly (~3 macd/firing).
- **ACTIVE SEARCH — tested 3 genuinely-new indicator families (CCI, Supertrend):**
  - **`cci_mom` (CCI(20) breakout through ±100) is a WIN — +EV in BOTH eras:** recent expR +0.12,
    LOCKBOX **+0.07**, R/R 1.46, IS→OOS ~0. Per-pair lockbox breadth **4/6 positive** (ETH +0.12,
    SOL +0.08, DOGE +0.10, XRP +0.18; BTC marginal; ADA −) — clears the ≥3-pair deploy gate (same
    profile as the macd leads). **Crucially ~3× the activity of macd** (1182 vs 364 trades/yr) → serves
    the scalp mandate AND gives a FAST forward-test. The project's THIRD cross-era +EV signal.
  - REFUTED same iter: `cci_revert` (mean-rev, lockbox ≤0 — consistent with every mean-rev refutation),
    `supertrend` (lockbox ≤0).
- **DEPLOYED cci_mom** as a live pattern: patterns.py `detect_cci_mom` + `_cci_pair` helper +
  SELF_DIRECTING + **regime_permits_pattern in all non-QUIET regimes (iter-25 lesson — did NOT repeat
  the inert-cohort bug)** + config `cci_period`=20 param + params.json contract + unit tests. 34-pair
  1h cohort (medium exit tp2.0/sl1.0/hold6), broadened like macd (iter-28) for fast forward-test.
  Fleet **306→340** (34 NEW). Staging leads += cci_mom → staging **12→18** (6 each of macd_cross/
  macd_rsi/cci_mom on the 6 validated pairs).
- **SHIP:** ruff + mypy src/ clean · patterns/regime/promote/config tests green · dedup 34 NEW + 306
  SEEN · committed+pushed `6808648` · CI green · rebuilt image · FULL RESET (wiped dev, KEPT candles +
  microstructure) · backfilled 34 dev + 6 staging cci_mom bot_ids · recreated labs + staging on new
  image · 340+18 heartbeats, trades=0, 0 errors, postgres 21% (host fine). cci_mom VERIFIED armed
  (registered/self-direct/permitted=True; reaches the pipeline).
- **HONEST:** cci_mom is a forward-test LEAD like the macd ones — modest (win <50%, lockbox expR
  +0.07), at 1h NOT 5m, ADA negative, PF/deflated-Sharpe borderline. Clears the deploy gate but is NOT
  a confirmed stop-#2 edge. ✗ overclaim. But it IS the first genuinely-new validated signal in a while
  and a high-activity one — the search is not fully dead.
- **STOP CHECK:** NOT met. Continue — cci_mom's high activity should produce a readable forward-test
  fast; next firings get real cci_mom + macd per-pair performance.

### Iteration 30 — 2026-06-24 (macd forward-test accumulating — early +, tiny sample = noise; justified HOLD)

- **MEASURE:** macd forward-test is now flowing across the 34 pairs. DEV: 10 macd trades closed —
  macd_cross **8 @ 75% win / +$0.70** (avg +$0.088), macd_rsi 2 @ 50% / +$0.055. Close reasons:
  take_profit 4 (+$0.79, **TPs actually hitting** now), timeout 4 (+$0.26), stop_loss 2 (−$0.29).
  STAGING (curated 6 majors): 4 macd trades, **−$0.67**. 5m fleet 521 closes, 27.8% win, −$24.18
  (usual −EV). 306+12 heartbeats, 0 errors, postgres 21% / kestrel 18% (fine).
- **DIAGNOSE — honest read (✗ overclaim):** macd_cross's 75% win / +$0.70 is ENCOURAGING but a TINY
  sample (8 trades) — the backtest win was ~50–52%, so 75% is high-side small-sample noise that will
  regress (same lesson as the 5m +$5.80 that round-tripped to −$12 over iters 26→28). The dev(+0.76)
  vs staging(−0.67) split on ~14 total trades is noise, not a pair-universe signal. NOTHING is
  readable yet — need ~30–100 macd closes for a real expectancy estimate. The point of this firing:
  confirm the forward-test ACCUMULATES (it does — 0 for 7 iters, now ~14 trades + TPs hitting).
- **STAGING MAINTENANCE (step 10b, now self-correcting):** cp-refreshed promote → 12 curated bots,
  unchanged (no per-pair cell has ≥10 trades clearing win>50%+net>0) → no churn. The cp-first fix
  (iter-29) held — no stale-script balloon.
- **JUSTIFIED HOLD (no deploy, no reset):** macd <30 closes, no infra fault, no new candidate beats
  the validated leads cross-era (search saturated). Let the macd sample build. Fleet byte-identical to
  live → no reset.
- **STOP CHECK:** NOT met (≪100 trades, sample meaningless). Continue — accumulate toward a readable
  macd expectancy across dev (broad) + staging (curated majors).

### Iteration 29 — 2026-06-24 (iter-28 broadening WORKED — first macd trades ever, from small-caps; HOLD to accumulate + fixed stale-promote tooling)

- **MEASURE — the iter-28 payoff:** the broadened macd cohorts produced the **FIRST macd trades in
  project history**: 2 `macd_cross` signals (1 closed +$0.055 win, 1 open) — both on **small-cap pairs
  (ATOM, TIA)**, exactly the iter-28 hypothesis (small-caps are more volatile / less QUIET → crosses
  fire where the 6 majors stayed QUIET-blocked for days). macd_rsi 0 yet. The 7+ iterations of "0
  trades" are over; the forward-test is moving. 5m fleet 283 closes, 25.8% win, −$16.37 (fresh
  post-iter-28-reset, usual −EV). 306+12 heartbeats, 0 errors, postgres 13% / kestrel 17%.
- **STAGING-TOOLING BUG (found + fixed, no fleet interruption):** running step 10b's promote in-container
  returned a **68-bot** staging fleet instead of 12 — the baked promote was STALE (iter-28's
  `_LOCKBOX_SEED_PAIRS` pair-filter was a config-only deploy, no rebuild, so the container ran the
  iter-25-baked promote without the filter → `_lockbox_seed` cloned all 68 macd bots). The RUNNING
  staging fleet was still the correct 12 (host bots.staging.json untouched), so no harm done — but the
  next loop firing would have ballooned staging. FIX: `docker compose cp` the current promote into the
  container (→ correctly produces 12 curated bots, unchanged → no churn) AND added a cp step to
  RESEARCH_LOOP.md step 10b so the loop self-corrects against baked-script staleness every firing.
- **HOLD (no strategy deploy, no reset):** the macd forward-test just started producing data (1 closed
  trade — far too few to evaluate); let it accumulate. No new candidate beats the validated leads
  cross-era (search saturated; 4h refuted iter-27). The only change this firing is the operational
  staging-tooling fix (cp + step-10b hardening) — not a strategy change, so no reset.
- **STOP CHECK:** NOT met (1 closed macd trade, +$0.055 — meaningless sample). Continue — accumulate
  macd trades across the 34 pairs; next firings get the first real per-pair macd performance read.

### Iteration 28 — 2026-06-24 (DEPLOY: broaden 1h macd cohorts 6→34 pairs to unblock the stalled forward-test)

- **MEASURE:** 5m fleet 832 closes, 37.0% win, **−$12.19** — the iter-26/27 +net fully reverted to
  negative (variance round-trip complete, no edge, as predicted). MACD cohorts (dev+staging): STILL
  0 trades / 0 signals after ~1.5 days armed.
- **DIAGNOSE (the stall is real, not a bug):** macd reject mix last 24h = `quiet_regime` 394 +
  `no_pattern_fired` 170. So ~70% of 1h evaluations are QUIET-blocked (legit gate — recent gate 1h
  market is persistently low-vol) and the other 30% reached the pattern scan with no qualifying cross.
  The iter-25 fix works (cohorts armed, reaching the scan) but at 6 pairs the qualifying-cross rate is
  too low to forward-test — "wait longer" has yielded 0 trades for 1.5 days.
- **ACTION — broaden, don't wait (mandate-aligned):** expanded both macd_cross + macd_rsi from the 6
  backtested pairs to the FULL liquid universe (SCALP_PAIRS, 34 pairs). More pairs — esp. more-volatile
  small-caps — = more non-QUIET windows = real forward-test velocity, and directly serves §13 (broad
  pairs / hundreds of bots / more activity). The SIGNAL is validated cross-era on the 6 tested pairs;
  broadening is forward-test BREADTH, not a new edge claim (honest). Fleet **250→306** (56 NEW macd
  bots). Staging SEED stays curated to the 6 lockbox-validated pairs (`promote._LOCKBOX_SEED_PAIRS`) —
  the broadened forward-test pairs only promote to staging if they earn win>50%+net>0 on their own.
- **SHIP:** ruff + (no src change, mypy N/A) · promote/regime tests green · dedup 56 NEW + 250 SEEN ·
  committed+pushed `1a77e35` · CI green · config-only redeploy (bots.json restart, no rebuild) · FULL
  RESET (wiped dev slate, KEPT candles + microstructure, staging untouched) · backfilled all 68 dev
  macd bots' 1h candles (iter-20 lesson) · 318 heartbeats wiped→306 dev + 12 staging repopulated,
  trades=0, 0 errors, postgres 14% / kestrel 16% (host 7.4Gi free — 306 bots fine, 1h bots are light).
- **STOP CHECK:** NOT met. Continue — the macd forward-test should FINALLY produce trades within days
  now that it spans 34 pairs; next firings measure real macd activity + per-pair performance.

### Iteration 27 — 2026-06-23 (5m +net REVERTED as predicted; ACTIVE SEARCH: 4h MACD data-mined → REFUTED; 1h confirmed TF-specific; HOLD)

- **MEASURE:** 5m fleet 572 closes, **40.4% win, +$0.66** — the iter-26 +$5.80/47.4% (291 trades)
  DECAYED to +$0.66/40.4% (572): the extra ~281 trades were net-negative. The +net was VARIANCE
  reverting to the −EV mean, exactly as iter-26 predicted. MACD cohorts (dev+staging): still 0 trades
  / 0 signals — armed (iter-26 verified they reach the pattern scan) but no qualifying cross in a
  non-QUIET 1h regime yet (recent gate 1h market frequently QUIET). 250+12 hb, 0 errors, mem fine.
- **ACTIVE SEARCH — does the MACD edge improve at 4h? (TF dimension of the validated signal):**
  ran macd_cross_ct/macd_rsi/sma_cross_9_21 at 4h, maker, 6 pairs, recent + LOCKBOX.
  - RECENT 4h looked GREAT: macd_cross_ct/tight 52.2% win, expR **+0.20** (higher than 1h's +0.15),
    IS→OOS +0.022 — tempting.
  - **LOCKBOX 4h: ALL NEGATIVE** — macd_cross_ct −0.10/−0.06, macd_rsi −0.10/−0.14, sma_cross −0.03.
    Recent +0.20 → lockbox −0.10 = the CLASSIC data-mining collapse (identical signature to the
    mom_adx 4h trap that started this project's lockbox discipline).
  - **VERDICT: 4h MACD REFUTED.** And the contrast is the real finding: the SAME signal is +EV in
    BOTH eras at 1h (recent +0.15/lockbox +0.14) but +recent/−lockbox at 4h → the MACD edge is
    **TF-SPECIFIC to 1h**, not "any slow TF." This STRENGTHENS the 1h leads (genuinely cross-era,
    not a lucky TF) and closes the "try 4h for more activity" tangent. Added to the refuted ledger.
- **HOLD (no deploy, no reset):** 4h refuted; 1h leads unchanged and armed; 5m +net is variance;
  search saturated. Nothing byte-worthy → fleet identical to live. Staging seed unchanged (no dev
  cell clears win>50%+net>0). Deliverable = the 4h refutation + the TF-specificity finding.
- **STOP CHECK:** NOT met. Continue — let the 1h macd cohorts accumulate their first real crosses.

### Iteration 26 — 2026-06-23 (iter-25 fix VERIFIED live; 5m +net this window = variance not edge; justified HOLD)

- **MEASURE (since iter-25 reset):** 5m fleet 291 closes, **47.4% win, +$5.80 net** — broadly positive
  across ALL momentum strategies (trend_momentum +$4.15/48.1%, triple_mom +$1.19/47.1%, mom_adx
  +$0.53/47.7%). MACD cohorts (dev+staging): still 0 trades / 0 signals. 250+12 heartbeats, 0 errors,
  postgres 14% / kestrel 17% / staging 30%.
- **VERIFY iter-25 fix (the deliverable):** macd signal-event reject mix over the last 3h is now ONLY
  `quiet_regime` (38) + `no_pattern_fired` (34) — the `no_trend_alignment`/`no_trend_streak`
  empty-permitted artifacts are GONE. That proves the regime-permit fix works: macd now REACHES the
  pattern scan and is correctly armed (no_pattern_fired = evaluated, no cross right now). 0 trades is
  genuine: the recent 1h gate market is frequently QUIET (38 quiet rejections/3h) and a trend-aligned
  cross is rare — but the cohorts can now finally fire when one occurs.
- **DIAGNOSE — the 5m +net is VARIANCE, not edge (honest, ✗ overclaim):** nothing changed in the 5m
  fleet (iter-25 only touched regime.py for macd). The same momentum strategies were −$24 last window
  (iter-25) and are lockbox-NEGATIVE / refuted across all prior iters. A 47% win / +$5.80 over 291
  trades is well within a coin-flip momentum book's variance during a favorable (trending) stretch.
  This is exactly the recent-window positive the lockbox exists to refute — NOT a deployable edge.
- **STAGING MAINTENANCE (step 10b):** ran `promote --min-win 50`. The +net 5m cells all win <50%, so
  the win-floor correctly EXCLUDES them (illustrating the owner's win>50% criterion in action — it
  keeps the most-profitable-this-window cell, trend_momentum +$4.15 @ 48%, OUT of staging). No dev
  cell clears win>50%+net>0 → staging keeps the 12 lockbox-lead seed UNCHANGED → no churn (skip-if-identical).
- **JUSTIFIED HOLD (no deploy, no reset):** the macd cohorts JUST got armed (iter-25) and must
  accumulate — churning now wastes the fix; the 5m +net is variance not a candidate; the obvious
  indicator search space is saturated (MACD/MA/RSI/stoch/ADX-confluence all tested, last one refuted).
  Manufacturing a deploy here = theater. Fleet byte-identical to live → no reset.
- **STOP CHECK:** NOT met (no lockbox-confirmed edge; macd armed but no trades yet; 5m +net is
  variance). Continue — the macd forward-test can finally accumulate; next firings measure real macd
  trades + whether the 5m +net persists or reverts (it will revert — no edge).

### Iteration 25 — 2026-06-23 (BUG FIX: the 1h MACD cohorts were STRUCTURALLY INERT since iter-18 — regime-permit omission → 0 trades ever)

- **MEASURE:** dev 543 5m closes, 30.6% win, −$24.25 (known −EV book). **Both 1h MACD cohorts:
  STILL 0 trades** — not "rare cross," investigated. Staging 12 bots up, 0 trades. 250+12 heartbeats,
  postgres 15%/kestrel 16%, 0 errors. CI on prior commits green.
- **DIAGNOSE (read-first, the real cause):** macd cohort events were dominated by `quiet_regime` (76),
  `no_pattern_fired` (54), `no_trend_alignment`/`no_trend_streak` (62). Reading detector.evaluate:
  `permitted = regime_patterns ∩ enabled_patterns`, and **macd_cross/macd_rsi were in NONE of
  `regime_permits_pattern`'s allowed sets** (they were registered + self-directing in iter-18/22 but
  never listed in regime.py). So `permitted` was **ALWAYS EMPTY** for macd bots → they could never
  reach the pattern scan → 0 trades EVER. The `no_pattern_fired` events were empty-permitted (not
  "evaluated, no cross"); the trend rejections were the empty self-direct set
  (`permitted & SELF_DIRECTING` = ∅) falling through the trend gate. The cohorts have been INERT the
  whole time (iter 18→25) — every "0 trades, rare cross, expected" read in iters 19-23 was WRONG.
- **FIX (regime.py, agent scope):** listed macd_cross/macd_rsi in TRENDING/VOLATILE/RANGING — exactly
  like mom_adx/triple_mom, and matching the VALIDATION (algo_search neutralises the regime-permit gate
  = permit-all-ex-QUIET, so the lockbox +EV is for the regime-ungated signal). QUIET stays blocked for
  all. Only the 24 macd bots affected; the 238-bot 5m fleet is untouched. +regime unit test.
- **VERIFIED LIVE (new image):** `regime_permits_pattern('macd_cross', …)` → [True,True,True] for
  non-QUIET; `evaluate()` on a real DOGE/BTC 1h window now reaches **stage=pattern** (`no_pattern_fired`
  = armed, no cross right now) instead of empty-permitted. The cohorts (dev + staging) are now armed
  and will fire on the next qualifying cross.
- **SHIP:** ruff + mypy src/ clean · regime/detector/patterns tests green · committed+pushed `67ed772`
  · CI green · rebuilt image · FULL RESET (wiped dev+staging slate, KEPT candles + microstructure;
  bot_ids unchanged → no backfill) · recreated labs + staging on the fixed image · 262 heartbeats,
  trades=0, 0 errors. Staging maintenance (step 10b) ran: no dev cell clears win>50%+net>0 → staging
  keeps the 12 lockbox-lead seed (now also un-inerted by the same fix).
- **LESSON (codified):** adding a new registered pattern is NOT enough to make it fire live — it must
  ALSO be listed in `regime_permits_pattern` (regime.py) for at least one non-QUIET regime, else
  detector.evaluate's `permitted` set is empty and the bot is silently inert (no signals row; only
  signal_rejected events). Same inert-cohort class as the iter-20 backfill bug, different cause.
- **STOP CHECK:** NOT met (no edge; cohorts now armed but no live trades yet). Continue — the macd
  forward-test can FINALLY accumulate data; next firings measure real macd trades.

### Iteration 24 — 2026-06-23 (owner directive: ACTIVATE Phase-2 STAGING — curated best-performers pool, full-verbosity Grafana, loop-maintained)

- **TRIGGER:** owner — "[the 3-phase plan] phase 1 = deploy hundreds to try everything; phase 2 =
  staging, the place of best performers, select best + replace unproductive; phase 3 = prod, real
  money, not now. I want staging Grafana to have the SAME verbosity as phase 1. Do it now and have
  the loop do this." This activates the dormant Phase-2 scaffolding (`promote_to_staging.py`,
  `Env.STAGING`, `docker-compose.staging.yml`) — see [[project_three_phase_architecture]].
- **BLOCKER + DECISION:** staging DI routed only to the LIVE code path on a demo venue (needs BingX
  VST keys we don't have). Owner defers real money to phase 3, so I added a **`STAGING_ENGINE=sim`**
  flag (config.py + execution/providers/__init__.py — NOT a frozen file): ENV=staging + sim →
  SimulationExecution, so the curated pool runs NOW, isolated in env='staging'. The live-demo path is
  preserved for when VST keys arrive (one env change). Added test_staging_routing cases.
- **SELECTION (`promote_to_staging.py` reworked):** now CLONES each winner's exact dev bot config
  (tf + exit params + patterns) with a `staging-` id (was hardcoded 4h + a fixed _EXIT — broken for
  the 5m/1h fleet); ranks by **EXPECTANCY** (avg net/trade — not win rate, which is gameable); falls
  back to the **lockbox-validated leads** (macd_cross/macd_rsi) when no dev cell is +EV. Rewrote its
  unit tests. Today the dev fleet has 0 +EV cells → staging seeded with the 12 lockbox-lead bots
  (6 macd_cross tp1.4/sl1.0/hold4 + 6 macd_rsi tp2.0/sl1.0/hold6), each cloned with its real bracket.
- **GRAFANA same verbosity:** discovered the phase-1 dashboard already templates every query by an
  `env` variable (dev/staging/prod dropdown). Rewrote `build_staging_dashboard.py` to emit
  `kestrel-staging.json` as a FULL CLONE of the 153-panel phase-1 board locked to env='staging'
  (was a slim 36-panel ops board). Staging dashboard now has byte-identical verbosity; any future
  phase-1 panel flows to it automatically. (Old live-ops panels kept in git history for the venue era.)
- **DEPLOYED:** `.env.staging` (host-local, STAGING_ENGINE=sim, FEED_MODE=poll for 1h, telegram
  silenced, shared DB) → rebuilt image (staging-DI code) → recreated labs (dev unchanged, NO reset)
  → backfilled the 12 NEW staging bot_ids (720 1h candles each, iter-20 lesson) → brought up the
  `kestrel-staging` compose project (shares postgres+grafana, 512m). VERIFIED: 12 staging heartbeats,
  12 daemon_ready, **0 errors** (sim DI confirmed — no venue connect attempted), 8640 staging candles,
  Grafana staging dashboard live at 173 panels. Memory fine (staging 157MiB; host 8.1Gi free).
- **LOOP INTEGRATION:** added protocol step 10b (PHASE-2 STAGING MAINTENANCE) — every firing
  re-runs promote, and only churns staging if the best-performer selection CHANGED (skip-if-identical,
  so staging accumulates between real changes). Phase-3 promotion stays §18 human-gated.
- **SHIP:** ruff + mypy src/ clean · staging-routing/promote/config tests green · committed+pushed ·
  CI green. NO dev reset (dev fleet unchanged; staging is a separate project).
- **HONEST:** "best performers" today = the lockbox-validated leads + (none yet) +EV live cells —
  there is still NO confirmed edge; staging is a cleaner *selection + visibility* stage, on sim until
  VST keys. **STOP CHECK:** NOT met. Continue.

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
