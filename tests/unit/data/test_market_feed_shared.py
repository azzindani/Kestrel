"""Unit tests for the shared-feed semantics of MarketFeed + provider registry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.data.feed import MarketFeed
from src.data.providers import (
    _SHARED_EXCHANGES,
    _SHARED_INSTANCES,
    get_data_feed,
    reset_shared_feeds,
)
from tests.helpers.factories import make_app_config


@pytest.fixture(autouse=True)
def _clean_shared_feeds():
    reset_shared_feeds()
    yield
    reset_shared_feeds()


class TestSharedExchangeRegistration:
    def test_ccxt_exchanges_are_shared(self):
        for name in ("binance", "bybit", "okx", "kraken"):
            assert name in _SHARED_EXCHANGES

    def test_non_ccxt_providers_are_not_shared(self):
        for name in ("oanda", "alpaca", "ibkr"):
            assert name not in _SHARED_EXCHANGES


class TestSharedFeedInstance:
    def test_two_bots_same_exchange_get_same_feed(self):
        cfg_a = make_app_config(exchange="binance", bot_id="bot-a", pair="BTCUSDT")
        cfg_b = make_app_config(exchange="binance", bot_id="bot-b", pair="ETHUSDT")
        builder_a = MagicMock()
        builder_b = MagicMock()

        feed_a = get_data_feed(cfg_a, "BTCUSDT", "5m", builder_a)
        feed_b = get_data_feed(cfg_b, "ETHUSDT", "5m", builder_b)

        assert feed_a is feed_b
        assert isinstance(feed_a, MarketFeed)

    def test_subscriptions_accumulate_on_shared_feed(self):
        cfg_a = make_app_config(exchange="binance", bot_id="bot-a", pair="BTCUSDT")
        cfg_b = make_app_config(exchange="binance", bot_id="bot-b", pair="ETHUSDT")
        builder_a = MagicMock()
        builder_b = MagicMock()

        feed = get_data_feed(cfg_a, "BTCUSDT", "5m", builder_a)
        get_data_feed(cfg_b, "ETHUSDT", "5m", builder_b)

        subs = feed.subscriptions
        assert len(subs) == 2
        assert {s.pair for s in subs} == {"BTCUSDT", "ETHUSDT"}

    def test_reset_clears_shared_instances(self):
        cfg = make_app_config(exchange="binance", bot_id="bot-a", pair="BTCUSDT")
        get_data_feed(cfg, "BTCUSDT", "5m", MagicMock())
        assert "binance" in _SHARED_INSTANCES
        reset_shared_feeds()
        assert "binance" not in _SHARED_INSTANCES

    def test_different_exchanges_get_different_feeds(self):
        # binance and bybit are both shared but distinct exchanges
        cfg_a = make_app_config(exchange="binance", bot_id="bot-a", pair="BTCUSDT")
        cfg_b = make_app_config(exchange="bybit", bot_id="bot-b", pair="BTCUSDT")

        feed_a = get_data_feed(cfg_a, "BTCUSDT", "5m", MagicMock())
        feed_b = get_data_feed(cfg_b, "BTCUSDT", "5m", MagicMock())

        assert feed_a is not feed_b


class TestMarketFeedConstruction:
    def test_subscribe_records_subscription(self):
        cfg = make_app_config(exchange="binance")
        feed = MarketFeed(cfg)
        builder = MagicMock()

        feed.subscribe("BTCUSDT", "5m", builder)
        feed.subscribe("ETHUSDT", "5m", builder)

        subs = feed.subscriptions
        assert len(subs) == 2
        assert subs[0].pair == "BTCUSDT"
        assert subs[1].pair == "ETHUSDT"

    def test_subscribe_preserves_callbacks(self):
        cfg = make_app_config(exchange="binance")
        feed = MarketFeed(cfg)
        builder = MagicMock()
        on_reconnect = MagicMock()
        notify = MagicMock()

        feed.subscribe("BTCUSDT", "5m", builder, on_reconnect, notify)

        sub = feed.subscriptions[0]
        assert sub.on_reconnect is on_reconnect
        assert sub.notify is notify
        assert sub.builder is builder


class TestMultiBotFanout:
    """Regression: one WS stream per (pair, tf) must feed EVERY bot on that pair.

    The original bug fed only the first-subscribed bot per pair; the other
    strategies on the same pair never saw a candle close → never traded.
    """

    def test_dispatch_feeds_all_bots_on_a_pair(self):
        cfg = make_app_config(exchange="binance")
        feed = MarketFeed(cfg)
        b1, b2, other = MagicMock(), MagicMock(), MagicMock()
        feed.subscribe("BTCUSDT", "5m", b1)
        feed.subscribe("BTCUSDT", "5m", b2)  # second strategy, SAME pair
        feed.subscribe("ETHUSDT", "5m", other)

        row = [1_700_000_000_000, 100.0, 110.0, 95.0, 105.0, 5.0]
        feed._dispatch_ws("BTCUSDT", "5m", row, is_closed=True)

        b1.process_ohlcv.assert_called_once()
        b2.process_ohlcv.assert_called_once()  # would FAIL under the old per-sub bug
        other.process_ohlcv.assert_not_called()

    def test_reconnect_broadcasts_to_all_bots_on_a_pair(self):
        cfg = make_app_config(exchange="binance")
        feed = MarketFeed(cfg)
        r1, r2 = MagicMock(), MagicMock()
        feed.subscribe("BTCUSDT", "5m", MagicMock(), on_reconnect=r1)
        feed.subscribe("BTCUSDT", "5m", MagicMock(), on_reconnect=r2)

        for sub in feed._subs_for("BTCUSDT", "5m"):
            if sub.on_reconnect:
                sub.on_reconnect(1_700_000_000_000)

        r1.assert_called_once_with(1_700_000_000_000)
        r2.assert_called_once_with(1_700_000_000_000)
