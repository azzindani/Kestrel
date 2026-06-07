"""Unit tests for compute_exit_prices (signal/detector.py).

Covers both TP/SL modes — ATR-multiple and fixed-percent reward:risk — and the
liquidation-safety clamp that keeps a percentage stop inside the liquidation
distance so liquidation can't front-run the intended stop.
"""

from __future__ import annotations

import dataclasses

import pytest

from src.config import Direction, load_params
from src.signal.detector import _LIQ_SAFETY_FRACTION, compute_exit_prices

_BASE = load_params("params.json")


def _params(**overrides):
    return dataclasses.replace(_BASE, **overrides)


class TestAtrMode:
    def test_long_atr_distances(self):
        p = _params(tp_sl_pct_enabled=False, tp_atr_multiplier=1.6, sl_atr_multiplier=1.0)
        tp, sl = compute_exit_prices(100.0, Direction.LONG, atr=2.0, params=p, leverage=20)
        assert tp == pytest.approx(100.0 + 2.0 * 1.6)
        assert sl == pytest.approx(100.0 - 2.0 * 1.0)

    def test_short_atr_distances_mirror_long(self):
        p = _params(tp_sl_pct_enabled=False, tp_atr_multiplier=1.6, sl_atr_multiplier=1.0)
        tp, sl = compute_exit_prices(100.0, Direction.SHORT, atr=2.0, params=p, leverage=20)
        assert tp == pytest.approx(100.0 - 3.2)
        assert sl == pytest.approx(100.0 + 2.0)

    def test_atr_mode_requires_atr(self):
        p = _params(tp_sl_pct_enabled=False)
        assert compute_exit_prices(100.0, Direction.LONG, atr=None, params=p, leverage=20) is None
        assert compute_exit_prices(100.0, Direction.LONG, atr=0.0, params=p, leverage=20) is None


class TestPctMode:
    def test_long_pct_reward_risk(self):
        # 5% TP / 2.5% SL at 20x: liq distance ~5%, clamp 0.7/20=3.5% > 2.5% so SL unclamped.
        p = _params(tp_sl_pct_enabled=True, tp_pct=0.05, sl_pct=0.025)
        tp, sl = compute_exit_prices(100.0, Direction.LONG, atr=2.0, params=p, leverage=20)
        assert tp == pytest.approx(105.0)
        assert sl == pytest.approx(97.5)
        # reward:risk = 2:1
        assert (tp - 100.0) / (100.0 - sl) == pytest.approx(2.0)

    def test_short_pct_mirrors_long(self):
        p = _params(tp_sl_pct_enabled=True, tp_pct=0.05, sl_pct=0.025)
        tp, sl = compute_exit_prices(100.0, Direction.SHORT, atr=2.0, params=p, leverage=20)
        assert tp == pytest.approx(95.0)
        assert sl == pytest.approx(102.5)

    def test_pct_mode_ignores_missing_atr(self):
        # No ATR needed in pct mode — must still produce prices.
        p = _params(tp_sl_pct_enabled=True, tp_pct=0.05, sl_pct=0.025)
        out = compute_exit_prices(100.0, Direction.LONG, atr=None, params=p, leverage=20)
        assert out is not None

    def test_sl_clamped_inside_liquidation_at_high_leverage(self):
        # 2.5% requested SL at 50x: liq ~1.5%, clamp = 0.7/50 = 1.4% → SL tightened to 1.4%.
        p = _params(tp_sl_pct_enabled=True, tp_pct=0.05, sl_pct=0.025)
        _, sl = compute_exit_prices(100.0, Direction.LONG, atr=2.0, params=p, leverage=50)
        expected_sl_pct = _LIQ_SAFETY_FRACTION / 50  # 0.014
        assert sl == pytest.approx(100.0 * (1.0 - expected_sl_pct))
        assert (100.0 - sl) / 100.0 < 1.0 / 50  # strictly inside liquidation

    def test_clamp_noop_when_already_safe(self):
        # 1% SL at 20x is well inside; clamp must not move it.
        p = _params(tp_sl_pct_enabled=True, tp_pct=0.02, sl_pct=0.01)
        _, sl = compute_exit_prices(100.0, Direction.LONG, atr=2.0, params=p, leverage=20)
        assert sl == pytest.approx(99.0)


class TestDegenerate:
    def test_zero_entry_returns_none(self):
        p = _params(tp_sl_pct_enabled=True)
        assert compute_exit_prices(0.0, Direction.LONG, atr=2.0, params=p, leverage=20) is None

    def test_zero_tp_or_sl_pct_returns_none(self):
        assert compute_exit_prices(
            100.0, Direction.LONG, atr=2.0,
            params=_params(tp_sl_pct_enabled=True, tp_pct=0.0, sl_pct=0.025), leverage=20,
        ) is None
        assert compute_exit_prices(
            100.0, Direction.LONG, atr=2.0,
            params=_params(tp_sl_pct_enabled=True, tp_pct=0.05, sl_pct=0.0), leverage=20,
        ) is None
