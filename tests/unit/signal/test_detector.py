"""Tests for price-precision rounding in the signal detector.

Regression guard for the sub-cent TP/SL bug: fixed 8-decimal rounding quantized
ATR-based TP/SL distances on sub-cent instruments (e.g. PEPE ~$1e-5) so coarsely
that the TP/SL distance ratio the risk manager's Rule 3 checks got corrupted —
letting sub-1.2-R/R configs leak trades on those pairs only. Price-aware rounding
preserves the intended ratio.
"""

from __future__ import annotations

from src.signal.detector import _round_price


def test_round_price_is_noop_at_or_above_ten_cents():
    # at/above $0.1, behaves exactly like fixed 8-dp rounding (no behaviour change)
    for p in (80000.123456789, 3210.55, 1.23456789, 0.30000001, 0.1):
        assert _round_price(p) == round(p, 8)


def test_round_price_preserves_precision_below_a_dollar():
    p = 0.000012345678  # PEPE scale
    # 8-dp rounding loses real precision here; price-aware rounding does not
    assert abs(round(p, 8) - p) / p > 1e-5
    assert abs(_round_price(p) - p) / p < 1e-7


def test_round_price_preserves_tp_sl_ratio_on_subcent_pair():
    # planned R/R = tp_mult / sl_mult = 0.714 — must survive rounding so Rule 3
    # sees the true (rejected) ratio rather than a quantization artefact.
    entry, atr = 0.000012345, 0.00000031
    tp_mult, sl_mult = 1.0, 1.4
    tp = _round_price(entry + atr * tp_mult)
    sl = _round_price(entry - atr * sl_mult)
    ratio = abs(tp - entry) / abs(sl - entry)
    assert abs(ratio - tp_mult / sl_mult) < 0.01


def test_round_price_handles_nonpositive():
    assert _round_price(0.0) == 0.0
    assert _round_price(-1.0) == round(-1.0, 8)
