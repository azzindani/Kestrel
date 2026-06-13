#!/usr/bin/env python3
"""
Algorithm-search ACCELERATOR — rank many handwritten entry algorithms in one
offline walk-forward sweep, instead of one slow live A/B at a time.

Why this exists (CLAUDE.md §5/§30, and the no-edge history): the live lab learns
at wall-clock speed — 2 days of paper trading = 2 days of data, and it took ~3 days
to rank 3 entries. This script runs *dozens* of cheap, rule-based entry algorithms
across all pairs over months of real history in minutes, walk-forward (train 60% /
test 40%), with the SAME fee+slippage model the live runner applies — so its
leaderboard is directly comparable to the live dashboard and the grid/wave sweeps.
It is the search engine: add a new algorithm = add one function below, re-run.

Design (search vs production):
    - The entry algorithms live HERE, registered into the shared registry at import
      and judged purely on their own entry edge. To do that the script neutralises
      the secondary pipeline gates that are orthogonal to entry quality:
        * regime allowlist  -> permit-all (regime becomes a later refinement)
        * trend filter       -> every experimental algo self-directs (counter-trend set)
        * volume confirm     -> pass-through (volume is a separate dimension to add later)
      It KEEPS the legitimate economic gates: QUIET regime (no tradeable move) and the
      risk manager (R/R >= 1.2, liquidation distance, fee-viability Rule 4) — clearing
      the ~0.18% round-trip cost is the whole point, so that gate stays on.
    - Production patterns.py is untouched. Only a §30 SURVIVOR gets promoted there
      later, with regime wiring + a unit test. Dead ideas never touch production.

Entry algorithms cover the archetype space (all O(small-window), no ML, no big math):
    mean-reversion/fade : rsi_revert_* · bb_fade · donch_fade_* · spike_fade · wick_revert
    momentum/breakout   : bb_break · donch_break_* · ema_cross · streak_go_* · body_go · rsi_cross_50
    volatility-compress : compress_break · compress_fade   (the one signal with a live pulse: bb_width)

Run (one-off container, repo mounted, no DB needed):
  docker run --rm --entrypoint python --env-file .env -e EXCHANGE=gate \
    -v /root/Kestrel:/app -w /app kestrel-kestrel:latest -u \
    scripts/algo_search.py --days 45
  # smoke: scripts/algo_search.py --days 30 --pairs BTC/USDT,ETH/USDT
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import statistics
import sys
import time
from typing import Callable, Optional, Sequence

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import backtest_grid as bg  # build_candles_for / _enrich_trades / _ext_metrics / _rr / _COST_PCT
import backtest_real as bt  # robust multi-exchange fetch_ohlcv
import build_lab as lab  # PAIRS — single source of truth
from dotenv import load_dotenv

import src.signal.detector as detector
from src.backtest.runner import run_backtest
from src.config import (
    AppConfig,
    Candle,
    Direction,
    Params,
    PatternResult,
    PatternType,
    VolumeResult,
    load_params,
)
from src.signal.patterns import registry

# ---------------------------------------------------------------------------
# Entry-algorithm library — each registered into the shared registry below.
# A PatternResult carries its real identity in details["variant"]; PatternType is
# only a coarse bucket (5 values), exactly as the production wave_* patterns do.
# Confidence is fixed at 0.80 so the session-adjusted min-confidence gate never
# differentially filters one algorithm vs another (entry-edge search, not tuning).
# ---------------------------------------------------------------------------

EntryFn = Callable[[Sequence[Candle], Params], Optional[PatternResult]]
_ALGOS: list[str] = []
_CONF = 0.80


def _algo(name: str, bucket: PatternType) -> Callable[[EntryFn], EntryFn]:
    def wrap(fn: EntryFn) -> EntryFn:
        def wrapped(candles: Sequence[Candle], params: Params) -> Optional[PatternResult]:
            d = fn(candles, params)
            if d is None:
                return None
            return PatternResult(pattern=bucket, direction=d, confidence=_CONF, details={"variant": name})

        registry[name] = wrapped
        _ALGOS.append(name)
        return wrapped

    return wrap


def _dir(c: Candle) -> Optional[Direction]:
    if c.close > c.open:
        return Direction.LONG
    if c.close < c.open:
        return Direction.SHORT
    return None


def _body(c: Candle) -> float:
    return c.body_size if c.body_size is not None else abs(c.close - c.open)


def _opp(d: Optional[Direction]) -> Optional[Direction]:
    if d is Direction.LONG:
        return Direction.SHORT
    if d is Direction.SHORT:
        return Direction.LONG
    return None


# --- mean-reversion / fade ---------------------------------------------------
def _make_rsi_revert(lo: float, hi: float) -> None:
    @_algo(f"rsi_revert_{int(lo)}_{int(hi)}", PatternType.ANOMALY_FADE)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        c = C[-1]
        if c.rsi14 is None:
            return None
        if c.rsi14 < lo:
            return Direction.LONG
        if c.rsi14 > hi:
            return Direction.SHORT
        return None


for _lo, _hi in [(25.0, 75.0), (20.0, 80.0), (30.0, 70.0)]:
    _make_rsi_revert(_lo, _hi)


@_algo("bb_fade", PatternType.ANOMALY_FADE)
def _bb_fade(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    if c.bb_lower is None or c.bb_upper is None:
        return None
    if c.close < c.bb_lower:
        return Direction.LONG
    if c.close > c.bb_upper:
        return Direction.SHORT
    return None


def _make_donch_fade(n: int) -> None:
    @_algo(f"donch_fade_{n}", PatternType.ANOMALY_FADE)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        if len(C) < n + 1:
            return None
        c = C[-1]
        prior = C[-(n + 1) : -1]
        if c.high >= max(x.high for x in prior):
            return Direction.SHORT  # fade a new high
        if c.low <= min(x.low for x in prior):
            return Direction.LONG  # fade a new low
        return None


for _n in (10, 20):
    _make_donch_fade(_n)


@_algo("spike_fade", PatternType.ANOMALY_FADE)
def _spike_fade(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    if c.atr14 is None or c.atr14 <= 0.0:
        return None
    if abs(c.close - c.open) > 1.5 * c.atr14:
        return _opp(_dir(c))  # fade an outsized candle
    return None


@_algo("wick_revert", PatternType.WICK_REJECTION)
def _wick_revert(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    body = _body(c)
    if body <= 0.0:
        return None
    lw = c.lower_wick if c.lower_wick is not None else min(c.open, c.close) - c.low
    uw = c.upper_wick if c.upper_wick is not None else c.high - max(c.open, c.close)
    if lw > 1.5 * body and lw > uw:
        return Direction.LONG
    if uw > 1.5 * body and uw > lw:
        return Direction.SHORT
    return None


# --- momentum / breakout -----------------------------------------------------
@_algo("bb_break", PatternType.COMPRESSION_BREAKOUT)
def _bb_break(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    if c.bb_lower is None or c.bb_upper is None:
        return None
    if c.close > c.bb_upper:
        return Direction.LONG
    if c.close < c.bb_lower:
        return Direction.SHORT
    return None


def _make_donch_break(n: int) -> None:
    @_algo(f"donch_break_{n}", PatternType.MOMENTUM_CONTINUATION)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        if len(C) < n + 1:
            return None
        c = C[-1]
        prior = C[-(n + 1) : -1]
        if c.close >= max(x.high for x in prior):
            return Direction.LONG
        if c.close <= min(x.low for x in prior):
            return Direction.SHORT
        return None


for _n in (10, 20):
    _make_donch_break(_n)


@_algo("ema_cross", PatternType.MOMENTUM_CONTINUATION)
def _ema_cross(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 2:
        return None
    a, c = C[-2], C[-1]
    if None in (a.ema9, a.ema21, c.ema9, c.ema21):
        return None
    if a.ema9 <= a.ema21 and c.ema9 > c.ema21:
        return Direction.LONG
    if a.ema9 >= a.ema21 and c.ema9 < c.ema21:
        return Direction.SHORT
    return None


def _make_streak_go(n: int) -> None:
    @_algo(f"streak_go_{n}", PatternType.MOMENTUM_CONTINUATION)
    def _fn(C: Sequence[Candle], p: Params) -> Optional[Direction]:
        if len(C) < n:
            return None
        dirs = [_dir(x) for x in C[-n:]]
        if None in dirs or len(set(dirs)) != 1:
            return None
        return dirs[0]


for _n in (2, 3):
    _make_streak_go(_n)


@_algo("body_go", PatternType.MOMENTUM_CONTINUATION)
def _body_go(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    br = c.body_ratio if c.body_ratio is not None else 0.0
    if br > 0.6:
        return _dir(c)
    return None


@_algo("rsi_cross_50", PatternType.MOMENTUM_CONTINUATION)
def _rsi_cross_50(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 2:
        return None
    a, c = C[-2], C[-1]
    if a.rsi14 is None or c.rsi14 is None:
        return None
    if a.rsi14 <= 50.0 < c.rsi14:
        return Direction.LONG
    if a.rsi14 >= 50.0 > c.rsi14:
        return Direction.SHORT
    return None


# --- volatility compression (the one signal with a live pulse: bb_width) ------
def _compressed(C: Sequence[Candle]) -> bool:
    """bb_width in the bottom of its recent range (cheap squeeze proxy)."""
    if len(C) < 21:
        return False
    widths = [x.bb_width for x in C[-21:-1] if x.bb_width is not None]
    cur = C[-1].bb_width
    if cur is None or len(widths) < 10:
        return False
    return cur < statistics.median(widths) * 0.7


@_algo("compress_break", PatternType.COMPRESSION_BREAKOUT)
def _compress_break(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if not _compressed(C):
        return None
    return _dir(C[-1])  # enter the breakout direction


@_algo("compress_fade", PatternType.ANOMALY_FADE)
def _compress_fade(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if not _compressed(C):
        return None
    return _opp(_dir(C[-1]))  # fade the first move out of the squeeze


# --- confluence (multi-condition AND) ----------------------------------------
# The only live-positive thread was 4h momentum (sub-§30, SOL-concentrated). These
# require 2-3 independent conditions to AGREE — the handwritten alternative to ML
# feature-combination — to filter momentum down to higher-quality setups. All use
# only stored Candle indicators (cheap). Volume is read directly here (the pipeline
# volume gate is bypassed), so these explicitly test whether volume helps.
def _streak_dir(C: Sequence[Candle], n: int) -> Optional[Direction]:
    if len(C) < n:
        return None
    ds = [_dir(x) for x in C[-n:]]
    if None in ds or len(set(ds)) != 1:
        return None
    return ds[0]


@_algo("mom_adx", PatternType.MOMENTUM_CONTINUATION)
def _mom_adx(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    if c.adx is None or c.adx <= 25.0:  # 3-streak only inside a strong trend
        return None
    return _streak_dir(C, 3)


@_algo("mom_align", PatternType.MOMENTUM_CONTINUATION)
def _mom_align(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    c = C[-1]
    if None in (c.ema9, c.ema21, c.adx) or c.adx <= 22.0:
        return None
    br = c.body_ratio if c.body_ratio is not None else 0.0
    d = _dir(c)
    if d is None or br < 0.5:
        return None
    trend = Direction.LONG if c.ema9 > c.ema21 else (Direction.SHORT if c.ema9 < c.ema21 else None)
    return d if d is trend else None  # conviction candle aligned with EMA trend + ADX


@_algo("breakout_vol", PatternType.MOMENTUM_CONTINUATION)
def _breakout_vol(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 21:
        return None
    c = C[-1]
    vr = c.volume_ratio if c.volume_ratio is not None else 0.0
    if vr < 1.5:  # breakout must carry real participation
        return None
    prior = C[-21:-1]
    if c.close >= max(x.high for x in prior):
        return Direction.LONG
    if c.close <= min(x.low for x in prior):
        return Direction.SHORT
    return None


@_algo("pullback_trend", PatternType.MOMENTUM_CONTINUATION)
def _pullback_trend(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 3:
        return None
    a, c = C[-2], C[-1]
    if None in (c.ema9, c.ema21, c.rsi14):
        return None
    if c.ema9 > c.ema21 and _dir(a) is Direction.SHORT and _dir(c) is Direction.LONG and 40.0 <= c.rsi14 <= 60.0:
        return Direction.LONG  # buy the pullback in an uptrend, RSI not extended
    if c.ema9 < c.ema21 and _dir(a) is Direction.LONG and _dir(c) is Direction.SHORT and 40.0 <= c.rsi14 <= 60.0:
        return Direction.SHORT
    return None


@_algo("mom_volexp", PatternType.MOMENTUM_CONTINUATION)
def _mom_volexp(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 7:
        return None
    c, past = C[-1], C[-7]
    if c.atr14 is None or past.atr14 is None or past.atr14 <= 0.0 or c.atr14 <= past.atr14:
        return None  # ATR rising = volatility expanding
    return _streak_dir(C, 3)


@_algo("triple_mom", PatternType.MOMENTUM_CONTINUATION)
def _triple_mom(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if len(C) < 7:
        return None
    c, past = C[-1], C[-7]
    if c.adx is None or c.adx <= 25.0:
        return None
    if c.atr14 is None or past.atr14 is None or c.atr14 <= past.atr14:
        return None
    return _streak_dir(C, 3)  # strictest: streak + strong ADX + expanding ATR


@_algo("compress_vol_break", PatternType.COMPRESSION_BREAKOUT)
def _compress_vol_break(C: Sequence[Candle], p: Params) -> Optional[Direction]:
    if not _compressed(C):
        return None
    c = C[-1]
    vr = c.volume_ratio if c.volume_ratio is not None else 0.0
    if vr < 1.3:
        return None
    return _dir(c)  # squeeze breakout confirmed by volume


# ---------------------------------------------------------------------------
# Neutralise secondary gates so each algo is judged on its own entry edge.
# (Module-global rebinds — detector resolves these names at call time.)
# ---------------------------------------------------------------------------
def _install_search_gates(regime_filter: Optional[str] = None) -> None:
    if regime_filter:
        want = regime_filter.lower()
        detector.regime_permits_pattern = lambda regime, p: regime.name.lower() == want  # one regime only
    else:
        detector.regime_permits_pattern = lambda regime, p: True  # permit-all
    detector.SELF_DIRECTING_PATTERNS = frozenset(_ALGOS)  # every algo self-directs

    def _pass_volume(candle: Candle, params: Params, mult: float) -> VolumeResult:
        return VolumeResult(
            volume_ratio=candle.volume_ratio if candle.volume_ratio is not None else 1.0,
            volume_ma20=candle.volume_ma20 if candle.volume_ma20 is not None else 0.0,
        )

    detector._volume_confirm = _pass_volume


# Exit profiles (ATR-mode). Entry style interacts with exit, so bracket tight/wide.
# Both keep planned R/R = tp/sl >= 1.2 (risk Rule 3).
EXITS = {
    "tight": {"tp_atr_multiplier": 1.4, "sl_atr_multiplier": 1.0, "max_hold_candles": 4},
    "wide": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.5, "max_hold_candles": 8},
}

# Forex / metals universe for --forex mode (yfinance symbols). Majors + crosses +
# gold/oil — the instruments BingX's TradFi side covers but ccxt doesn't expose, so
# we research them on free Yahoo data before building any live forex integration.
FOREX_PAIRS = [
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "AUDUSD=X",
    "USDCAD=X",
    "USDCHF=X",
    "NZDUSD=X",
    "EURJPY=X",
    "GBPJPY=X",
    "GC=F",
]


def _fetch_forex(symbol: str, tf: str, days: int) -> tuple[str, list]:
    """Fetch forex/metal OHLCV via yfinance → rows [ts_ms, o, h, l, c, v].

    Yahoo caps intraday history (5m≈60d, 1h≈730d) and has no native 4h, so we
    fetch the largest available base interval and resample up. yfinance is a
    research-only dep (NOT in the image) — imported lazily, pip-installed in the
    one-off container that runs --forex.
    """
    import pandas as pd
    import yfinance as yf

    if tf == "5m":
        base, period = "5m", f"{min(days, 59)}d"
    elif tf in ("1h", "4h"):
        base, period = "1h", f"{min(days, 729)}d"
    else:  # 1d or higher
        base, period = "1d", f"{days}d"

    df = yf.download(symbol, period=period, interval=base, progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        raise RuntimeError("yfinance returned no rows")
    if getattr(df.columns, "nlevels", 1) > 1:  # single-ticker MultiIndex → flatten
        df.columns = df.columns.get_level_values(0)

    if tf == "4h":  # resample 1h → 4h
        df = pd.DataFrame(
            {
                "Open": df["Open"].resample("4h").first(),
                "High": df["High"].resample("4h").max(),
                "Low": df["Low"].resample("4h").min(),
                "Close": df["Close"].resample("4h").last(),
                "Volume": df["Volume"].resample("4h").sum(),
            }
        ).dropna()

    rows = []
    for ts, r in df.iterrows():
        rows.append(
            [
                int(ts.timestamp() * 1000),
                float(r["Open"]),
                float(r["High"]),
                float(r["Low"]),
                float(r["Close"]),
                float(r["Volume"] or 0.0),
            ]
        )
    return ("yfinance", rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--pairs", default=None, help="comma list to override PAIRS")
    ap.add_argument("--algos", default=None, help="comma list to restrict the algo set")
    ap.add_argument("--exits", default="tight,wide", help="comma list of exit profiles")
    ap.add_argument("--regime", default=None, help="restrict firing to one regime: ranging|trending|volatile")
    ap.add_argument("--forex", action="store_true", help="search forex/metals (yfinance) instead of crypto")
    args = ap.parse_args()

    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    base = load_params("params.json")
    base = dataclasses.replace(base, volume_ratio_min=1.1)  # most-permissive (volume gate is bypassed anyway)
    _install_search_gates(args.regime)

    default_pairs = FOREX_PAIRS if args.forex else lab.PAIRS
    pairs = [p.strip() for p in args.pairs.split(",")] if args.pairs else default_pairs
    fetch = _fetch_forex if args.forex else bt.fetch_ohlcv
    algos = [a.strip() for a in args.algos.split(",")] if args.algos else list(_ALGOS)
    exits = [e.strip() for e in args.exits.split(",") if e.strip() in EXITS]
    tag = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    combos = [(a, e) for a in algos for e in exits]

    print(
        f"=== Kestrel ALGORITHM SEARCH ({args.tf}, {args.days}d, {cfg.leverage}x, regime={args.regime or 'all'}) ===",
        flush=True,
    )
    print(
        f"algos={len(algos)} exits={len(exits)} pairs={len(pairs)} "
        f"combos={len(combos)} backtests={len(combos) * len(pairs)} "
        f"cost/trade≈{bg._COST_PCT:.2f}%",
        flush=True,
    )

    # pooled[(algo,exit)] = {"oos": [...], "ins": [...], "n_oos": int}
    pooled: dict[tuple[str, str], dict] = {c: {"oos": [], "ins": [], "n_oos": 0} for c in combos}
    skipped: list[str] = []

    for pi, pair in enumerate(pairs, 1):
        try:
            ex_used, raw = fetch(pair, args.tf, args.days)
        except Exception as exc:  # noqa: BLE001 — survey loop: report and continue
            print(
                f"[{pi}/{len(pairs)}] {pair}: FETCH FAILED ({type(exc).__name__}: {str(exc)[:70]}) — skipped",
                flush=True,
            )
            skipped.append(pair)
            continue
        candles = bg.build_candles_for(pair, args.tf, cfg, base, raw)
        ts_index = {int(c.ts): i for i, c in enumerate(candles)}
        split_ts = candles[int(len(candles) * 0.60)].ts
        n_oos = sum(1 for c in candles if c.ts >= split_ts)
        print(f"[{pi}/{len(pairs)}] {pair}: src={ex_used} candles={len(candles)} (OOS={n_oos})", flush=True)

        for algo, exit_name in combos:
            p = dataclasses.replace(base, **EXITS[exit_name])
            trades = run_backtest(candles, p, cfg, bot_id=f"as-{pair}-{algo}-{exit_name}", enabled_patterns=[algo])[
                "trades"
            ]
            bg._enrich_trades(trades, ts_index, candles)
            d = pooled[(algo, exit_name)]
            d["oos"].extend(t for t in trades if t["entry_ts"] >= split_ts)
            d["ins"].extend(t for t in trades if t["entry_ts"] < split_ts)
            d["n_oos"] += n_oos

    # Build leaderboard
    rows = []
    for (algo, exit_name), d in pooled.items():
        mo = bg._ext_metrics(d["oos"], d["n_oos"])
        mi = bg._ext_metrics(d["ins"], d["n_oos"])
        rows.append(
            {
                "algo": algo,
                "exit": exit_name,
                "oos": mo,
                "ins": mi,
                "degrade": round(mo["avg_pnl_usdt"] - mi["avg_pnl_usdt"], 5),
            }
        )
    rows.sort(key=lambda r: (r["oos"]["total_trades"] == 0, -r["oos"]["avg_pnl_usdt"]))

    # §30 OOS bar: win>55% · R/R>=1.2 · net>0 · n>=30
    def passes(m: dict) -> bool:
        return m["total_pnl_usdt"] > 0 and m["win_rate"] > 0.55 and bg._rr(m) >= 1.2 and m["total_trades"] >= 30

    survivors = [r for r in rows if passes(r["oos"])]

    print("\n=== ALGO LEADERBOARD (out-of-sample, top 25) ===", flush=True)
    print(
        f"  {'algo':16s} {'exit':5s} {'n':>5s} {'win%':>6s} {'avg$':>9s} {'net$':>9s} "
        f"{'R/R':>5s} {'expR':>6s} {'tp%':>5s} {'to%':>5s} {'clr%':>5s} {'IS→OOS':>8s}",
        flush=True,
    )
    for r in rows[:25]:
        m = r["oos"]
        print(
            f"  {r['algo']:16s} {r['exit']:5s} {m['total_trades']:5d} {m['win_rate'] * 100:6.1f} "
            f"{m['avg_pnl_usdt']:9.4f} {m['total_pnl_usdt']:9.3f} {bg._rr(m):5.2f} "
            f"{m['expectancy_R']:6.2f} {m['tp_rate'] * 100:5.0f} {m['timeout_rate'] * 100:5.0f} "
            f"{m['pct_clearing_cost'] * 100:5.0f} {r['degrade']:+8.4f}",
            flush=True,
        )

    print("\n=== VERDICT (§30 OOS: win>55% · R/R≥1.2 · net>0 · n≥30) ===", flush=True)
    print(f"  algorithms clearing the bar: {len(survivors)} / {len(rows)}", flush=True)
    if survivors:
        for r in survivors:
            print(
                f"  SURVIVOR: {r['algo']} / {r['exit']} — win {r['oos']['win_rate'] * 100:.1f}% "
                f"net ${r['oos']['total_pnl_usdt']:.2f} R/R {bg._rr(r['oos']):.2f} "
                f"n={r['oos']['total_trades']}",
                flush=True,
            )
    elif rows:
        b = rows[0]
        print(
            f"  best: {b['algo']}/{b['exit']} avg=${b['oos']['avg_pnl_usdt']:.4f}/trade "
            f"win={b['oos']['win_rate'] * 100:.1f}% n={b['oos']['total_trades']} "
            f"— no edge; cost floor dominates (consistent with the no-edge history)",
            flush=True,
        )
    if skipped:
        print(f"  NOTE: {len(skipped)} pair(s) skipped (fetch failed): {', '.join(skipped)}", flush=True)

    _write_reports(tag, args, cfg, pairs, rows, survivors, skipped)


def _write_reports(tag, args, cfg, pairs, rows, survivors, skipped) -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, f"algo_search_{tag}")

    lines = [
        f"# Kestrel algorithm search — {tag} UTC",
        "",
        f"- **window:** {args.days}d {args.tf} · **leverage:** {cfg.leverage}x · cost/trade ≈ {bg._COST_PCT:.2f}%",
        f"- **pairs:** {', '.join(pairs)}" + (f" · **skipped:** {', '.join(skipped)}" if skipped else ""),
        f"- **algorithms × exits:** {len({r['algo'] for r in rows})} × "
        f"{len({r['exit'] for r in rows})} = {len(rows)} pooled cells",
        f"- **§30 OOS survivors:** {len(survivors)} / {len(rows)}",
        "",
        "| algo | exit | n | win% | avg $ | net $ | R/R | expR | tp% | to% | clr% | IS→OOS |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        m = r["oos"]
        lines.append(
            f"| {r['algo']} | {r['exit']} | {m['total_trades']} | {m['win_rate'] * 100:.1f} | "
            f"{m['avg_pnl_usdt']:.4f} | {m['total_pnl_usdt']:.3f} | {bg._rr(m):.2f} | "
            f"{m['expectancy_R']:.2f} | {m['tp_rate'] * 100:.0f} | {m['timeout_rate'] * 100:.0f} | "
            f"{m['pct_clearing_cost'] * 100:.0f} | {r['degrade']:+.4f} |"
        )
    lines += ["", "## Verdict", ""]
    if survivors:
        lines.append(
            f"**{len(survivors)} algorithm(s) clear the §30 OOS bar** — promote to "
            "`src/signal/patterns.py` (with regime wiring + unit test) before deploying:"
        )
        for r in survivors:
            lines.append(
                f"- `{r['algo']}` / {r['exit']}: win {r['oos']['win_rate'] * 100:.1f}%, "
                f"net ${r['oos']['total_pnl_usdt']:.2f}, R/R {bg._rr(r['oos']):.2f}, "
                f"n={r['oos']['total_trades']}"
            )
    elif rows:
        b = rows[0]
        lines.append(
            f"**No algorithm clears the §30 OOS bar.** Best was `{b['algo']}`/{b['exit']} at "
            f"avg ${b['oos']['avg_pnl_usdt']:.4f}/trade, win {b['oos']['win_rate'] * 100:.1f}%, "
            f"n={b['oos']['total_trades']}. Consistent with the documented no-edge result."
        )
    with open(f"{stem}.md", "w") as f:
        f.write("\n".join(lines) + "\n")

    survivor_specs = [
        {
            "algo": r["algo"],
            "exit": r["exit"],
            "params": EXITS[r["exit"]],
            "oos": {k: r["oos"][k] for k in ("total_trades", "win_rate", "total_pnl_usdt")},
        }
        for r in survivors
    ]
    with open(f"{stem}_survivors.json", "w") as f:
        json.dump(survivor_specs, f, indent=2)
    print(f"\nwrote {os.path.normpath(stem)}.md  (+ _survivors.json: {len(survivor_specs)})", flush=True)


if __name__ == "__main__":
    main()
