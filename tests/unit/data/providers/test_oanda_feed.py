"""Unit tests for src/data/providers/oanda.py (pure logic — no OANDA API calls)."""

from __future__ import annotations

import pytest

from src.data.providers import _REGISTRY, registered_feeds
from src.data.providers.oanda import _to_granularity, _to_interval, _parse_oanda_ts


class TestOandaFeedRegistration:
    def test_oanda_feed_registered(self):
        assert "oanda" in _REGISTRY

    def test_oanda_feed_in_registered_list(self):
        assert "oanda" in registered_feeds()


class TestToGranularity:
    def test_5m_maps_to_M5(self):
        assert _to_granularity("5m") == "M5"

    def test_1m_maps_to_M1(self):
        assert _to_granularity("1m") == "M1"

    def test_15m_maps_to_M15(self):
        assert _to_granularity("15m") == "M15"

    def test_1h_maps_to_H1(self):
        assert _to_granularity("1h") == "H1"

    def test_4h_maps_to_H4(self):
        assert _to_granularity("4h") == "H4"

    def test_1d_maps_to_D(self):
        assert _to_granularity("1d") == "D"

    def test_unknown_uppercases(self):
        assert _to_granularity("M5") == "M5"


class TestToInterval:
    def test_5m_is_300_seconds(self):
        assert _to_interval("5m") == 300

    def test_1m_is_60_seconds(self):
        assert _to_interval("1m") == 60

    def test_15m_is_900_seconds(self):
        assert _to_interval("15m") == 900

    def test_1h_is_3600_seconds(self):
        assert _to_interval("1h") == 3600

    def test_1d_is_86400_seconds(self):
        assert _to_interval("1d") == 86400

    def test_unknown_defaults_to_300(self):
        assert _to_interval("unknown") == 300


class TestParseOandaFeedTs:
    def test_parses_iso_to_int(self):
        ts = _parse_oanda_ts("2025-04-16T14:30:00.000000000Z")
        assert isinstance(ts, int)

    def test_five_minute_gap_is_300000ms(self):
        ts1 = _parse_oanda_ts("2025-04-16T14:30:00.000000000Z")
        ts2 = _parse_oanda_ts("2025-04-16T14:35:00.000000000Z")
        assert ts2 - ts1 == 300_000
