"""Tests for equity-scaled position sizing (src/signal/sizing.py).

Covers the five real-trader behaviours: equity scaling (compounding both ways),
confidence weighting, drawdown de-risking, consecutive-loss cool-off, and the
min-size floor / bucket-exhaustion stop. Plus the None-state legacy fallback.
"""

from __future__ import annotations

from src.signal.sizing import compute_position_size
from tests.helpers.factories import make_params, make_sizing_state


def test_none_state_uses_legacy_fixed_bucket():
    params = make_params()
    assert compute_position_size(0.80, None, params) == 10.0  # full conviction
    assert compute_position_size(0.60, None, params) == 5.0  # half conviction


def test_scales_up_with_equity_full_conviction():
    # full fraction 1.0 → size == equity; compounding as the balance grows
    params = make_params(size_fraction_full=1.0)
    assert compute_position_size(0.80, make_sizing_state(equity_usdt=10.0), params) == 10.0
    assert compute_position_size(0.80, make_sizing_state(equity_usdt=500.0), params) == 500.0


def test_scales_down_with_equity():
    # losses shrink equity → smaller size
    params = make_params(size_fraction_full=1.0)
    assert compute_position_size(0.80, make_sizing_state(equity_usdt=4.0), params) == 4.0


def test_confidence_weighting():
    params = make_params(size_fraction_full=1.0, size_fraction_half=0.5)
    st = make_sizing_state(equity_usdt=100.0)
    assert compute_position_size(0.80, st, params) == 100.0  # full
    assert compute_position_size(0.60, st, params) == 50.0  # half


def test_drawdown_derisking():
    # equity 70 vs peak 100 → 30% drawdown ≥ 20% threshold → ×0.5
    params = make_params(size_fraction_full=1.0, drawdown_derisk_threshold=0.20, drawdown_derisk_factor=0.5)
    st = make_sizing_state(equity_usdt=70.0, peak_equity_usdt=100.0)
    assert compute_position_size(0.80, st, params) == 35.0


def test_no_derisk_above_threshold():
    # 10% drawdown < 20% threshold → no reduction
    params = make_params(size_fraction_full=1.0, drawdown_derisk_threshold=0.20, drawdown_derisk_factor=0.5)
    st = make_sizing_state(equity_usdt=90.0, peak_equity_usdt=100.0)
    assert compute_position_size(0.80, st, params) == 90.0


def test_consecutive_loss_cooloff():
    # 3 consecutive losses ≥ cooloff 3 → ×0.5
    params = make_params(size_fraction_full=1.0, consec_loss_cooloff=3, consec_loss_factor=0.5)
    st = make_sizing_state(equity_usdt=100.0, consec_losses=3)
    assert compute_position_size(0.80, st, params) == 50.0
    # 2 losses < cooloff → unaffected
    st2 = make_sizing_state(equity_usdt=100.0, consec_losses=2)
    assert compute_position_size(0.80, st2, params) == 100.0


def test_min_size_floor_bumps_when_affordable():
    # equity above floor but fraction puts size below floor → bump to floor
    params = make_params(size_fraction_half=0.05, size_min_usdt=2.0)
    st = make_sizing_state(equity_usdt=20.0)  # half: 20×0.05 = 1.0 < 2.0 floor
    assert compute_position_size(0.60, st, params) == 2.0


def test_bucket_exhausted_returns_zero():
    # equity below the viable floor → 0 (stop trading this bucket)
    params = make_params(size_fraction_full=1.0, size_min_usdt=2.0)
    assert compute_position_size(0.80, make_sizing_state(equity_usdt=1.5), params) == 0.0
    assert compute_position_size(0.80, make_sizing_state(equity_usdt=0.0), params) == 0.0


def test_never_exceeds_equity():
    # fraction can't push size above the bucket's own equity (margin cap)
    params = make_params(size_fraction_full=1.0)
    assert compute_position_size(0.80, make_sizing_state(equity_usdt=12.34), params) == 12.34
