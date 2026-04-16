"""
Layer 1 — backtest metrics computation.

Pure functions: trade list in → metrics dict out. No I/O.
Fee + slippage must already be applied before calling these functions.
"""

from __future__ import annotations

import math
from typing import Sequence


def compute_metrics(trades: Sequence[dict]) -> dict:
    """Compute all performance metrics from a list of closed trade dicts.

    Each trade dict must contain:
        pnl_net_usdt: float
        pnl_pct: float
        close_reason: str ('take_profit' | 'stop_loss' | 'timeout' | 'liquidated')
        entry_ts: int  (unix ms)
        exit_ts: int   (unix ms)
        size_usdt: float

    Returns a metrics dict suitable for reporting and tune.sh comparison.
    """
    if not trades:
        return _empty_metrics()

    pnls = [float(t["pnl_net_usdt"]) for t in trades]
    pnl_pcts = [float(t["pnl_pct"]) for t in trades]
    wins = [t for t in trades if float(t["pnl_net_usdt"]) > 0]
    losses = [t for t in trades if float(t["pnl_net_usdt"]) <= 0]

    total = len(trades)
    win_count = len(wins)
    win_rate = win_count / total if total > 0 else 0.0

    total_pnl = sum(pnls)
    avg_pnl = total_pnl / total if total > 0 else 0.0
    avg_win = sum(t["pnl_net_usdt"] for t in wins) / win_count if wins else 0.0
    avg_loss = sum(t["pnl_net_usdt"] for t in losses) / len(losses) if losses else 0.0
    profit_factor = (
        abs(avg_win * win_count) / abs(avg_loss * len(losses)) if losses and avg_loss != 0.0 else float("inf")
    )

    # Sharpe ratio (annualised, assuming 5m candles)
    sharpe = _sharpe(pnl_pcts)

    # Maximum drawdown
    max_dd, max_dd_pct = _max_drawdown(pnls)

    # Close reason breakdown
    reasons: dict[str, int] = {}
    for t in trades:
        r = t.get("close_reason", "unknown")
        reasons[r] = reasons.get(r, 0) + 1

    # Average hold duration (candles)
    hold_candles = [t.get("hold_candles", 0) or 0 for t in trades]
    avg_hold = sum(hold_candles) / len(hold_candles) if hold_candles else 0.0

    return {
        "total_trades": total,
        "win_count": win_count,
        "loss_count": total - win_count,
        "win_rate": round(win_rate, 4),
        "total_pnl_usdt": round(total_pnl, 4),
        "avg_pnl_usdt": round(avg_pnl, 4),
        "avg_win_usdt": round(avg_win, 4),
        "avg_loss_usdt": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_usdt": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "avg_hold_candles": round(avg_hold, 2),
        "close_reasons": reasons,
    }


def _empty_metrics() -> dict:
    return {
        "total_trades": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
        "total_pnl_usdt": 0.0,
        "avg_pnl_usdt": 0.0,
        "avg_win_usdt": 0.0,
        "avg_loss_usdt": 0.0,
        "profit_factor": None,
        "sharpe_ratio": 0.0,
        "max_drawdown_usdt": 0.0,
        "max_drawdown_pct": 0.0,
        "avg_hold_candles": 0.0,
        "close_reasons": {},
    }


def _sharpe(pnl_pcts: list[float], periods_per_year: int = 105_120) -> float:
    """Annualised Sharpe ratio. periods_per_year = 365 * 24 * 12 (5m candles)."""
    if len(pnl_pcts) < 2:
        return 0.0
    mean = sum(pnl_pcts) / len(pnl_pcts)
    variance = sum((p - mean) ** 2 for p in pnl_pcts) / (len(pnl_pcts) - 1)
    std = math.sqrt(variance)
    if std == 0.0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)


def _max_drawdown(pnls: list[float]) -> tuple[float, float]:
    """Compute (max_drawdown_abs, max_drawdown_pct) from PnL series."""
    if not pnls:
        return 0.0, 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = (max_dd / peak * 100.0) if peak > 0 else 0.0
    return max_dd, max_dd_pct


def compare_metrics(baseline: dict, candidate: dict, threshold: float = 0.05) -> dict:
    """Compare candidate metrics to baseline.

    Returns a dict of {metric: 'improve' | 'hold' | 'regress'} and
    an overall 'verdict': 'ACCEPT' | 'REJECT'.

    A metric regresses if it worsens by more than threshold (5%).
    """
    key_metrics = [
        ("win_rate", True),  # higher is better
        ("total_pnl_usdt", True),
        ("sharpe_ratio", True),
        ("max_drawdown_pct", False),  # lower is better
        ("profit_factor", True),
    ]

    results = {}
    any_regress = False

    for metric, higher_is_better in key_metrics:
        b = baseline.get(metric)
        c = candidate.get(metric)
        if b is None or c is None:
            results[metric] = "hold"
            continue
        if b == 0.0:
            results[metric] = "hold"
            continue

        delta = (c - b) / abs(b)
        if higher_is_better:
            if delta >= 0:
                results[metric] = "improve"
            elif delta < -threshold:
                results[metric] = "regress"
                any_regress = True
            else:
                results[metric] = "hold"
        else:
            if delta <= 0:
                results[metric] = "improve"
            elif delta > threshold:
                results[metric] = "regress"
                any_regress = True
            else:
                results[metric] = "hold"

    results["verdict"] = "REJECT" if any_regress else "ACCEPT"
    return results
