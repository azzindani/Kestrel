"""Unit tests for src/data/providers/ibkr.py (pure logic — no IB connection)."""

from __future__ import annotations

import pytest

from src.data.providers import _REGISTRY, registered_feeds
from src.data.providers.ibkr import _parse_host_port, _INTERVAL_MAP, _IB_BARSIZE


class TestIBKRFeedRegistration:
    def test_ibkr_feed_registered(self):
        assert "ibkr" in _REGISTRY

    def test_ibkr_feed_in_registered_list(self):
        assert "ibkr" in registered_feeds()


class TestIBKRFeedHostPort:
    def test_parse_standard(self):
        host, port = _parse_host_port("127.0.0.1:7497")
        assert host == "127.0.0.1"
        assert port == 7497

    def test_parse_default_when_no_port(self):
        _, port = _parse_host_port("127.0.0.1")
        assert port == 7497


class TestIBKRBarSize:
    def test_5m_bar_size(self):
        assert _IB_BARSIZE["5m"] == "5 mins"

    def test_1m_bar_size(self):
        assert _IB_BARSIZE["1m"] == "1 min"

    def test_1h_bar_size(self):
        assert _IB_BARSIZE["1h"] == "1 hour"

    def test_1d_bar_size(self):
        assert _IB_BARSIZE["1d"] == "1 day"


class TestIBKRFeedClientIdOffset:
    """Feed client ID must be execution client ID + 1 to avoid conflicts."""

    def test_feed_client_id_offset(self):
        from tests.helpers.factories import make_app_config
        from src.data.providers.ibkr import IBKRFeed
        from unittest.mock import MagicMock

        cfg = make_app_config(exchange="ibkr", api_key="127.0.0.1:7497", api_secret="1")
        builder = MagicMock()
        feed = IBKRFeed(cfg=cfg, pair="BTC.USD", timeframe="5m", builder=builder)
        # API_SECRET=1 → client_id should be 2 (offset +1)
        assert feed._client_id == 2

    def test_feed_client_id_default_offset(self):
        from tests.helpers.factories import make_app_config
        from src.data.providers.ibkr import IBKRFeed
        from unittest.mock import MagicMock

        cfg = make_app_config(exchange="ibkr", api_key="127.0.0.1:7497", api_secret="3")
        builder = MagicMock()
        feed = IBKRFeed(cfg=cfg, pair="BTC.USD", timeframe="5m", builder=builder)
        assert feed._client_id == 4
