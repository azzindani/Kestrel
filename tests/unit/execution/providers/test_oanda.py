"""Unit tests for src/execution/providers/oanda.py (pure logic — no OANDA API calls)."""

from __future__ import annotations

import pytest

from src.config import Direction
from src.data.providers.oanda import _parse_oanda_ts
from src.execution.providers import _REGISTRY, registered_providers
from src.execution.providers.oanda import _units


class TestOandaRegistration:
    def test_oanda_registered_in_registry(self):
        assert "oanda" in _REGISTRY

    def test_oanda_in_registered_providers_list(self):
        assert "oanda" in registered_providers()


class TestUnitsCalculation:
    def test_long_units_positive(self):
        result = _units(10.0, 20, 1.1, Direction.LONG)  # EUR/USD at 1.10
        assert float(result) > 0

    def test_short_units_negative(self):
        result = _units(10.0, 20, 1.1, Direction.SHORT)
        assert float(result) < 0

    def test_long_units_magnitude(self):
        # size_usdt=10, leverage=20 → notional=200; at price=100 → 2 units
        result = _units(10.0, 20, 100.0, Direction.LONG)
        assert float(result) == pytest.approx(2.0, abs=0.01)

    def test_short_units_magnitude(self):
        result = _units(10.0, 20, 100.0, Direction.SHORT)
        assert float(result) == pytest.approx(-2.0, abs=0.01)

    def test_units_returns_string(self):
        result = _units(10.0, 20, 83000.0, Direction.LONG)
        assert isinstance(result, str)

    def test_units_higher_leverage_more_units(self):
        low = float(_units(10.0, 10, 100.0, Direction.LONG))
        high = float(_units(10.0, 20, 100.0, Direction.LONG))
        assert high > low

    def test_units_higher_price_fewer_units(self):
        cheap = float(_units(10.0, 20, 100.0, Direction.LONG))
        expensive = float(_units(10.0, 20, 1000.0, Direction.LONG))
        assert cheap > expensive

    def test_units_fractional_crypto(self):
        # BTC at 83000: 10*20/83000 ≈ 0.0024 units (fractional)
        result = float(_units(10.0, 20, 83000.0, Direction.LONG))
        assert result == pytest.approx(0.0, abs=0.01)  # rounds to 0.0 at 2dp
        # Use higher size to get measurable units
        result2 = float(_units(1000.0, 20, 83000.0, Direction.LONG))
        assert result2 > 0


class TestParseOandaTs:
    def test_parse_standard_oanda_timestamp(self):
        iso = "2025-04-16T14:30:00.000000000Z"
        ts = _parse_oanda_ts(iso)
        assert isinstance(ts, int)
        assert ts > 0

    def test_parse_returns_milliseconds(self):
        # 2025-04-16T14:30:00Z ≈ 1744814200000 ms range
        iso = "2025-04-16T14:30:00.000000000Z"
        ts = _parse_oanda_ts(iso)
        assert ts > 1_700_000_000_000  # after 2023

    def test_parse_two_different_timestamps_differ(self):
        ts1 = _parse_oanda_ts("2025-04-16T14:30:00.000000000Z")
        ts2 = _parse_oanda_ts("2025-04-16T14:35:00.000000000Z")
        assert ts2 > ts1

    def test_parse_five_minute_gap(self):
        ts1 = _parse_oanda_ts("2025-04-16T14:30:00.000000000Z")
        ts2 = _parse_oanda_ts("2025-04-16T14:35:00.000000000Z")
        assert ts2 - ts1 == 5 * 60 * 1000
