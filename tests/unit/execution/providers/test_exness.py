"""Unit tests for src/execution/providers/exness.py (pure logic — no MetaApi calls)."""

from __future__ import annotations

import pytest

from src.execution.providers import _REGISTRY, registered_providers
from src.execution.providers.exness import _compute_volume, _round_volume


class TestExnessRegistration:
    def test_exness_registered_in_registry(self):
        assert "exness" in _REGISTRY

    def test_exness_in_registered_providers_list(self):
        assert "exness" in registered_providers()


class TestRoundVolume:
    def test_floors_to_volume_step(self):
        # 0.027 floored to 0.01 step → 0.02
        assert _round_volume(0.027, 0.01, 0.01) == pytest.approx(0.02)

    def test_below_min_returns_zero(self):
        # 0.0025 < min 0.01 → 0.0 (reject, do not inflate)
        assert _round_volume(0.0025, 0.01, 0.01) == 0.0

    def test_exactly_min_is_kept(self):
        assert _round_volume(0.01, 0.01, 0.01) == pytest.approx(0.01)

    def test_larger_step(self):
        assert _round_volume(0.37, 0.1, 0.1) == pytest.approx(0.3)


class TestComputeVolume:
    def test_gold_small_bucket_rejected(self):
        # $10 bucket × 50x = $500 notional; gold contract 100oz @ $2000 →
        # 0.0025 lots, below 0.01 min → rejected (0.0).
        vol = _compute_volume(10.0, 50, 2000.0, 100.0, 0.01, 0.01)
        assert vol == 0.0

    def test_gold_larger_bucket_trades(self):
        # $100 bucket × 50x = $5000 notional → 0.025 lots → floored to 0.02.
        vol = _compute_volume(100.0, 50, 2000.0, 100.0, 0.01, 0.01)
        assert vol == pytest.approx(0.02)

    def test_btc_bucket(self):
        # $100 × 50x = $5000; BTC contract 1 @ $60000 → 0.0833 → floored 0.08.
        vol = _compute_volume(100.0, 50, 60000.0, 1.0, 0.01, 0.01)
        assert vol == pytest.approx(0.08)

    def test_higher_leverage_more_lots(self):
        low = _compute_volume(100.0, 20, 80.0, 100.0, 0.01, 0.01)
        high = _compute_volume(100.0, 50, 80.0, 100.0, 0.01, 0.01)
        assert high >= low

    def test_zero_price_safe(self):
        assert _compute_volume(100.0, 50, 0.0, 100.0, 0.01, 0.01) == 0.0

    def test_zero_contract_size_safe(self):
        assert _compute_volume(100.0, 50, 2000.0, 0.0, 0.01, 0.01) == 0.0
