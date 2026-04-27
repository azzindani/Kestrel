#!/usr/bin/env python3
"""
Kestrel Local Simulation — Synthetic BTC/USDT 5m Data

Generates a synthetic dataset designed to reliably trigger impulse_retracement
signals, then runs a walk-forward backtest (60%/40% split) and prints metrics.

Bug fixes applied vs prior attempts:
  1. fee_not_viable (blocked all signals):
       ATR14 ≈ 0.145% → tp_pct = 0.233% < 0.27% threshold.
       Fix: impulse_pct = 0.70% → ATR14 ≈ 0.18-0.25% → tp_pct ≈ 0.29-0.40%.

  2. quiet_regime (31% rejections):
       Normal vol = 0.85-1.15× made vol_ratio dip to 0.664 < 0.70 after
       spike-inflated MA20. Fix: normal vol = 1.1× (fixed), min vol_ratio ≈ 0.82.

  3. classify_regime race in _make():
       classify_regime(buf) was called while buf[-1] still had vol_ratio=None,
       so stored regime was always TRENDING (fallback 1.0). Fix: set indicators
       on an intermediate candle before calling classify_regime so it sees the
       real vol_ratio.

Volume steady-state math (12-candle cycle: 10×N + T + R):
  MA20 at retrace  ≈ (9×1.1 + 3.5 + 2.5 + 2.5 + 3.5 + 7×1.1) / 20 = 1.48×
  trigger.vol_ratio ≈ 3.5 / 1.41 = 2.48  ≥ 1.30  (all sessions)          ✓
  retrace.vol_ratio ≈ 2.5 / 1.48 = 1.69  ≥ 1.56  (Asian session worst)   ✓
  normal.vol_ratio  ≈ 1.1 / 1.29 = 0.85  ≥ 0.70  (QUIET threshold)        ✓
  retrace.vol (2.5) < trigger.vol (3.5)            (pattern constraint)    ✓
"""

import sys
import random
from collections import deque

sys.path.insert(0, "/home/user/Kestrel")

from src.config import (
    AppConfig,
    BucketState,
    Candle,
    Env,
    Rejection,
    compute_candle_geometry,
    load_params,
)
from src.signal.indicators import compute_all_indicators
from src.signal.regime import classify_regime
from src.backtest.runner import run_backtest, walk_forward
from src.risk.manager import validate
from src.signal.detector import evaluate

random.seed(42)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

params = load_params("/home/user/Kestrel/params.json")

cfg = AppConfig(
    env=Env.DEV,
    bot_id="sim-local",
    exchange="binance",
    api_key="x",
    api_secret="x",
    testnet=True,
    db_host="localhost",
    db_port=5432,
    db_name="kestrel",
    db_user="kestrel",
    db_password="x",
    pair="BTCUSDT",
    timeframe_entry="5m",
    timeframe_regime="15m",
    leverage=20,
    bucket_size_usdt=10.0,
    max_active_buckets=1,
    telegram_token="x",
    telegram_chat_id="x",
    log_level="DEBUG",
)

# ---------------------------------------------------------------------------
# Timestamp
# 2025-01-06 09:00 UTC — Monday London session.
# Starting mid-week ensures the first ~90 evaluated candles stay in
# London/US sessions (09:00-23:30 UTC) before cycling into Asian.
# ---------------------------------------------------------------------------

TS_START = 1_736_154_000_000  # 2025-01-06 09:00:00 UTC in ms
TS_STEP = 300_000              # 5 minutes per candle

_ts_idx = [0]


def _next_ts() -> int:
    ts = TS_START + _ts_idx[0] * TS_STEP
    _ts_idx[0] += 1
    return ts


# ---------------------------------------------------------------------------
# Candle builder — fixed volumes prevent vol_ratio edge cases
# ---------------------------------------------------------------------------

BASE_VOL = 200.0      # arbitrary unit; only ratios matter
_VOL_NORMAL  = 1.1    # ×BASE — min stored vol_ratio ≈ 0.85 > 0.70
_VOL_TRIGGER = 3.5    # ×BASE — stored vol_ratio ≈ 2.48 >> 1.30
_VOL_RETRACE = 2.5    # ×BASE — stored vol_ratio ≈ 1.69 ≥ 1.56 (Asian session)

buf: deque[Candle] = deque(maxlen=200)
candles: list[Candle] = []
price: float = 84_000.0


def _make(o: float, h: float, l: float, c: float, v: float) -> Candle:
    """Build a fully-computed Candle with indicators and regime, append to buf."""
    global price
    ts = _next_ts()
    h = max(h, o, c)
    l = min(l, o, c)
    l = max(l, 0.1)

    geom = compute_candle_geometry(o, h, l, c)

    # Step 1: add raw candle to buf so compute_all_indicators sees full history
    raw = Candle(
        bot_id="sim", ts=ts, pair="BTCUSDT", timeframe="5m",
        open=o, high=h, low=l, close=c, volume=max(v, 1.0), **geom,
    )
    buf.append(raw)

    # Step 2: compute indicators using the updated buf
    inds = compute_all_indicators(list(buf), ema_fast=params.ema_fast, ema_slow=params.ema_slow)

    # Step 3: build intermediate candle WITH indicators so classify_regime
    #         sees the real volume_ratio (not None → fallback 1.0)
    intermediate = Candle(
        bot_id="sim", ts=ts, pair="BTCUSDT", timeframe="5m",
        open=o, high=h, low=l, close=c, volume=max(v, 1.0),
        regime=None, **geom, **inds,
    )
    buf[-1] = intermediate

    # Step 4: classify regime now that buf[-1].volume_ratio is real
    reg = classify_regime(list(buf), params)
    rs = reg.regime.value if not isinstance(reg, Rejection) else None

    # Step 5: final candle with correct regime
    full = Candle(
        bot_id="sim", ts=ts, pair="BTCUSDT", timeframe="5m",
        open=o, high=h, low=l, close=c, volume=max(v, 1.0),
        regime=rs, **geom, **inds,
    )
    buf[-1] = full
    candles.append(full)
    price = c
    return full


# ---------------------------------------------------------------------------
# Candle generators
# ---------------------------------------------------------------------------


def _normal_candle(drift_pct: float = 0.08, noise_pct: float = 0.11) -> Candle:
    """Normal uptrend candle: small body, fixed baseline volume, mild drift.
    Range ≈ 0.19% × price; ATR contribution keeps fee viability marginal
    until the first trigger boosts ATR to safe levels.
    """
    global price
    o = price
    move  = o * drift_pct / 100.0
    noise = o * noise_pct / 100.0
    c = o + move + random.uniform(-noise, noise)
    body = abs(c - o)
    h = max(o, c) + random.uniform(body * 0.1, body * 0.4)
    l = min(o, c) - random.uniform(body * 0.1, body * 0.4)
    return _make(o, h, l, c, _VOL_NORMAL * BASE_VOL)


def _trigger_candle() -> Candle:
    """Strong bullish impulse: body_ratio ≈ 0.91, volume 3.5× BASE.
    impulse_pct = 0.70% → ATR boost ensures tp_pct ≈ 0.29-0.40% >> 0.27%.
    """
    global price
    o = price
    body = o * 0.70 / 100.0   # 0.70% of price
    c = o + body
    h = c + random.uniform(body * 0.03, body * 0.06)
    l = o - random.uniform(body * 0.02, body * 0.04)
    return _make(o, h, l, c, _VOL_TRIGGER * BASE_VOL)


def _retrace_candle(trigger_open: float, trigger_close: float) -> Candle:
    """Bearish retracement: 33-44% of trigger body, volume 2.5× BASE.

    Constraints enforced:
      retrace.close ≥ trigger.open + 5% × trigger_body  (LONG pattern check)
      33% ≤ retrace_body / trigger_body ≤ 44%            (30-50% window)
      retrace.volume (2.5×) < trigger.volume (3.5×)      (pattern check)
      vol_ratio ≈ 1.69 ≥ 1.56 (Asian, worst-case session threshold)
    """
    global price
    o = trigger_close
    trigger_body = trigger_close - trigger_open
    retrace_frac = random.uniform(0.33, 0.44)
    body = trigger_body * retrace_frac
    c = o - body  # bearish

    # Hard floor: close must stay above trigger.open (impulse_retracement §23)
    floor = trigger_open + trigger_body * 0.05
    if c < floor:
        c = floor
        body = o - c

    h = o + random.uniform(body * 0.03, body * 0.09)
    l = c - random.uniform(body * 0.03, body * 0.07)
    return _make(o, h, l, c, _VOL_RETRACE * BASE_VOL)


# ---------------------------------------------------------------------------
# Phase 1: Warmup — 160 candles to build EMA separation (EMA9 >> EMA21) and
# ADX > 20 before the first signal evaluation starts at index 60.
# ---------------------------------------------------------------------------

print("=" * 64)
print("  Kestrel Local Simulation — Synthetic BTC/USDT 5m")
print("=" * 64)
print()
print("Phase 1: Warmup (160 candles — establishing uptrend)...")

for _ in range(160):
    _normal_candle(drift_pct=0.09, noise_pct=0.08)

w = candles[-1]
print(f"  price={w.close:,.0f}  ema9={w.ema9:,.0f}  ema21={w.ema21:,.0f}")
print(f"  adx={w.adx:.1f}  rsi14={w.rsi14:.1f}  atr14={w.atr14:.0f}"
      f"  vol_ratio={w.volume_ratio:.3f}  regime={w.regime}")

# ---------------------------------------------------------------------------
# Phase 2: 50 trigger+retrace cycles (12 candles each = 600 more candles)
# Total: 760 candles · walk-forward split: 456 train / 304 test
# ---------------------------------------------------------------------------

print()
print("Phase 2: Signal generation (50 impulse+retrace cycles)...")

trigger_pairs: list[tuple[int, int]] = []
for _ in range(50):
    for _ in range(10):
        _normal_candle()
    trig = _trigger_candle()
    ti = len(candles) - 1
    ret = _retrace_candle(trig.open, trig.close)
    ri = len(candles) - 1
    trigger_pairs.append((ti, ri))

print(f"  Generated {len(trigger_pairs)} trigger+retrace pairs")
print(f"  Total candles: {len(candles):,}")

last = candles[-1]
print(f"  Final: price={last.close:,.0f}  atr14={last.atr14:.0f}"
      f"  vol_ratio={last.volume_ratio:.3f}  regime={last.regime}")

# ---------------------------------------------------------------------------
# Spot-check: verify first 4 pairs meet all pattern + validate() constraints
# ---------------------------------------------------------------------------

print()
print("Spot-check: first 4 trigger+retrace pairs")
print(f"  {'Pair':>4}  {'t_br':>5}  {'t_vr':>5}  {'ret_frac':>8}  "
      f"{'r_vr':>5}  {'r≥t_o':>6}  {'atr14':>6}  {'tp_pct':>7}  {'fee_ok':>6}")
for i, (ti, ri) in enumerate(trigger_pairs[:4]):
    t = candles[ti]; r = candles[ri]
    tb = t.close - t.open
    rb = abs(r.close - r.open)
    ret_frac = rb / tb if tb > 0 else 0.0
    # In evaluate(), signal uses latest.atr14 (retrace candle's stored ATR)
    atr = r.atr14 or 0.0
    entry = r.close
    tp_pct = (atr * params.tp_atr_multiplier / entry * 100.0) if entry > 0 else 0.0
    fee_ok = tp_pct > 0.18 * 1.5   # 0.27%
    above_open = r.close >= t.open
    print(f"  {i+1:>4}  {t.body_ratio:>5.3f}  {t.volume_ratio:>5.3f}  "
          f"{ret_frac:>8.3f}  {r.volume_ratio:>5.3f}  {str(above_open):>6}  "
          f"{atr:>6.0f}  {tp_pct:>6.3f}%  {'OK' if fee_ok else 'FAIL':>6}")

# ---------------------------------------------------------------------------
# Diagnostic: evaluation-window statistics
# ---------------------------------------------------------------------------

print()
print("Diagnostic: evaluation-window candle statistics (skip warmup)...")
eval_c = candles[60:]
n = len(eval_c)

vols  = [c.volume_ratio for c in eval_c if c.volume_ratio is not None]
atrs  = [c.atr14        for c in eval_c if c.atr14        is not None]
adxs  = [c.adx          for c in eval_c if c.adx          is not None]
rsis  = [c.rsi14        for c in eval_c if c.rsi14        is not None]

regime_counts = {}
for c in eval_c:
    regime_counts[c.regime or "None"] = regime_counts.get(c.regime or "None", 0) + 1

n_vol_ok   = sum(1 for v in vols if v >= 1.30)
n_vol_70   = sum(1 for v in vols if v >= 0.70)
n_ema_bull = sum(1 for c in eval_c if c.ema9 and c.ema21 and c.ema9 > c.ema21)
n_adx_ok   = sum(1 for a in adxs if a > 20)

print(f"  Evaluated: {n} candles")
print(f"  Regime: { {k: v for k, v in sorted(regime_counts.items())} }")
print(f"  vol_ratio ≥ 1.30: {n_vol_ok}/{n} ({100*n_vol_ok/n:.1f}%)")
print(f"  vol_ratio ≥ 0.70: {n_vol_70}/{n} ({100*n_vol_70/n:.1f}%)  "
      f"[QUIET if < 0.70, min={min(vols):.3f}]")
print(f"  EMA9 > EMA21:     {n_ema_bull}/{n} ({100*n_ema_bull/n:.1f}%)")
print(f"  ADX > 20:         {n_adx_ok}/{n} ({100*n_adx_ok/n:.1f}%)")
if atrs:
    atr_pcts = [a / c.close * 100 for a, c in zip(atrs, eval_c)]
    tp_pcts  = [p * params.tp_atr_multiplier for p in atr_pcts]
    n_fee_ok = sum(1 for p in tp_pcts if p > 0.27)
    print(f"  ATR14%:    min={min(atr_pcts):.3f}%  mean={sum(atr_pcts)/len(atr_pcts):.3f}%  max={max(atr_pcts):.3f}%")
    print(f"  tp_pct:    min={min(tp_pcts):.3f}%  mean={sum(tp_pcts)/len(tp_pcts):.3f}%  "
          f"  > 0.27% threshold: {n_fee_ok}/{n} ({100*n_fee_ok/n:.0f}%)")

# ---------------------------------------------------------------------------
# Rejection probe: full pipeline (evaluate + validate) on first 360 candles
# ---------------------------------------------------------------------------

print()
print("Rejection probe: full pipeline on 360 evaluation candles...")

PROBE = min(360, n)
rej_eval:  dict[str, int] = {}
rej_risk:  dict[str, int] = {}
fired = 0

for i in range(60, 60 + PROBE):
    window = list(candles[max(0, i - 119) : i + 1])
    signal, rej = evaluate(window, params, "probe", "probe-session", "dev")

    if rej is not None:
        key = f"{rej.stage}:{rej.reason[:40]}"
        rej_eval[key] = rej_eval.get(key, 0) + 1
        continue

    assert signal is not None
    state = BucketState(
        active_positions=0,
        last_ws_reconnect_ts=None,
        session_net_pnl=0.0,
        current_ts=candles[i].ts,
    )
    vr = validate(signal, state, cfg)
    if not vr.passed:
        rej_risk[vr.reason or "unknown"] = rej_risk.get(vr.reason or "unknown", 0) + 1
    else:
        fired += 1

passed_eval = sum(rej_risk.values()) + fired
print(f"  Passed evaluate():  {passed_eval}/{PROBE} ({100*passed_eval/PROBE:.1f}%)")
print(f"  Passed validate():  {fired}/{PROBE} ({100*fired/PROBE:.1f}%)")
if rej_eval:
    print("  evaluate() rejections:")
    for k, v in sorted(rej_eval.items(), key=lambda x: -x[1]):
        print(f"    {k:<55s} {v:3d} ({100*v/PROBE:.1f}%)")
if rej_risk:
    print("  validate() rejections:")
    for k, v in sorted(rej_risk.items(), key=lambda x: -x[1]):
        print(f"    {k:<30s} {v:3d} ({100*v/PROBE:.1f}%)")

# ---------------------------------------------------------------------------
# Walk-forward backtest (60% train / 40% test)
# ---------------------------------------------------------------------------

print()
print("Running walk-forward backtest (60% train / 40% test)...")

result = walk_forward(candles, params, cfg)
is_   = result["in_sample"]
os_   = result["out_sample"]
trades_out = result["trades_out"]

split = int(len(candles) * 0.60)
print()
print("=" * 64)
print("  BACKTEST RESULTS")
print("=" * 64)
print()
print(f"  IN-SAMPLE  (first 60%): {split} candles")
print(f"    trades={is_['total_trades']:3d}  win={is_['win_rate']:.1%}  "
      f"pnl={is_['total_pnl_usdt']:+.4f} USDT  "
      f"sharpe={is_['sharpe_ratio']:.3f}  "
      f"max_dd={is_['max_drawdown_usdt']:.4f} USDT")
print(f"    close_reasons: {is_['close_reasons']}")
print()
print(f"  OUT-OF-SAMPLE (last 40%): {len(candles) - split} candles")
print(f"    trades={os_['total_trades']:3d}  win={os_['win_rate']:.1%}  "
      f"pnl={os_['total_pnl_usdt']:+.4f} USDT  "
      f"sharpe={os_['sharpe_ratio']:.3f}  "
      f"max_dd={os_['max_drawdown_usdt']:.4f} USDT")
print(f"    close_reasons: {os_['close_reasons']}")

if not trades_out:
    print()
    print("  [!] No out-of-sample trades — check rejection probe above")
else:
    print()
    print(f"  Out-of-sample trade log ({len(trades_out)} trades):")
    print(f"  {'#':>3}  {'pattern':<25}  {'dir':<5}  {'reason':<13}"
          f"  {'entry':>9}  {'tp':>9}  {'sl':>9}  {'pnl_net':>10}")
    print(f"  {'-'*3}  {'-'*25}  {'-'*5}  {'-'*13}"
          f"  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*10}")
    for i, t in enumerate(trades_out, 1):
        print(f"  {i:3d}  {t['pattern']:<25}  {t['direction']:<5}  "
              f"{t['close_reason']:<13}  {t['entry_price']:9.0f}  "
              f"{t['tp_price']:9.0f}  {t['sl_price']:9.0f}  "
              f"{t['pnl_net_usdt']:+10.4f}")

# ---------------------------------------------------------------------------
# Go-live criteria (CLAUDE.md §18 / §30)
# ---------------------------------------------------------------------------

print()
print("=" * 64)
print("  GO-LIVE CRITERIA (out-of-sample — CLAUDE.md §30)")
print("=" * 64)

os_tr  = os_["total_trades"]
os_win = os_["win_rate"]
os_pf  = os_["profit_factor"]
os_pnl = os_["total_pnl_usdt"]
os_sr  = os_["sharpe_ratio"]
os_dd  = os_["max_drawdown_pct"]

cr_trades  = os_tr  >= 5
cr_winrate = os_win >= 0.55
cr_pf      = os_pf is not None and os_pf >= 1.2

print(f"  Min 5 OOS trades:       {'PASS' if cr_trades else 'FAIL':4s}  ({os_tr})")
print(f"  Win rate ≥ 55%:         {'PASS' if cr_winrate else 'FAIL':4s}  ({os_win:.1%})")
print(f"  Profit factor ≥ 1.2:    {'PASS' if cr_pf else 'FAIL':4s}  ({os_pf})")
print(f"  Net PnL:                       {os_pnl:+.4f} USDT")
print(f"  Sharpe ratio:                  {os_sr:.3f}")
print(f"  Max drawdown:                  {os_dd:.2f}%")
print()
overall = cr_trades and cr_winrate and cr_pf
print(f"  Overall: {'[GO]' if overall else '[NO-GO — more data / tuning needed for production]'}")
print("=" * 64)
