# 05 · Strategies & Patterns

This is the **algorithm catalogue**. Every entry strategy in Kestrel is a *pattern*: a pure
function `(candles, params) -> PatternResult | None` registered into
`signal/patterns.py:registry`. The detector ([Signal Engine](04-signal-engine.md)) looks
patterns up by name; there is no hardcoded dispatch. A pattern only decides **entry and
direction** — the *exit* behaviour (TP/SL/hold/trailing) is expressed by each bot's parameter
profile, not in the pattern.

Eleven patterns are registered. They fall into four families:

1. **The five classic patterns** (`CLAUDE.md` §23) — the original geometric setups.
2. **The permissive baseline** (`trend_momentum`) — a simple, high-frequency activity signal.
3. **The wave family** (`wave_ride`, `vol_burst`, `wave_flip`) — the "surf the wave / flip
   when it turns" idea.
4. **The confluence-momentum family** (`mom_adx`, `triple_mom`) — multi-condition AND
   entries; the family that briefly looked like an edge.

> **Reminder (read [Overview §5](01-overview.md#5-honest-status--no-proven-edge)):** *none of
> these has a cross-year-robust edge.* The catalogue below documents how each works and what
> it was found to do — not a recommendation that any of them makes money.

## How a pattern's output is used

```python
@register("name")
def detect_name(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
    ...
    return PatternResult(pattern=PatternType.X, direction=Direction.LONG,
                         confidence=0.78, details={...})
```

`confidence` drives the size band (≥0.75 full, ≥0.55 half). `direction` is gated against the
trend filter **unless** the pattern is a `SELF_DIRECTING_PATTERN`. The detector keeps the
highest-confidence qualifying pattern per candle.

### Two special pattern sets (`patterns.py`)

```python
COUNTER_TREND_PATTERNS  = {"wave_flip"}                                  # trades AGAINST the EMA trend
SELF_DIRECTING_PATTERNS = COUNTER_TREND_PATTERNS | {"mom_adx", "triple_mom"}
```

`SELF_DIRECTING_PATTERNS` bypass the detector's trend-alignment gate and set their own
direction — either to fade a trend (`wave_flip`) or to ride a price streak inside a strong
trend (`mom_adx`, `triple_mom`).

---

## Family 1 — The five classic patterns

### `impulse_retracement`
- **Idea:** weak-hand profit-taking creates a dip after a strong impulse; the trend then
  resumes. Trend-aligned.
- **Trigger candle:** `body_ratio ≥ body_ratio_min` and `volume_ratio ≥ volume_ratio_min`.
- **Retracement candle (current):** body is `retracement_min`–`retracement_max` of the
  trigger body, **lower** volume than the trigger, moves *against* the trigger (a genuine
  pullback, not continuation), and does not close past the trigger's open.
- **Entry:** close of the retracement candle.
- **Confidence:** `0.40 + trigger_body_ratio×0.3 + volume_excess×0.3`.

### `wick_rejection`
- **Idea:** a long wick is a failed push that was absorbed; price reverses off a level.
- **Long:** `lower_wick ≥ wick_ratio_min × body`, close in the **top 30%** of the range, and
  the low within 1 ATR of the recent 10-candle low (support).
- **Short:** mirror — `upper_wick ≥ wick_ratio_min × body`, close in the **bottom 30%**, high
  within 1 ATR of recent resistance.
- **Entry:** close of the rejection candle. Either direction qualifies (long checked first).
- **Confidence:** `0.45 + wick_excess×0.1 + close_position×0.15`.

### `compression_breakout`
- **Idea:** a volatility squeeze releases directionally on a volume surge.
- **Setup:** `ATR(5) < ATR(20) × compression_factor`, Bollinger width declining for 3+
  candles, pre-breakout volume declining. (Needs ≥25 candles.)
- **Trigger:** close *outside* a Bollinger band with `volume_ratio > 1.5`.
- **Entry:** close of the breakout candle, in the break direction.
- **Confidence:** `0.50 + (volume_ratio − 1.5)×0.1`, capped 0.95.
- This is the **only pattern permitted during the 13–16 UTC Overlap session.**

### `momentum_continuation`
- **Idea:** an accelerating run pauses for one shallow breath, then continues.
- **Setup:** `momentum_acceleration_candles` consecutive same-direction candles, each body ≥
  the previous (acceleration), volume non-decreasing.
- **Trigger:** the current candle is a small retracement — body < 40% of the last setup
  candle, lower volume, opposite direction or doji.
- **Entry:** close of the retracement candle.
- **Confidence:** `0.50 + body_growth×0.05 + N×0.03`, capped 0.95.

### `anomaly_fade`
- **Idea:** an extreme single-candle move is a stop-hunt that snaps back to the mean.
- **Trigger (spike candle):** `volume > volume_ma20 + anomaly_volume_stddev × volume_stddev`
  **and** single-candle move `> anomaly_price_atr × ATR`.
- **Action:** do **not** chase the spike — wait for the next candle to close *against* it,
  then enter in the **opposite** direction (a fade).
- **Confidence:** `0.50 + (spike_volume_ratio − 1.0)×0.05`, capped 0.95.

---

## Family 2 — The permissive baseline

### `trend_momentum`  *(lab strategy label: `trend_mom`)*
- **Naming:** the registered *pattern* is `trend_momentum`; the lab's *strategy label* in
  `bots.json` is the shortened `trend_mom` (`"strategy": "trend_mom"`, `"patterns":
  ["trend_momentum"]`). `mom_adx` and `triple_mom` use the same name for both.
- **Why it exists:** the five classic patterns almost never trigger on real 5m data, so the
  lab stayed silent. `trend_momentum` is a deliberately simple, higher-frequency entry that
  gives the lab *activity* to measure parity and behaviour — **explicitly not an edge.**
- **Logic:** direction from the EMA relationship (so it always agrees with the trend gate);
  the latest candle must close in that direction with `body_ratio ≥ body_ratio_min`.
- **Confidence:** `0.55 + body_ratio×0.20`, **capped at 0.72** so it sizes at the conservative
  half-bucket band.
- It still passes through every downstream gate — volume confirm, min-confidence, and all six
  risk rules — so it is a real (if unsophisticated) momentum signal, not a forced fire.

---

## Family 3 — The wave family ("surf the wave, flip when wrong")

These three were built to test the user's intuition that a strategy should *ride* a move and
*flip* when it turns. The exit behaviour (wide SL / far TP / long hold vs tight scalp) lives
in the bot's param profile, not the pattern.

### `wave_ride` (trend-aligned)
Ride a trend wave on the resumption candle after a *shallow pullback*: latest candle closes
in the EMA direction with a real body, **and** at least one of the prior two candles closed
against the trend (the pullback). Avoids chasing extended blow-offs. Confidence
`0.58 + body_ratio×0.20`.

### `vol_burst` (trend-aligned)
Enter in the trend direction **only while volatility is expanding**:
`ATR(5)/ATR(20) ≥ atr_volatile_multiplier` and the latest candle closes in the EMA direction
with a real body. The "selective scalp" — active during bursts (where moves are large enough
to clear cost), silent during chop. Confidence `0.55 + expansion_excess×0.1 + body_ratio×0.1`.

### `wave_flip` (**counter-trend** — self-directing)
Fade an exhausted run: after N consecutive same-direction candles, a candle that closes
*against* the run with a real body signals exhaustion → enter the **opposite** direction.
This is the only `COUNTER_TREND_PATTERN`; it sets its own direction. Confidence
`0.55 + N×0.04 + reversal_body_ratio×0.10`.

> **Research result:** the wave family was backtested 120 days × 10 pairs and showed **no
> edge** — all ~30% win, all ≈ −$0.18–0.20/trade out-of-sample, with in-sample ≈ out-of-sample
> (i.e. *not* overfit, just no edge). Widening the SL did **not** lift the win rate, which
> refuted the "premature stop-out" theory. See [Backtesting & Research](09-backtesting-research.md).

---

## Family 4 — Confluence momentum ("ride the strong trend")

These are hand-written **multi-condition AND** entries — the low-compute alternative to ML
feature combination. Both are `SELF_DIRECTING_PATTERNS`: they take the price-streak direction
inside a strong trend *without* the RSI/EMA trend gate (exactly how they were validated).
They read only stored candle indicators, so they are cheap.

### `mom_adx`
Enter the direction of a **3-candle same-direction streak** when `ADX > adx_strong_min`
(default 25 — a genuinely strong directional move, not chop).
- Confidence `0.78 + (ADX − adx_strong_min)×0.004`, capped 0.92 → always in the full-size band.

### `triple_mom` (strictest)
The `mom_adx` confluence **plus** rising volatility: `ATR(14)` must be higher than it was ~6
candles ago (volatility expanding *into* the move, so it is more likely to clear the
round-trip cost).
- Confidence `0.80 + (ADX − adx_strong_min)×0.004`, capped 0.93.

### The rise and fall of the momentum "edge"

This family is the centre of the project's research story:

1. On a **recent-year** 4h walk-forward across 10 crypto pairs, `mom_adx` was net-positive on
   **10/10 pairs** (taker +$28 net, R/R 1.52; maker +$61), with out-of-sample *better* than
   in-sample — the first broadly-positive result ever seen. It was promoted to production and
   deployed as a paper lab.
2. It was then run against an **untouched prior-year lockbox** (the year *before* the search
   window, which no search had ever seen). On that year `mom_adx` 4h was **net-negative**
   (taker −$19, maker −$10), in-sample → out-of-sample degrading negative.
3. A cross-check sealed it: `breakout_vol` is the exact mirror — positive on the prior year,
   negative on the recent year. **Neither is positive on both independent years.**

**Conclusion:** the recent-year "win" was a data-mining / single-regime artifact. There is
**no cross-year-robust edge in any hand-written entry archetype, at any timeframe, under
taker or maker fees.** The momentum family is documented here because it is *deployed in the
lab and registered in production*, **not** because it works.

---

## Regime → permitted-pattern map (`regime.py`)

The regime gates which patterns may even be considered (intersected with the bot's
`enabled_patterns` and the session rule):

| Regime | Permitted patterns |
|---|---|
| **TRENDING** | `impulse_retracement`, `momentum_continuation`, `trend_momentum`, `wave_ride`, `vol_burst`, `mom_adx`, `triple_mom` |
| **VOLATILE** | `compression_breakout`, `anomaly_fade`, `trend_momentum`, `wave_ride`, `vol_burst`, `wave_flip`, `mom_adx`, `triple_mom` |
| **RANGING** | `wick_rejection`, `anomaly_fade`, `trend_momentum`, `wave_flip`, `mom_adx`, `triple_mom` |
| **QUIET** | *(none — all signals blocked)* |

`mom_adx` / `triple_mom` appear in every non-QUIET regime because they carry their own
ADX-strength gate.

## Pattern parameters (defaults from `params.json`)

| Param | Default | Controls |
|---|---|---|
| `body_ratio_min` | 0.40 | min body/range for impulse & momentum candles |
| `volume_ratio_min` | 1.1 | volume vs 20-MA for confirmation |
| `wick_ratio_min` | 1.5 | min wick/body for `wick_rejection` |
| `compression_factor` | 0.7 | `ATR(5)/ATR(20)` squeeze threshold |
| `retracement_min` / `_max` | 0.20 / 0.70 | retracement band for `impulse_retracement` |
| `anomaly_volume_stddev` | 1.5 | volume σ for `anomaly_fade` spike |
| `anomaly_price_atr` | 1.5 | ATR multiple for `anomaly_fade` spike |
| `momentum_acceleration_candles` | 2 | streak length for `momentum_continuation` / `wave_flip` |
| `adx_strong_min` | 25.0 | ADX floor for `mom_adx` / `triple_mom` |

See [Risk & Capital](06-risk-and-capital.md) for the exit and sizing parameters, and
`CLAUDE.md` §26 for the full params contract (note: the §26 table shows the *original spec*
defaults — `params.json` is the live source of truth and differs in several values).
