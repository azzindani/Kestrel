# 04 · Signal Engine (Layer 1)

The signal engine is the brain. It is a **pure-functional, five-stage pipeline** that takes a
window of candles and returns either a fully-formed `Signal` (ready for risk validation and
execution) or a typed `Rejection` explaining why no trade fires. It performs **no I/O** — it
is given candle history and parameters, and returns data. This is what makes it exhaustively
testable and backtestable: the same `evaluate()` runs live, in simulation, and in every
backtest.

Everything here is in `src/signal/`: `detector.py` (the pipeline), `regime.py`
(classification), `patterns.py` (the registered detectors — see [Strategies](05-strategies.md)),
`indicators.py` ([Data Pipeline §4](03-data-pipeline.md#4-the-indicator-suite-signalindicatorspy-layer-1)),
`memory.py`, and `sizing.py` ([Risk & Capital](06-risk-and-capital.md)).

## 1. Public API

```python
# src/signal/detector.py
def evaluate(
    candles: Sequence[Candle],
    params: Params,
    bot_id: str,
    session_id: str,
    env: str,
    pattern_memories: Optional[dict[str, dict | None]] = None,
    enabled_patterns: Optional[Sequence[str]] = None,
    sizing_state: Optional[SizingState] = None,
    leverage: int = 0,
) -> tuple[Signal, None] | tuple[None, Rejection]
```

It returns `(Signal, None)` on a fire, or `(None, Rejection)` when any stage declines.
`Rejection` carries `stage` (`'regime' | 'trend' | 'pattern' | 'volume' | 'risk'`) and a
`reason` string; the daemon logs it to the `signals` table with `outcome='rejected'`.

## 2. The five stages

```
candle_close
   │
   ├─ 1. classify_regime(candles, params)      → RegimeResult | Rejection(stage="regime")
   │        QUIET → reject everything
   │
   ├─ 2. _trend_filter(candles, params)        → TrendResult  | Rejection(stage="trend")
   │        (soft-bypassed for self-directing momentum patterns)
   │
   ├─ 3. _pattern_scan(...)                     → (PatternResult, conf) | Rejection(stage="pattern")
   │        registry lookup · regime+session allowlist · memory adjust · min-confidence gate
   │
   ├─ 4. _volume_confirm(candle, params)        → VolumeResult | Rejection(stage="volume")
   │
   └─ 5. build Signal (entry/TP/SL + size)      → Signal
```

Each stage returns a typed result **or** a typed rejection; control flow is never expressed
with exceptions. The first rejection short-circuits the pipeline.

### Stage 1 — Regime classification (`regime.py`)

`classify_regime(candles, params) -> RegimeResult | Rejection`. Evaluated in precedence order:

| Order | Regime | Condition |
|---|---|---|
| 1 | **QUIET** → *reject* (`reason="quiet_regime"`) | `atr14 < atr50 × atr_quiet_multiplier` **OR** `volume_ratio < 0.7` |
| 2 | **VOLATILE** | `atr14 > atr50 × atr_volatile_multiplier` **AND** `adx > 15.0` |
| 3 | **TRENDING** | `adx > adx_trend_min` **AND** `ema_spread > ema_spread_threshold` |
| 4 | **RANGING** | default fallback |

`ema_spread = |ema9 − ema21| / ema21`. If fewer than 51 candles are available, `atr14` is
used as a proxy for `atr50`. **QUIET blocks all signals** — when volatility is too low to
clear costs, the engine refuses to trade at all.

### Stage 2 — Trend filter (`detector.py`)

`_trend_filter(candles, params) -> TrendResult | Rejection`. Establishes a directional bias:

- **Long** if `ema_fast > ema_slow` **and** `rsi_low ≤ rsi ≤ rsi_long_max`.
- **Short** if `ema_fast < ema_slow` **and** `rsi_short_min ≤ rsi ≤ rsi_high`.
- Otherwise `Rejection(reason="no_trend_alignment")`.
- Then a **streak check**: if ≥4 candles are available, the prior three must all show the
  same EMA ordering, else `Rejection(reason="no_trend_streak")`.

**The self-directing bypass:** if the trend filter rejects *but* the regime's permitted
patterns include any **`SELF_DIRECTING_PATTERN`** (`wave_flip`, `mom_adx`, `triple_mom`),
the trend stage does not hard-reject — instead it sets `trend_direction = None` and lets the
pattern scan proceed. Those patterns supply their own direction. This is deliberate: the
momentum patterns were *validated without* the RSI/EMA gate (which would discard the
strongest, most "overbought" momentum entries), so they reproduce that validated behaviour.

### Stage 3 — Pattern scan (`detector.py` + `patterns.py`)

`_pattern_scan(...)` iterates the **registry** (no `if/else` dispatch):

1. Skip any pattern not in the **permitted set** — the intersection of *regime-permitted*
   (`regime.py`), *session-permitted* (OVERLAP restricts to `compression_breakout`), and the
   bot's `enabled_patterns` allowlist from `bots.json`.
2. Call the pattern function; `None` → skip.
3. **Alignment gate:** unless the pattern is in `SELF_DIRECTING_PATTERNS`, its direction must
   match `trend_direction` (and `trend_direction` must not be `None`), else skip.
4. **Memory adjustment** (`memory.py`): pull the pattern's historical record for this
   `(direction, session, regime)`; `should_suppress(...)` can veto a chronically-losing
   pattern, and `adjust_confidence(...)` blends the raw confidence with historical win rate.
5. **Session penalty:** `final_conf = raw / session_confidence_multiplier` (harder sessions
   like Asian demand more).
6. Keep the **highest-confidence** qualifying pattern.
7. **Min-confidence gate:** if `final_conf < params.min_confidence`, reject.

### Stage 4 — Volume confirmation (`detector.py`)

`_volume_confirm(candle, params, session_vol_multiplier)`: requires
`candle.volume_ratio ≥ volume_ratio_min × session_volume_multiplier`, else
`Rejection(stage="volume")`. The session multiplier raises the bar in thin sessions (Asian
×1.2) and lowers it in liquid ones (US ×0.9).

### Stage 5 — Build the signal

Computes the exit prices and position size, then assembles the immutable `Signal`:

- the four layer flags (`layer_regime`, `layer_trend`, `layer_momentum`, `layer_volume`)
  and `layers_passed`, recorded for post-hoc analysis;
- entry/TP/SL (next section);
- `size_usdt` from the sizing module ([Risk & Capital](06-risk-and-capital.md)).

## 3. TP / SL computation

`compute_exit_prices(entry, direction, atr, params, leverage)` supports two modes:

**Mode A — ATR-based (default):**
```
tp_dist = atr × tp_atr_multiplier
sl_dist = atr × sl_atr_multiplier
```
Distances *breathe* with volatility. The lab uses `tp_atr_multiplier = 2.0`,
`sl_atr_multiplier = 1.0` (a 2:1 reward:risk in ATR units).

**Mode B — fixed-percent (`tp_sl_pct_enabled = true`):**
```
tp_dist = entry × tp_pct          # e.g. 5%
sl_dist = entry × sl_pct          # e.g. 2.5%   → deterministic 2:1 R:R
```

**The liquidation safety clamp.** In fixed-percent mode with leverage, the stop percentage
is clamped so the stop *always sits inside* the liquidation distance:
```
_LIQ_SAFETY_FRACTION = 0.7
sl_pct = min(sl_pct, 0.7 / leverage)
```
This guarantees liquidation (a 100% loss) can never front-run the intended stop. At 20×,
`0.7/20 = 3.5%` is the widest a percent-stop may be.

Direction then applies the distances: long → `(entry + tp_dist, entry − sl_dist)`; short →
`(entry − tp_dist, entry + sl_dist)`.

**Price rounding** (`_round_price`) preserves ~8 significant figures *and* at least 8 decimal
places. This fixed a real bug where naive `round(price, 8)` corrupted TP/SL on sub-cent pairs
like PEPE.

## 4. Confidence → size bands

Confidence is produced by the pattern (each detector has its own formula — see
[Strategies](05-strategies.md)) and drives the **position-sizing band**:

```
confidence ≥ 0.75  → full-conviction band  (size_fraction_full × equity)
0.55 ≤ c < 0.75    → half-conviction band  (size_fraction_half × equity)
c < min_confidence → no fire
```

The momentum patterns deliberately produce confidences in the full band (≥ 0.78), the way
they were sized during validation; the permissive `trend_momentum` caps at 0.72 so it sizes
at the conservative half-bucket. The actual USDT amount and all the risk-shaping (drawdown
de-risk, loss cool-off, per-trade loss cap) happen in the sizing module —
[Risk & Capital §3](06-risk-and-capital.md#3-equity-scaled-position-sizing-signalsizingpy).

## 5. Trading sessions (UTC)

Sessions modulate the volume and confidence thresholds (`config.py` utilities):

| Session | UTC hours | Volume multiplier | Confidence multiplier | Note |
|---|---|---|---|---|
| **Asian** | 00–08 | ×1.2 | ×1.1 | stricter — thin liquidity |
| **London** | 08–16 | ×1.0 | ×1.0 | base |
| **US** | 13–21 | ×0.9 | ×1.0 | looser — deep liquidity |
| **Overlap** | 13–16 | ×0.9 | ×1.0 | **only `compression_breakout` permitted** (highest-momentum window) |

`get_trading_session(ts_ms)` returns the session for a candle; the multipliers feed Stages 3
and 4 above.
