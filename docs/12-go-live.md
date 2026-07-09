# 12 · Go-Live & Definition of Done

Going live with real capital is **human-enforced and not automated**. This document lists the
go-live criteria, the per-feature/strategy/deploy Definition of Done, and — most importantly —
the one thing that actually stands between Kestrel and production: a validated edge that does
not yet exist.

## 1. The single blocking fact

> **Kestrel must not trade real money. Not because the engineering isn't ready — because there
> is no proven net-of-fee edge.** 54 research iterations found five real-but-marginal
> cross-era leads whose gross capture sits at or below the fee floor; none clears the
> formal deflated-Sharpe bar ([Backtesting & Research](09-backtesting-research.md),
> `RESEARCH_LOOP.md`). The §18 criteria below are *necessary* but the edge requirement is
> the one that fails today. The current path to changing that is the
> **[Points Framework](13-points-framework.md)** program: its §6.3 program target (points
> win ≥ 70% + expectancy ≥ +4 bps + breadth, both eras + live) is the precondition for
> the Phase-D dollar bridge that would make this document's gates live again.

Concrete standing instructions (do **not** treat these as done):

- ✗ Do **not** create a funded account, request real API keys, or fund capital.
- ✗ Do **not** provision a production VPS.
- ✗ Do **not** promote staging → prod.
- ✓ **Do** run as a paper / research / forward-test platform on a live data feed.

## 2. Go-live criteria (`CLAUDE.md` §18 — human-enforced, skip none)

```
[ ] install.sh → [GO] on a clean cold-start
[ ] testnet paper trading: 14 days, zero unplanned crashes
[ ] walk-forward backtest: win rate > 55% out-of-sample        ← see §4: this bar is contested
[ ] simulated fee+slippage vs real testnet fills: < 15% deviation
[ ] watchdog: proven restart after a forced kill
[ ] stop.sh: confirmed graceful close of all positions
[ ] ✗ no TODO/FIXME in risk/ or execution/
[ ] one full session log reviewed by a human before go-live
[ ] Telegram alerts confirmed end-to-end
[ ] DB backup cron confirmed (pg_dump daily)
```

Several of these are *already demonstrated* by the lab (watchdog restart, graceful close,
Telegram, backups, the simulator's cost realism). The unmet one that matters is the edge
behind the "win rate > 55% out-of-sample" line.

## 3. Definition of Done

### Per feature (`CLAUDE.md` §30)
```
[ ] implements the spec exactly
[ ] has a corresponding test in /tests
[ ] ✗ no TODO/FIXME
[ ] ✗ no print() — structured logging only
[ ] ✗ no hardcoded values — .env or params.json
[ ] passes install.sh validation
```

### Per strategy change
```
[ ] backtest on ≥ 90 days of data
[ ] walk-forward (train 60% · test 40%)
[ ] fee + slippage model applied
[ ] win rate > 55% out-of-sample           ← contested; see §4
[ ] R/R ≥ 1.2 on average
[ ] tune.sh reports ACCEPT (no > 5% regression vs baseline)
[ ] passes BOTH a recent-year AND a prior-year lockbox  (the lockbox lesson — §5)
```

### Per deployment
```
[ ] all §18 go-live criteria met
[ ] clean cold-start verified within 24h of deploy
[ ] Telegram confirmed end-to-end
[ ] DB backup cron confirmed
[ ] stop.sh graceful close confirmed
[ ] human monitoring plan for the first 48h live
```

## 4. The contested win-rate bar (a known tension)

The "win rate > 55%" criterion in §18/§30 is, per the research, **the wrong metric** — it
structurally excludes the entire profitable trend/momentum family (which wins 30–45% and pays
off 3–5×) and only ever validates mean-reversion. Professionals validate on **expectancy +
profit factor (≥1.3) + Deflated Sharpe**, with R/R as a constraint.

This is flagged, not silently worked around. **Amending the bar is a `CLAUDE.md` change, which
is §3 human-only** — the agent surfaced it as the user's decision and cannot make it. Until the
bar is amended, even a genuinely +EV momentum strategy "fails" go-live on win rate alone. The
documentation records this as an open governance item between the contract and the evidence.

> **2026-07-09 update:** the [Points Framework](13-points-framework.md) finally gives the
> win-rate criterion a coherent form — **win rate joint with points expectancy** at an
> explicitly chosen exit geometry (win % alone is purchasable for free via geometry and
> means nothing; the joint pair is not). It also surfaced a *second* frozen-file tension:
> risk **Rule 3 (R/R ≥ 1.2)** mathematically caps win rate near 45–50%, so the §18 55%
> bar and the owner's 70% target are unreachable under the current risk config — both the
> win bar (§18/§30) and Rule 3 (§24) need owner amendment for the high-win geometry to go
> live. See [Risk & Capital §2](06-risk-and-capital.md) and Points Framework §3/§9.

## 5. The lockbox lesson (added to the bar in practice)

A single out-of-sample window — even 365 days across 10 pairs — is **not sufficient** evidence
of edge. Correlated crypto pairs behave like ≈ one effective bet, and one year ≈ one market
regime. The `mom_adx` episode proved this: it passed a recent-year 10-pair walk-forward, was
promoted, and was then refuted by an untouched prior-year lockbox. **Any future candidate must
pass both a recent-year and a prior-year lockbox before it is believed** — this is now the
de-facto standard layered on top of §30, even though §30's text predates it.

## 6. What would actually change the verdict

Not more tuning of OHLCV patterns or params — that space is exhausted. The verdict changes only
with **genuinely new information or structure**:

- **Funding-rate harvesting** (long spot + short perp) — the highest-ceiling structural
  candidate identified; needs two-leg infrastructure, a funding feed, and larger buckets than
  $10. Scoped, not built.
- Order-book microstructure / alternative data — a new information source, not a new chart
  pattern.
- Cross-sectional momentum on a basket (rank, rotate) — the best-pedigreed academic extension;
  needs basket infrastructure.
- Properly cross-validated ML (Deflated Sharpe + Combinatorial Purged CV + embargo), *if* it
  surfaces structure the linear feature scan (`edge_scan.py`) missed.

Until one of these produces a cost-robust, **cross-year-robust** edge, Kestrel stays exactly
where it is: a well-built research platform that is honest about not having found gold yet.
