# 14 — Production Runbook (BingX, real capital)

> Owner plan (2026-07-15): if the staged bots prove profitable, go live on BingX
> starting with **$50**. This runbook is the ordered path from here to that
> go-live, mapping every CLAUDE.md §18 criterion to its concrete action, plus
> the day-2 operations once live. **Nothing in this document authorizes go-live
> by itself — §18 is human-enforced, every box, in order.**

---

## 1. Current readiness state (audited 2026-07-15)

| # | §18 criterion | State | Blocking action |
|---|---|---|---|
| 1 | `install.sh` → [GO] on clean session | ⏳ untested on a prod box | Run on the prod VPS after provisioning |
| 2 | Paper trading 14 days, zero unplanned crashes | ❌ clock restarted 2026-07-13 (2 DNS-outage crashes) | Clean window through ~2026-07-27 |
| 3 | Walk-forward backtest win >55% OOS | ✅ **met** — staged cells 65–79% points win, both eras, realistic fees | DSR caveat stands (iter 56); the live legs are the compensating evidence |
| 4 | Sim vs real fills <15% deviation | ❌ needs maker execution + BingX VST demo | See §3 below — THE critical unknown |
| 5 | Watchdog proven restart | 🟡 de-facto proven (auto-recovered real crashes 07-03, 07-13) | One formal forced-kill drill |
| 6 | `stop.sh` graceful close confirmed | ⏳ | One drill on staging |
| 7 | No TODO/FIXME in risk/execution | ✅ verified 0 | — |
| 8 | Full session log reviewed by human | ⏳ | Owner reads one full day of staging events |
| 9 | Telegram alerts end-to-end | ⏳ wired, unconfirmed | Send-test on the prod box |
| 10 | DB backup cron | ✅ pg-backup container + rotated lean dumps | Replicate cron on the prod VPS |

**Code readiness:** maker execution added to `src/execution/live.py`
(owner-authorized 2026-07-15) — post-only limit entry at the signal price,
resting post-only take-profit, market-out for stops/timeouts, unfilled entries
skipped never chased; mirrors the simulator that produced every validated
number. Full unit coverage in `tests/unit/execution/test_live_maker.py`.
**Compounding already exists**: `signal/sizing.py` scales position size with
bucket equity (drawdown de-risking + loss cool-off included), so a growing
balance automatically bets larger. `MAKER_EXECUTION=true` is MANDATORY in prod.

## 2. Open owner decisions (§4 — nobody else can make these)

1. **Instrument: spot margin vs perpetuals.** CLAUDE.md §13 says spot isolated
   margin, but the 20× leverage + 0.02% maker fee + VST demo used everywhere in
   validation are BingX **perpetuals** facts. Either amend §13 (spot→perp;
   funding costs then need modeling — sim currently assumes 0) or accept spot
   margin's lower leverage and re-validate the fee model. **Everything in §3
   waits on this.**
2. **Portfolio guard values for prod** (`PORTFOLIO_DD_PCT` — crash insurance at
   20×; template ships 0.10).
3. **Promotion bar**: this runbook assumes the owner's stated gate — staging
   points win ≥65% with positive dollars at n≥100 — plus §18 all-green.

## 3. Path to go-live (ordered; ~3–5 weeks critical path)

1. **[owner]** Decide instrument (§2.1). If perps: authorize the §13 amendment;
   agent then adds funding-cost modeling to sim + backtest and re-verifies the
   staged cells clear it.
2. **[done 2026-07-15]** Maker execution in `live.py` + tests.
3. **[owner]** Create BingX VST demo keys → set `STAGING_ENGINE=live`,
   `TESTNET=true` + keys in `.env.staging`. Staging then places REAL demo
   orders through the new maker path.
4. **[agent, 1–2 weeks]** Measure §18.4: compare staging's sim-priced fills vs
   VST fills — entry fill RATE (the ~90% maker-fill assumption), fill price
   deviation, TP fill behaviour. **Abort criterion: if the real maker fill rate
   is materially below the sim assumption and the deviation exceeds 15%, the
   edge does not survive live and go-live is off until redesigned.**
5. **[parallel]** The 14-day zero-crash window matures; staging accumulates
   toward n≥100.
6. **[owner + agent]** Freeze the prod fleet: replace `bots.prod.json`
   placeholders with the ACTUAL staging leaders at that moment (bar: ≥65%
   points win, positive net dollars, n≥30 per cell in staging).
7. **[owner]** Provision the prod VPS (§13: SG/Tokyo, 1 vCPU, $4–6/mo), fund
   BingX with **$50**, create REAL keys (trade-only, no withdrawal permission),
   copy `.env.prod.example` → `.env` with real values.
8. **[both]** On the VPS: `install.sh` → must print **[GO]**; Telegram
   send-test; watchdog forced-kill drill; `stop.sh` graceful-close drill;
   pg_dump cron; owner reviews one full session log. Every §18 box ticks.
9. **[owner]** Start with `scripts/start.sh`. First 48h = human-monitored (§30).

## 4. The $50 phase — what it is and is not

- 5 bots × $10 isolated buckets (template in `bots.prod.json`). Compounding is
  per-bucket and automatic; expect single-digit dollars per week at the
  validated +8–15 bps/trade — **this phase validates live mechanics and real
  fills, it is not the income phase.**
- Scale-up rule (owner's "if it always gains, will it scale?"): capacity at
  these sizes is a non-issue (a $200 maker order on 1h XRP/SOL is invisible;
  the model holds to thousands of dollars). Scale by ADDING buckets/bots as
  equity grows — $100 → 10 buckets — only after ≥100 live prod trades match
  staging within tolerance. Bucket size itself stays $10 (§13; changing it is
  §4 owner-only).
- **Kill rules (day-2 ops):** daily loss limit −$5 hard-stops entries (risk
  Rule 5); portfolio guard force-closes at −10% aggregate unrealised; and the
  human rule — if prod diverges from staging by more than the backtest→live
  tolerance after 30 trades, stop and investigate before adding a dollar.

## 5. Honest framing (stated once)

The points program is the strongest evidence this project has produced — 65.8%
live points win at n=38, on-thesis with both backtest eras — and it is still
n=38, still short of the 70%@100 program target, still DSR-unproven, and its
economics die without maker fills. The $50 gate exists precisely because the
remaining uncertainty is real. Every step above shrinks it; none of them skip it.
