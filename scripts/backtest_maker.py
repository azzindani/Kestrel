"""scripts/backtest_maker.py — fill-realistic maker execution backtest.

The live sim (execution/simulation.py, MAKER_EXECUTION=true) already prices maker
fees, but fills every post-only limit INSTANTLY at the exact price with zero miss.
That is the optimistic bound: a real post-only limit (a) does not always fill and
(b) is adversely selected — for a long you only get filled when price dips TO your
resting bid, which preferentially fills the entries that then continue against you,
while the strong continuations run away and never fill.

This script measures how much of the "maker lift" is real once fills are honest. It
generates the EXACT live signal stream (detector.evaluate + risk.validate, the same
calls the runner makes, under taker occupancy) and runs each signal through three
execution models with IDENTICAL exit logic (the deployed trailing exit profile), so
the only thing that varies is the entry/fill:

    taker        market fill on the signal candle: +slippage, taker fee   (live-safe model)
    maker_naive  instant fill at the signal price: no slip, maker fee      (what the sim does today)
    maker_real   post-only limit `offset_bps` better than the signal price;
                 fills ONLY if price trades to it within `fill_window` candles,
                 else the trade is MISSED. Filled trades pay the maker fee.

Under the deployed TRAILING profile there is no fixed take-profit, so every exit is a
trailing-stop / SL / timeout = taker out; maker therefore helps on the ENTRY side only
(that is modelled faithfully — TP exits get maker treatment only when not trailing).

Run recent + lockbox:
    PYTHONPATH=. python scripts/backtest_maker.py --days 45
    PYTHONPATH=. python scripts/backtest_maker.py --days 365 --offset-days 365   # lockbox

Reads only; never touches the DB, the live fleet, or any frozen file.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import time
from typing import Any, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)  # sibling research scripts (backtest_grid / backtest_real / build_*)
sys.path.insert(0, os.path.dirname(_HERE))  # repo root for `src`

import backtest_grid as bg  # noqa: E402  build_candles_for
import backtest_real as bt  # noqa: E402  robust multi-exchange fetch_ohlcv
import build_lab as lab  # noqa: E402  PAIRS (single source of truth)
import build_momentum_lab as mlab  # noqa: E402  deployed 5m exit profile (_EXIT)
from dotenv import load_dotenv  # noqa: E402

import src.signal.detector as detector  # noqa: E402
from src.config import (  # noqa: E402
    AppConfig,
    BucketState,
    Candle,
    Direction,
    SizingState,
    compute_liquidation_price,
    load_params,
)
from src.risk.manager import validate  # noqa: E402

# Cost constants — identical to src/backtest/runner.py and src/execution/simulation.py.
_TAKER_FEE = 0.04 / 100.0
_MAKER_FEE = 0.02 / 100.0
_SLIP = 0.05 / 100.0
_WARMUP = 60

# Live 5m fleet strategies (the activity drivers — build_momentum_lab _STRATEGIES).
_DEFAULT_STRATS = ["trend_momentum", "mom_adx", "triple_mom"]

# Execution-model definitions. `real_fill` drives the post-only fill/miss + adverse
# selection; `tp_is_maker` mirrors simulation.py (maker treatment for TP exits only,
# which under trailing never fires — so maker = entry-side benefit only).
_MODELS: dict[str, dict[str, Any]] = {
    "taker": {"entry_slip": _SLIP, "entry_fee": _TAKER_FEE, "tp_is_maker": False, "real_fill": False},
    "maker_naive": {"entry_slip": 0.0, "entry_fee": _MAKER_FEE, "tp_is_maker": True, "real_fill": False},
    "maker_real": {"entry_slip": 0.0, "entry_fee": _MAKER_FEE, "tp_is_maker": True, "real_fill": True},
}


# ---------------------------------------------------------------------------
# Exit walk — ported verbatim in spirit from src/backtest/runner._check_exit /
# _advance_trail_bt so the three models share one faithful exit simulator and the
# delta between them is purely the entry/fill model.
# ---------------------------------------------------------------------------


def _advance_trail(pos: dict[str, Any], high: float, low: float) -> None:
    direction = pos["direction"]
    entry = pos["entry_price"]
    activation = pos["trail_activation_dist"]
    distance = pos["trail_distance_dist"]
    if direction is Direction.LONG:
        peak = max(pos["peak_price"], high)
        pos["peak_price"] = peak
        if peak - entry >= activation:
            candidate = max(peak - distance, pos["sl_price"])
            cur = pos["trail_stop"]
            pos["trail_stop"] = candidate if cur is None else max(cur, candidate)
    else:
        trough = min(pos["peak_price"], low)
        pos["peak_price"] = trough
        if entry - trough >= activation:
            candidate = min(trough + distance, pos["sl_price"])
            cur = pos["trail_stop"]
            pos["trail_stop"] = candidate if cur is None else min(cur, candidate)


def _check_exit(pos: dict[str, Any], candle: Candle) -> Optional[str]:
    direction = pos["direction"]
    high, low = candle.high, candle.low
    trailing = pos["trailing_enabled"]
    if direction is Direction.LONG:
        if trailing:
            ts = pos["trail_stop"]
            if ts is not None and low <= ts:
                return "trailing_stop"
        elif high >= pos["tp_price"]:
            return "take_profit"
        if low <= pos["sl_price"]:
            return "stop_loss"
        if low <= pos["liquidation_price"]:
            return "liquidated"
    else:
        if trailing:
            ts = pos["trail_stop"]
            if ts is not None and high >= ts:
                return "trailing_stop"
        elif low <= pos["tp_price"]:
            return "take_profit"
        if high >= pos["sl_price"]:
            return "stop_loss"
        if high >= pos["liquidation_price"]:
            return "liquidated"
    if trailing:
        _advance_trail(pos, high, low)
    return None


def _exit_price(reason: str, pos: dict[str, Any], candle: Candle, tp_is_maker: bool) -> tuple[float, float]:
    """Return (fill_exit_price, exit_fee_rate). TP gets maker treatment only when the
    model allows AND the exit really is a take_profit; every other exit markets out
    (taker fee + slippage), exactly as execution/simulation.py does."""
    direction = pos["direction"]
    if reason == "take_profit":
        raw = pos["tp_price"]
    elif reason == "trailing_stop":
        raw = pos["trail_stop"]
    elif reason == "stop_loss":
        raw = pos["sl_price"]
    elif reason == "liquidated":
        raw = pos["liquidation_price"]
    else:  # timeout
        raw = candle.close

    if tp_is_maker and reason == "take_profit":
        return raw, _MAKER_FEE  # resting limit at the target — no slippage
    slip = _SLIP
    fill = raw * (1.0 - slip) if direction is Direction.LONG else raw * (1.0 + slip)
    return fill, _TAKER_FEE


def _sim_trade(
    model: str,
    candles: Sequence[Candle],
    sig_i: int,
    p_signal: float,
    tp: float,
    sl: float,
    direction: Direction,
    size: float,
    leverage: int,
    max_hold: int,
    trailing: bool,
    trail_act_r: float,
    trail_dist_r: float,
    offset_bps: float,
    fill_window: int,
) -> Optional[dict[str, Any]]:
    """Simulate one signal under one execution model. Returns the trade dict, or a
    dict with filled=False for a maker_real miss, or None if the series ends first."""
    m = _MODELS[model]
    n = len(candles)

    # --- entry / fill ---
    if m["real_fill"]:
        off = offset_bps / 10_000.0
        limit = p_signal * (1.0 - off) if direction is Direction.LONG else p_signal * (1.0 + off)
        entry_i: Optional[int] = None
        for j in range(sig_i + 1, min(sig_i + 1 + fill_window, n)):
            cj = candles[j]
            if direction is Direction.LONG and cj.low <= limit:
                entry_i = j
                break
            if direction is Direction.SHORT and cj.high >= limit:
                entry_i = j
                break
        if entry_i is None:
            return {"filled": False}  # post-only limit never came back — missed trade
        entry_price = limit
    else:
        entry_i = sig_i
        slip = m["entry_slip"]
        entry_price = p_signal * (1.0 + slip) if direction is Direction.LONG else p_signal * (1.0 - slip)

    notional = size * leverage
    fee_entry = notional * m["entry_fee"]
    liq = compute_liquidation_price(entry_price, direction, leverage)
    r_unit = abs(entry_price - sl)

    pos = {
        "direction": direction,
        "entry_price": entry_price,
        "tp_price": tp,
        "sl_price": sl,
        "liquidation_price": liq,
        "trailing_enabled": trailing,
        "peak_price": entry_price,
        "trail_stop": None,
        "trail_activation_dist": trail_act_r * r_unit,
        "trail_distance_dist": trail_dist_r * r_unit,
    }

    # --- exit walk (monitor from the candle AFTER fill; no intra-candle look-ahead) ---
    reason: Optional[str] = None
    exit_i = entry_i
    held = 0
    for k in range(entry_i + 1, n):
        held += 1
        exit_i = k
        r = _check_exit(pos, candles[k])
        if r is not None:
            reason = r
            break
        if held >= max_hold:
            reason = "timeout"
            break
    if reason is None:  # ran off the end of the series
        return None

    fill_exit, fee_rate = _exit_price(reason, pos, candles[exit_i], m["tp_is_maker"])
    fee_exit = notional * fee_rate
    if direction is Direction.LONG:
        pnl_gross = (fill_exit - entry_price) / entry_price * notional
    else:
        pnl_gross = (entry_price - fill_exit) / entry_price * notional
    pnl_net = pnl_gross - fee_entry - fee_exit
    pnl_bps = pnl_net / notional * 10_000.0  # size-independent

    return {
        "filled": True,
        "pnl_net": pnl_net,
        "pnl_bps": pnl_bps,
        "win": pnl_net > 0.0,
        "reason": reason,
        "exit_i": exit_i,
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _agg(trades: list[dict[str, Any]], n_signals: int) -> dict[str, Any]:
    filled = [t for t in trades if t.get("filled")]
    n = len(filled)
    if n == 0:
        return {"n": 0, "fill_rate": 0.0, "win": 0.0, "avg_bps": 0.0, "tot_bps": 0.0, "avg_usdt": 0.0}
    wins = sum(1 for t in filled if t["win"])
    tot_bps = sum(t["pnl_bps"] for t in filled)
    tot_usdt = sum(t["pnl_net"] for t in filled)
    return {
        "n": n,
        "fill_rate": n / n_signals if n_signals else 0.0,
        "win": wins / n,
        "avg_bps": tot_bps / n,
        "tot_bps": tot_bps,
        "avg_usdt": tot_usdt / n,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--pairs", default=None, help="comma list (SLASH fmt BTC/USDT); default build_lab.PAIRS")
    ap.add_argument("--strats", default=",".join(_DEFAULT_STRATS), help="comma list of deployed strategies")
    ap.add_argument(
        "--offset-bps",
        type=float,
        default=2.0,
        dest="offset_bps",
        help="post-only limit improvement vs signal price (bps better); join-the-bid ≈ half-spread",
    )
    ap.add_argument(
        "--fill-window",
        type=int,
        default=2,
        dest="fill_window",
        help="candles a post-only limit rests before it is cancelled (miss)",
    )
    ap.add_argument(
        "--offset-days",
        type=int,
        default=0,
        dest="offset_days",
        help="shift window END back N days for a LOCKBOX test (e.g. --days 365 --offset-days 365)",
    )
    args = ap.parse_args()

    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    base = load_params("params.json")
    base = dataclasses.replace(base, volume_ratio_min=1.1)
    base = dataclasses.replace(base, **mlab._EXIT)  # deployed 5m trailing exit profile

    pairs = [p.strip() for p in args.pairs.split(",")] if args.pairs else list(lab.PAIRS)
    strats = [s.strip() for s in args.strats.split(",")]
    max_hold = base.max_hold_candles
    trailing = base.trailing_enabled
    trail_act_r = base.trail_activation_r
    trail_dist_r = base.trail_distance_r

    window_tag = f"LOCKBOX offset={args.offset_days}d" if args.offset_days else "RECENT"
    print(
        f"=== Kestrel MAKER-FILL backtest ({args.tf}, {args.days}d, {cfg.leverage}x, {window_tag}) ===\n"
        f"strats={strats} pairs={len(pairs)} offset={args.offset_bps}bps fill_window={args.fill_window} "
        f"exit=trailing(tp{base.tp_atr_multiplier}/sl{base.sl_atr_multiplier}/hold{max_hold})",
        flush=True,
    )

    # pooled[strat][model] = list of OOS trade dicts ; signals[strat] = OOS signal count
    pooled: dict[str, dict[str, list]] = {s: {mdl: [] for mdl in _MODELS} for s in strats}
    sig_count: dict[str, int] = {s: 0 for s in strats}
    sizing = SizingState(equity_usdt=cfg.bucket_size_usdt, peak_equity_usdt=cfg.bucket_size_usdt, consec_losses=0)

    for pi, pair in enumerate(pairs, 1):
        try:
            ex_used, raw = bt.fetch_ohlcv(pair, args.tf, args.days, offset_days=args.offset_days)
        except Exception as exc:  # noqa: BLE001 — survey loop: report and continue
            print(
                f"[{pi}/{len(pairs)}] {pair}: FETCH FAILED ({type(exc).__name__}: {str(exc)[:60]}) — skipped",
                flush=True,
            )
            continue
        candles = bg.build_candles_for(pair, args.tf, cfg, base, raw)
        if len(candles) < _WARMUP + 50:
            print(f"[{pi}/{len(pairs)}] {pair}: too few candles ({len(candles)}) — skipped", flush=True)
            continue
        split_ts = candles[int(len(candles) * 0.60)].ts
        print(f"[{pi}/{len(pairs)}] {pair}: src={ex_used} candles={len(candles)}", flush=True)

        for strat in strats:
            i = _WARMUP
            while i < len(candles):
                win_c = candles[max(0, i - 119) : i + 1]
                state = BucketState(
                    active_positions=0, last_ws_reconnect_ts=None, session_net_pnl=0.0, current_ts=candles[i].ts
                )
                sig, rej = detector.evaluate(
                    win_c,
                    base,
                    "bt-maker",
                    "sess",
                    cfg.env.value,
                    enabled_patterns=[strat],
                    sizing_state=sizing,
                    leverage=cfg.leverage,
                )
                if rej is not None or sig is None or not validate(sig, state, cfg).passed:
                    i += 1
                    continue

                is_oos = candles[i].ts >= split_ts
                taker = _sim_trade(
                    "taker",
                    candles,
                    i,
                    sig.entry_price,
                    sig.tp_price,
                    sig.sl_price,
                    sig.direction,
                    sig.size_usdt,
                    cfg.leverage,
                    max_hold,
                    trailing,
                    trail_act_r,
                    trail_dist_r,
                    args.offset_bps,
                    args.fill_window,
                )
                if taker is None:  # signal too close to series end — stop this strat
                    break
                if is_oos:
                    sig_count[strat] += 1
                    pooled[strat]["taker"].append(taker)
                    for mdl in ("maker_naive", "maker_real"):
                        t = _sim_trade(
                            mdl,
                            candles,
                            i,
                            sig.entry_price,
                            sig.tp_price,
                            sig.sl_price,
                            sig.direction,
                            sig.size_usdt,
                            cfg.leverage,
                            max_hold,
                            trailing,
                            trail_act_r,
                            trail_dist_r,
                            args.offset_bps,
                            args.fill_window,
                        )
                        if t is not None:
                            pooled[strat][mdl].append(t)
                # advance past the taker exit to preserve one-position occupancy
                i = taker["exit_i"] + 1

    # --- report ---
    print(f"\n=== RESULT (out-of-sample pool · {window_tag}) ===", flush=True)
    print(
        f"  {'strat':16s} {'model':12s} {'n':>5s} {'fill%':>6s} {'win%':>6s} {'avg_bps':>8s} {'avg_$':>9s} {'tot_bps':>9s}",
        flush=True,
    )
    for strat in strats:
        for mdl in _MODELS:
            a = _agg(pooled[strat][mdl], sig_count[strat])
            print(
                f"  {strat:16s} {mdl:12s} {a['n']:5d} {a['fill_rate'] * 100:6.1f} {a['win'] * 100:6.1f} "
                f"{a['avg_bps']:8.2f} {a['avg_usdt']:9.4f} {a['tot_bps']:9.0f}",
                flush=True,
            )
        print(flush=True)

    print("Read: avg_bps is net per-trade in bps of NOTIONAL (size-independent). taker = live-safe model;", flush=True)
    print("maker_naive = what the sim assumes today (instant fill); maker_real = honest post-only fills.", flush=True)
    print(f"Generated {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC.", flush=True)


if __name__ == "__main__":
    main()
