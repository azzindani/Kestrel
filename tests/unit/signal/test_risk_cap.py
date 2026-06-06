"""Unit tests for risk-based position sizing (signal/sizing.py cap_size_for_risk).

Covers the "20% cut loss" hardening: a stop-out must cost at most
max_loss_pct of bucket equity, independent of leverage.
"""

from __future__ import annotations

import pytest

from src.signal.sizing import cap_size_for_risk


class TestCapSizeForRisk:
    def test_caps_loss_to_target_fraction(self):
        # equity=100, entry=100, sl=99.5 → sl_dist=0.5%; leverage=50; max_loss=2%.
        # cap = 0.02*100 / (50*0.005) = 2 / 0.25 = 8.0 USDT margin.
        capped = cap_size_for_risk(100.0, 100.0, 100.0, 99.5, 50, 0.02)
        assert capped == pytest.approx(8.0)

    def test_resulting_loss_at_sl_equals_target(self):
        equity, entry, sl, lev, maxloss = 100.0, 100.0, 99.5, 50, 0.02
        size = cap_size_for_risk(100.0, equity, entry, sl, lev, maxloss)
        sl_dist_pct = abs(entry - sl) / entry
        loss_at_sl = size * lev * sl_dist_pct  # notional × distance
        assert loss_at_sl == pytest.approx(maxloss * equity, rel=1e-3)

    def test_does_not_inflate_smaller_size(self):
        # Requested size already below the cap → unchanged.
        assert cap_size_for_risk(1.0, 100.0, 100.0, 99.5, 50, 0.02) == pytest.approx(1.0)

    def test_disabled_when_max_loss_zero(self):
        assert cap_size_for_risk(100.0, 100.0, 100.0, 99.5, 50, 0.0) == 100.0

    def test_higher_leverage_smaller_cap(self):
        low = cap_size_for_risk(100.0, 100.0, 100.0, 99.5, 10, 0.02)
        high = cap_size_for_risk(100.0, 100.0, 100.0, 99.5, 50, 0.02)
        assert high < low

    def test_wider_stop_smaller_cap(self):
        tight = cap_size_for_risk(100.0, 100.0, 100.0, 99.5, 50, 0.02)  # 0.5%
        wide = cap_size_for_risk(100.0, 100.0, 100.0, 98.0, 50, 0.02)   # 2.0%
        assert wide < tight

    def test_degenerate_inputs_safe(self):
        assert cap_size_for_risk(10.0, 0.0, 100.0, 99.5, 50, 0.02) == 10.0  # equity 0
        assert cap_size_for_risk(10.0, 100.0, 100.0, 100.0, 50, 0.02) == 10.0  # sl==entry
        assert cap_size_for_risk(10.0, 100.0, 100.0, 99.5, 0, 0.02) == 10.0  # leverage 0
