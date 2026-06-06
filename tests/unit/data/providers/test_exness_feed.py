"""Unit tests for src/data/providers/exness.py (pure logic — no MetaApi calls)."""

from __future__ import annotations

import datetime

from src.data.providers import _REGISTRY, registered_feeds
from src.data.providers.exness import _candle_time_to_ms, _to_interval, _to_metaapi_tf


class TestExnessFeedRegistration:
    def test_exness_feed_registered(self):
        assert "exness" in _REGISTRY

    def test_exness_feed_in_registered_list(self):
        assert "exness" in registered_feeds()


class TestToMetaapiTf:
    def test_5m_maps(self):
        assert _to_metaapi_tf("5m") == "5m"

    def test_15m_maps(self):
        assert _to_metaapi_tf("15m") == "15m"

    def test_unknown_lowercased(self):
        assert _to_metaapi_tf("M5") == "m5"


class TestToInterval:
    def test_5m_is_300_seconds(self):
        assert _to_interval("5m") == 300

    def test_15m_is_900_seconds(self):
        assert _to_interval("15m") == 900

    def test_1h_is_3600_seconds(self):
        assert _to_interval("1h") == 3600

    def test_unknown_defaults_to_300(self):
        assert _to_interval("unknown") == 300


class TestCandleTimeToMs:
    def test_datetime_utc(self):
        dt = datetime.datetime(2025, 4, 16, 14, 30, 0, tzinfo=datetime.timezone.utc)
        assert _candle_time_to_ms(dt) == int(dt.timestamp() * 1000)

    def test_naive_datetime_treated_utc(self):
        naive = datetime.datetime(2025, 4, 16, 14, 30, 0)
        aware = naive.replace(tzinfo=datetime.timezone.utc)
        assert _candle_time_to_ms(naive) == int(aware.timestamp() * 1000)

    def test_iso_string(self):
        ts = _candle_time_to_ms("2025-04-16T14:30:00.000Z")
        assert isinstance(ts, int)
        assert ts > 1_700_000_000_000

    def test_five_minute_gap(self):
        t1 = _candle_time_to_ms("2025-04-16T14:30:00.000Z")
        t2 = _candle_time_to_ms("2025-04-16T14:35:00.000Z")
        assert t2 - t1 == 300_000
