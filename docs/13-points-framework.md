# 13 · The Points Framework — measurement, strategy, and targets

*Adopted 2026-07-09 (owner directive). This document defines the project's new primary
scoreboard — **points and win rate, not dollars** — the strategy program built on it, the
daily and program-level targets, and the scalability model. It supersedes the dollar-PnL
framing in earlier docs as the day-to-day optimization target; the dollar layer returns
later as a separate, sequenced phase (§8).*

---

## 1. Why points — what the reframe actually buys us

A month of live fleet data plus 54 research-loop iterations produced one clean diagnosis:
the project has been optimizing **two problems at once** and failing at their intersection —

1. **Signal quality** — do our entries predict direction? (measured in *price movement*)
2. **Economics** — does the captured movement exceed fees? (measured in *dollars*)

Every dollar-based verdict entangles them: a signal that reliably captures +3 bps of
favorable movement per trade is a *real, valuable signal* — and a guaranteed dollar-loser
at today's ~4 bps maker round trip. Under a dollar scoreboard it reads "refuted"; under a
points scoreboard it reads "found signal, need 1 bp more or cheaper fills." Those are
completely different research conclusions demanding different next actions.

**The points reframe separates them cleanly:**

- **Points** (§2) measure raw directional capture, *gross of all fees* — pure signal quality.
- **Win rate** becomes an explicit, engineerable property of **exit geometry** (§3) rather
  than an emergent accident.
- **Scalability** falls out for free: points are expressed in basis points of entry price,
  so a result on PEPE and a result on BTC are directly comparable, and a validated
  points-config scales across pairs and position sizes without re-derivation (§7).

This is also how professional quant research is actually organized: alpha research in bps
of gross movement first, transaction-cost analysis as a separate later layer.

> **The honest caveat — stated once:** points-positive + high win rate does **not** by
> itself equal dollar profit; fees live in dollars and are charged per trade regardless of
> geometry. The framework's promise is *sequencing*, not magic: first find and maximize
> genuine directional capture (this document), then buy the required cost headroom with the
> venue/fee lever (BingX maker, fee tiers, §4 owner decisions). §6's target ladder makes
> the bridge explicit so nobody is surprised later.

---

## 2. Units and metrics

### 2.1 The point

**1 point = 1 basis point (bp) of entry price**, direction-signed:

```
points = sign × (exit_price − entry_price) / entry_price × 10,000
         where sign = +1 for long, −1 for short
```

- Raw pips are pair-dependent (one BTC "pip" ≠ one PEPE "pip") and are **not** used.
- Points are computed **gross** — before entry fee, exit fee, and slippage. Fees are
  tracked separately as a constant hurdle (maker RT ≈ 4 bps, taker RT ≈ 18 bps of notional).
- At 1h on our fleet, **1 × ATR(14) ≈ 97 bps median** (p25 79 / p75 123, measured on live
  candles 2026-07) — so ATR-multiple exits translate to points at roughly 1×ATR ≈ 100 pts.

### 2.2 The R-multiple

**R = the initial risk distance** (|entry − SL|). Every trade result is also expressed in
R: a win at a TP placed 0.5 R away is +0.5 R; a full stop-out is −1 R. R-multiples
normalize across volatility regimes the way points normalize across pairs.

### 2.3 The metric set (the new scoreboard)

| Metric | Definition | Replaces |
|---|---|---|
| **Points win rate** | share of trades with points > 0 | dollar win rate |
| **Points expectancy** | mean points/trade (gross) | $ expectancy |
| **R-expectancy** | mean R/trade | — |
| **Points profit factor** | Σ winning points / Σ \|losing points\| | $ PF |
| **e-ratio (MFE/MAE)** | median favorable excursion ÷ median adverse excursion, per candle since entry, ATR-normalized | *(new — the "is there any tilt at all" detector)* |
| **Breadth** | # pairs with points expectancy > 0 in **both** eras | unchanged |
| **Stability** | weekly points expectancy, sign consistency | unchanged |

All the anti-self-deception discipline **carries over unchanged**: walk-forward 60/40,
the untouched prior-year lockbox, ≥3-pair breadth, the deflated-Sharpe haircut (computed
on the per-trade points series instead of dollars), and the refuted ledger. Points change
*what* we score, not *how rigorously* we score it.

---

## 3. The geometry law of win rate — and why 70% was never reachable before

Win rate is **primarily an exit-geometry choice, not a signal property**. For a TP placed
`a` away and an SL placed `b` away, define the geometry ratio `g = a/b` (TP distance over
SL distance). For an entry with *zero* predictive tilt (a random walk):

```
P(win) ≈ 1 / (1 + g)          and          E[points] = exactly 0
```

| g = TP/SL | Win rate (no tilt) | Example bracket @ 1h (ATR ≈ 100 bps) |
|---|---|---|
| 1.2 *(current Rule-3 floor)* | **≈ 45%** | tp 1.2×ATR / sl 1.0×ATR |
| 1.0 | 50% | tp 1.0 / sl 1.0 |
| 0.6 | 62.5% | tp 0.9 / sl 1.5 |
| **0.5** | **66.7%** | **tp 0.6 / sl 1.2** |
| 0.43 | 70% | tp 0.6 / sl 1.4 |
| 0.33 | 75% | tp 0.5 / sl 1.5 |

Two consequences, both central:

1. **The 70% win-rate target and risk Rule 3 (R/R ≥ 1.2) were mathematically incompatible
   from day one.** With g ≥ 1.2 the no-tilt baseline is ~45%, and our real signals — with
   genuine but small tilt — live at 39–48% (measured, §4). The fleet was never going to
   print 70% under that geometry no matter how good the entries got. Reaching 70% requires
   g ≈ 0.4–0.5. *Backtesting inverted geometry needs no permission (the harness already
   bypasses risk gates); deploying it live requires a Rule-3 amendment — `risk/manager.py`
   is frozen/§24 human-only, flagged as an owner decision.*
2. **Geometry buys win rate for free, but expectancy stays zero without real tilt** — the
   E[points] = 0 identity is the framework's built-in honesty. A 75%-win config with zero
   points expectancy is a coin flip wearing makeup. Therefore the scoreboard is always the
   **joint target**: `win rate ≥ target AND points expectancy > 0`. Win rate alone is
   never reported as success.

The payoff arithmetic at the target geometry (why this all coheres): at g = 0.5 with a
1.2×ATR stop (TP ≈ 58 bps, SL ≈ 116 bps at the median 1h ATR), a genuine 70% win rate gives

```
E[points] = 0.70 × 58 − 0.30 × 116 ≈ +5.8 bps/trade gross
```

— which is above the ~4 bps maker fee floor. **The owner's 70% win-rate target, hit at
this geometry with real tilt, is simultaneously the first economically viable
configuration the project would ever have.** That equivalence is why this reframe is
worth the effort and not a vanity-metric exercise.

---

## 4. The empirical baseline — what our own data says in points (2026-07-09)

Measured on the live dev fleet (612 closed trades, ghost-position bug corrected), gross
points per trade by lead:

| Lead (1h, medium exit g≈2.0) | n | avg gross points | points win % |
|---|---|---|---|
| **macd_cross** | 80 | **+14.1 bps** | 47.5% |
| macd_rsi | 86 | −2.6 bps | 45.3% |
| cci_mom | 177 | −11.4 bps | 46.9% |
| sma_cross | 73 | −21.3 bps | 39.7% |
| exp_robustwide (wide, g=2.0) | 87 | ≈ −50 bps | 29–40% |

And by close reason — the geometry problem in one table:

| Close reason | n | avg gross points |
|---|---|---|
| take_profit | 74 | **+227 bps** |
| timeout | 278 | +13 bps |
| stop_loss | 173 | **−161 bps** |

Readings:

- **One lead is already gross-positive live above the maker floor** (macd_cross +14 bps).
  The tilt the backtests found (iter 42: all four leads gross-positive cross-era) is
  visible in live points too — small, but real and measurable.
- **Stops fire 2.3× as often as TPs and cost 161 bps each.** Under the current
  wide-target geometry, one stop erases ~12 average timeouts of gains. The entire
  observed win-rate ceiling (~39–48%) is the g ≥ 1.2 geometry expressing itself, exactly
  as §3's table predicts.
- **The timeout row is the buried treasure:** 278 trades that hit neither bracket drifted
  +13 bps favorable on average. Our entries have positive drift that the current bracket
  geometry fails to harvest — a tight TP placed *inside* that drift is precisely what the
  high-win geometry does.
- **Activity baseline:** ~44 closed trades/day fleet-wide over the last 10 days —
  *while ~half the fleet was jammed by the ghost-position bug* (fixed 2026-07-09). True
  capacity is plausibly 60–90/day. This calibrates the daily targets in §6.
- **The MFE/MAE corpus exists:** 208,650 trade-linked candles across 834 trades
  (`trade_context`) — enough to mine empirical excursion curves per lead (§5 S3).

---

## 5. The strategy program (proposed, ranked)

Entries stay the **five cross-era-validated leads** — `cci_mom`, `sma_cross`,
`macd_cross`, `macd_rsi`, `ensemble_3of4` — plus specific revivals below. The innovation
is **geometry + measurement**, not another indicator sweep (that space is exhausted and
stays exhausted; see the refuted ledger in `RESEARCH_LOOP.md`).

### S1 — HiWin re-geometry of the validated leads *(primary)*

Sweep the five leads at 1h under **inverted-geometry exit brackets**, scored entirely in
points:

| Preset | tp_atr | sl_atr | g | no-tilt win | max_hold |
|---|---|---|---|---|---|
| hiwin50 | 0.6 | 1.2 | 0.50 | 66.7% | 4 |
| hiwin43 | 0.6 | 1.4 | 0.43 | 70.0% | 4 |
| hiwin33 | 0.5 | 1.5 | 0.33 | 75.0% | 6 |
| scratch | 0.5 | 2.0 | 0.25 | 80.0% | 3 *(time-stop dominant)* |

Walk-forward OOS + prior-year lockbox, ≥ 10 pairs, `--by-pair` breadth, per the standing
methodology. **Deploy bar (points version of the cross-era gate):** points win ≥ 65% AND
points expectancy > 0 in BOTH eras on ≥ 3 pairs.

*Prediction to falsify:* the leads' tilt is real (§4), so at g = 0.5 they should print
66–72% win with expectancy in the +2…+6 bps range. If instead expectancy collapses to ≤ 0
at every g, the tilt was an artifact of the long-bracket geometry and this framework
kills the leads honestly — either outcome is information.

> **S1 VERDICT (run 2026-07-09, same day — see `RESEARCH_LOOP.md` iter 55):** the
> prediction held and then some. 18/25 combos clear the joint bar in the recent year,
> 19/25 in the lockbox; **12 cells clear it in BOTH eras at maker-viable (≥ +4 bps)
> expectancy**. Top cell `ensemble_3of4/hiwin33`: **76.2% / +7.67 bps recent ·
> 77.8% / +13.59 bps lockbox**, 6-pair both-eras core, **net-of-maker dollars positive
> in both eras**. The §6.3 backtest legs are met; the remaining legs are the ≥100-trade
> live forward test (blocked on the §9 Rule-3 owner amendment), a points-DSR, and a
> taker fill-stress. The S3 miner independently corroborated the geometry (front-loaded
> drift, empirical g ≈ 0.4–0.7).

### S2 — Mean-reversion re-audit under high-win geometry

The entire fade family (`rsi2`, `stoch_revert`, `cci_revert`, `bb_fade`,
`wick_rejection`, `anomaly_fade`, `vwap_revert`) was refuted under **net-dollar scoring
at g ≥ 1.2** — a geometry that is *structurally wrong for the strategy class*.
Mean-reversion's natural payoff shape is exactly high-win/small-gain: price snaps back a
little, often. Re-test the family in gross points at g ∈ {0.33, 0.5}, tight targets
inside the snapback distance.

*Refuted-ledger compliance:* the ledger bans re-tries "without a materially new variant."
Inverted geometry + gross-points scoring is a materially new variant — the prior
refutations never asked this question. If the family fails *again* under its natural
geometry, it earns a permanent, unconditional ledger entry.

### S3 — MFE/MAE exit mining on our own live corpus

Use the 208k-candle `trade_context` corpus to compute, for each lead, the empirical
**maximum favorable / adverse excursion curves** by candles-since-entry: how far does
price actually run our way (and against us) after a real live entry, candle by candle?
Then *derive* the bracket from the data instead of guessing ATR multiples:

- TP at the ~60th percentile of MFE (a target most trades actually reach),
- SL just beyond the noise band of MAE for *winning* trades (a stop losers hit but
  winners rarely graze),
- time-stop at the candle where the median favorable excursion stops growing.

This answers the exit-design question with our own month of live data and either
corroborates or replaces S1's hand-picked presets. It is also the first real use of the
trade-context system for its designed purpose (offline exit research).

### S4 — The "marginal" revival bucket

`stoch_ct` and `compress_vol_break/wide` were +EV in **both eras** but below the dollar
deploy bar — parked as "real but too small." Under points scoring with S1 geometry they
re-enter the sweep. Cheap to include, already coded in the harness.

### Validation flow for everything above

```
S1–S4 backtest (points scoreboard, both eras, --by-pair)
      → survivors: deploy as exp_hiwin cohort (paper, dev fleet)   ← Rule 3 blocks live g<1.2:
                                                                      backtest is unaffected;
                                                                      live cohort needs §24 owner amendment
      → live forward-test ≥ 100 closed trades at the points targets
      → only then the dollar phase (§8)
```

---

## 6. The targets

### 6.1 Per-trade (the configuration bar)

| Metric | Minimum (validate) | Standard (deploy) | Stretch |
|---|---|---|---|
| Points win rate | ≥ 65% | **≥ 70%** | ≥ 75% |
| Points expectancy | > 0 bps | **≥ +4 bps** | ≥ +18 bps |
| R-expectancy | > 0 | ≥ +0.04 R | ≥ +0.15 R |
| Points profit factor | ≥ 1.05 | ≥ 1.15 | ≥ 1.30 |
| Breadth (both eras) | ≥ 3 pairs | ≥ 5 pairs | ≥ 10 pairs |

The expectancy ladder is the fee bridge made explicit: **> 0** = the signal is real;
**≥ +4 bps** = viable at maker fills; **≥ +18 bps** = viable even at taker (fill-robust,
the real-money-safe tier). Points stay the scoreboard; the ladder just tells us *which
shelf* a result sits on.

### 6.2 Daily (the fleet scoreboard — replaces daily $ PnL as the headline)

| Metric | Initial target | Standard target |
|---|---|---|
| Fleet points win rate (day) | ≥ 65% | **≥ 70%** |
| Fleet aggregate points (day) | **≥ +100 bps** | ≥ +250 bps |
| Points expectancy (day) | > 0 | ≥ +4 bps/trade |
| Closed trades (day) | ≥ 30 | ≥ 60 |

Arithmetic sanity: 44 closed/day (current, half-jammed fleet) × +2.5 bps ≈ +110 bps/day;
60/day × +4 bps = +240 bps/day. The initial bar is reachable with the measured activity
and a *small* real tilt; the standard bar needs the S1 geometry actually working. These
recalibrate after the first S1 sweep — they are honest first bars, not promises.

### 6.3 The program target ("the target")

> **A configuration family that, on walk-forward OOS + the untouched prior-year lockbox
> AND a ≥ 100-trade live forward test, holds: points win rate ≥ 70%, points expectancy
> ≥ +4 bps/trade, R-expectancy > 0, breadth ≥ 5 pairs.**

That is the points-framework restatement of the research loop's stop-condition #2 — and
by §3's arithmetic it is *simultaneously* the owner's 70% win-rate target and the first
maker-viable economics. One target, all three requirements aligned. When it is met, the
program moves to the dollar phase (§8); until it is met, nothing is promoted, exactly as
before.

### 6.4 Daily reporting

The Grafana boards and the research loop's MEASURE step adopt the §2.3 metric set:
per-lead points expectancy, fleet points win rate, aggregate daily points, close-reason
points profile. Dollar columns remain visible (they're one query away and keep us honest)
but stop being the headline.

---

## 7. Scalability — what scales, what doesn't, and the metric for it

**Points are scale-free in both directions that matter:**

1. **Across pairs** — bps of entry price is dimensionless, so a +4 bps/trade config is
   the same result on BTC and on PEPE. Scaling out = adding pairs that pass the per-pair
   cross-era gate (the iter-43/45/53 playbook: validate per pair, concentrate, never
   blanket-deploy). *Scalability metric: points expectancy holds (±1 bp) as breadth
   grows — a config whose expectancy degrades as pairs are added is concentration luck,
   not a scalable signal.*
2. **Across position size** — points don't know the stake. A validated points config is
   identical logic at $10, $100, or $1,000 buckets. Dollar-side limits (order-book depth,
   own-impact slippage) only bind far above this project's sizes and are a Phase-D
   concern, tracked but not blocking.

**What does NOT scale and must not be hidden by the reframe:** fees are charged per
trade, so *activity* multiplies the fee bill linearly while points-per-trade stays flat.
More trades at +2 bps gross is *worse* in dollars at a 4 bps fee, not better. The daily
aggregate-points target (§6.2) therefore only counts as met when the per-trade
expectancy bar is ALSO met — aggregate points bought with sub-hurdle expectancy and
volume is the one gaming path this framework explicitly refuses.

Fleet mechanics (bots, shared WS feeds, per-pair bot_ids, the registry dedup guard)
already scale to hundreds of bots and carry over unchanged.

---

## 8. The bridge back to dollars (Phase D — later, brief)

When (and only when) §6.3 is met: flip the validated cohort's scoring to net dollars on
BingX **maker** execution, verify the §18 sim-vs-real fill deviation criterion (< 15%)
on demo/tiny-real orders, then walk the standard go-live gates (`12-go-live.md`). The
points result tells us exactly how many bps of cost headroom exist; the venue/fee-tier
decision (§4, owner) buys the rest. No dollar promise is made here — the bridge is
mechanical once the points target is genuinely met, and impossible before it.

---

## 9. Implementation plan

**Agent-buildable now (no permissions needed):**

1. `algo_search.py --points` mode — score in gross points/R (win %, expectancy, PF,
   per-pair breadth), add the four `hiwin*` exit presets (§5 S1).
2. `scripts/analyze_excursions.py` — the S3 MFE/MAE miner over `trade_context`.
3. Run S1 → S4 sweeps (recent + lockbox), record verdicts in `RESEARCH_LOOP.md`.
4. Grafana: a points row on the main board (per-lead expectancy, fleet win rate, daily
   aggregate points).
5. Deploy survivors as an `exp_hiwin` paper cohort — **note:** live risk Rule 3 will
   reject g < 1.2 brackets, so the live cohort runs at the closest permitted geometry
   (g = 1.2) until the owner amends Rule 3; the backtest verdicts are unaffected.

**Owner-gated (flagged, not started):**

- **Risk Rule 3 amendment** (`risk/manager.py`, §24 frozen/human-only): permit g < 1.2
  brackets for patterns validated under the §6.1 bar. Without it the 70% win target
  cannot be realized live — see §3.
- §18/§30 win-bar rewording (already a documented open item — this framework finally
  gives the win-rate criterion a coherent form: *win % joint with points expectancy*,
  rather than win % alone).
- Phase-D items: BingX keys, fee tier, venue decisions (§4).

**Sequencing:** S1 backtest first (one research-loop iteration), S3 in parallel with the
existing corpus, S2/S4 the following iteration, cohort deploy on the first survivor.
The research loop's MEASURE/BACKTEST/APPLY steps adopt the points scoreboard from the
next firing.
