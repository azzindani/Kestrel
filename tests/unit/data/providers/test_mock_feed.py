"""Unit tests for the synthetic MockFeed provider."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.data.providers import _REGISTRY, _SHARED_EXCHANGES, registered_feeds
from src.data.providers.mock import MockFeed
from tests.helpers.factories import make_app_config


class TestMockFeedRegistration:
    def test_mock_feed_registered(self):
        assert "mock" in _REGISTRY

    def test_mock_feed_in_registered_list(self):
        assert "mock" in registered_feeds()

    def test_mock_is_per_bot_not_shared(self):
        assert "mock" not in _SHARED_EXCHANGES


class TestMockFeedSubscription:
    def test_legacy_constructor_subscribes_immediately(self):
        cfg = make_app_config(exchange="mock")
        builder = MagicMock()
        feed = MockFeed(cfg, pair="BTC/USDT", timeframe="5m", builder=builder)
        assert len(feed.subscriptions) == 1
        assert feed.subscriptions[0].pair == "BTC/USDT"

    def test_explicit_subscribe_appends(self):
        cfg = make_app_config(exchange="mock")
        feed = MockFeed(cfg)
        feed.subscribe("BTC/USDT", "5m", MagicMock())
        feed.subscribe("ETH/USDT", "5m", MagicMock())
        assert len(feed.subscriptions) == 2
        assert {s.pair for s in feed.subscriptions} == {"BTC/USDT", "ETH/USDT"}

    def test_different_pairs_get_distinct_starting_price(self):
        cfg = make_app_config(exchange="mock")
        feed = MockFeed(cfg)
        feed.subscribe("BTC/USDT", "5m", MagicMock())
        feed.subscribe("ETH/USDT", "5m", MagicMock())
        # Per-pair seeded RNG → distinct starting prices for visual variety.
        prices = {s.pair: s.price for s in feed.subscriptions}
        assert prices["BTC/USDT"] != prices["ETH/USDT"]


class TestMockFeedLifecycle:
    @pytest.mark.asyncio
    async def test_stop_before_run_is_noop(self):
        cfg = make_app_config(exchange="mock")
        feed = MockFeed(cfg)
        feed.stop()  # safe even with no run() call

    @pytest.mark.asyncio
    async def test_short_run_emits_candles(self, monkeypatch):
        """Run the feed briefly and confirm candles are emitted to the builder."""
        monkeypatch.setenv("MOCK_SECONDS_PER_CANDLE", "0.01")
        cfg = make_app_config(exchange="mock")
        builder = MagicMock()
        feed = MockFeed(cfg)
        feed.subscribe("BTC/USDT", "5m", builder)

        import asyncio

        async def stopper():
            await asyncio.sleep(0.1)
            feed.stop()

        await asyncio.gather(feed.run(), stopper())
        # Should have generated several candles in 0.1s @ 0.01s each
        assert builder.process_ohlcv.call_count >= 3
        # Every call must have is_closed=True
        for call in builder.process_ohlcv.call_args_list:
            assert call.kwargs.get("is_closed") is True or call.args[-1] is True
