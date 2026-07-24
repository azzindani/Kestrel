"""
Layer 1 — indicator-based exit rules (owner-directed 2026-07-24, iter 65).

Close a position when the ENTRY pattern's own indicator state reverses, instead
of (or in addition to) a fixed price bracket — "the signal that got you in is
gone, get out". Validated cross-era in the iter-65 sweep (scripts/algo_search.py
sigexit presets: dollar-+EV in BOTH the recent year and the untouched lockbox
year under realistic fees + funding; sma_cross/sigexit the strongest result on
record, incl. the first-ever cross-era-positive BTC cell).

Pure logic only (no I/O): the daemon (L3 boundary) supplies the candle window
and calls close_position when a reason comes back. Price disaster-stops
(SL / liquidation / any configured TP) are ALWAYS checked first by the
execution engine — these rules run only when no price exit fired, mirroring
the backtest wrapper's ordering exactly.

Rules are STATE-based (not cross-based), matching the validated backtest
semantics: e.g. a long macd-family position exits whenever the MACD line sits
at-or-below its signal line on a closed candle — not only on the crossing
candle — so a position opened into a state that already decayed exits on the
next close rather than waiting for a fresh cross.

Modes (Params.indicator_exit_mode):
    ""            — off (price brackets only; default for every existing bot)
    "sigexit"     — reversal exit only ('signal_exit')
    "sigexit_rsi" — reversal exit + RSI 70/30 profit-take ('indicator_tp')
    "sigexit_tp"  — same reversal rule; the deploy preset keeps a price TP so
                    the reversal acts as the CUT-LOSS side (hybrid)
"""

from __future__ import annotations

from typing import Optional, Sequence

from src.config import Candle, Direction, Params
from src.signal.patterns import _cci_pair, _macd_lines, _sma

REASON_SIGNAL_EXIT = "signal_exit"
REASON_INDICATOR_TP = "indicator_tp"

_MACD_FAMILY = ("macd_cross", "macd_rsi", "macd_state")
_SMA_FAMILY = ("sma_cross", "sma_state")
_CCI_FAMILY = ("cci_mom", "cci_state")
_ENSEMBLE_FAMILY = ("ensemble_3of4", "ensemble_state")


def indicator_exit_reason(
    candles: Sequence[Candle],
    direction: Direction,
    pattern: str,
    params: Params,
) -> Optional[str]:
    """Exit decision for an open position on the just-closed candle.

    Returns 'indicator_tp' (RSI extreme profit-take, sigexit_rsi mode only),
    'signal_exit' (the entry indicator's state reversed), or None (hold).
    """
    mode = params.indicator_exit_mode
    if not mode or not candles:
        return None
    latest = candles[-1]
    long = direction is Direction.LONG

    # RSI-extreme profit-take: exit INTO strength (sigexit_rsi mode only).
    if mode == "sigexit_rsi" and latest.rsi14 is not None:
        if (long and latest.rsi14 >= 70.0) or (not long and latest.rsi14 <= 30.0):
            return REASON_INDICATOR_TP

    if pattern in _MACD_FAMILY or pattern in _ENSEMBLE_FAMILY:
        macd_state = _macd_up(candles, params)
    else:
        macd_state = None

    if pattern in _MACD_FAMILY:
        if macd_state is None:
            return None
        return REASON_SIGNAL_EXIT if macd_state is not long else None

    if pattern in _SMA_FAMILY:
        sma_state = _sma_up(candles, params)
        if sma_state is None:
            return None
        return REASON_SIGNAL_EXIT if sma_state is not long else None

    if pattern in _CCI_FAMILY:
        cci = _cci_now(candles, params)
        if cci is None:
            return None
        reversed_ = cci < 0.0 if long else cci > 0.0
        return REASON_SIGNAL_EXIT if reversed_ else None

    if pattern in _ENSEMBLE_FAMILY:
        sma_state = _sma_up(candles, params)
        cci = _cci_now(candles, params)
        rsi = latest.rsi14
        if macd_state is None or sma_state is None or cci is None or rsi is None:
            return None
        states = [macd_state, sma_state, cci > 0.0, rsi > 50.0]
        aligned = sum(1 for s in states if s == long)
        return REASON_SIGNAL_EXIT if aligned <= 1 else None

    return None


def _macd_up(candles: Sequence[Candle], params: Params) -> Optional[bool]:
    """MACD line above its signal line on the latest closed candle."""
    if len(candles) < params.macd_slow + params.macd_signal + 1:
        return None
    m = _macd_lines([c.close for c in candles], params.macd_fast, params.macd_slow, params.macd_signal)
    if m is None:
        return None
    macd_last, _, sig_last, _ = m
    return macd_last > sig_last


def _sma_up(candles: Sequence[Candle], params: Params) -> Optional[bool]:
    """Fast SMA above slow SMA on the latest closed candle."""
    closes = [c.close for c in candles]
    fast = _sma(closes, params.sma_cross_fast)
    slow = _sma(closes, params.sma_cross_slow)
    if fast is None or slow is None:
        return None
    return fast > slow


def _cci_now(candles: Sequence[Candle], params: Params) -> Optional[float]:
    """CCI on the latest closed candle."""
    pair = _cci_pair(candles, params.cci_period)
    if pair is None:
        return None
    now, _prev = pair
    return now
