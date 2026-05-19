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


class TestMockFeedFiresSignals:
    """End-to-end: mock candles must produce signals through the real algorithm.

    Locks in that the regime-aware mock + current params actually exercise the
    signal pipeline. If a refactor breaks the fire rate, this test catches it.
    """

    def test_thousand_candles_produce_at_least_one_signal(self):
        from src.config import Candle, compute_candle_geometry, load_params
        from src.data.providers.mock import MockFeed
        from src.signal.detector import evaluate
        from src.signal.indicators import compute_all_indicators

        cfg = make_app_config(exchange="mock")
        feed = MockFeed(cfg)
        feed.subscribe("BTC/USDT", "5m", MagicMock())
        sub = feed.subscriptions[0]

        # Generate 1000 candles with geometry, then enrich with indicators on
        # a rolling 120-candle window and run the full signal pipeline on each.
        raw: list[Candle] = []
        for i in range(1000):
            ohlcv = feed._next_candle(sub, 1_700_000_000_000 + i * 300_000)
            g = compute_candle_geometry(ohlcv[1], ohlcv[2], ohlcv[3], ohlcv[4])
            raw.append(
                Candle(
                    bot_id="t",
                    ts=ohlcv[0],
                    pair="BTC/USDT",
                    timeframe="5m",
                    open=ohlcv[1],
                    high=ohlcv[2],
                    low=ohlcv[3],
                    close=ohlcv[4],
                    volume=ohlcv[5],
                    body_size=g["body_size"],
                    total_range=g["total_range"],
                    body_ratio=g["body_ratio"],
                    upper_wick=g["upper_wick"],
                    lower_wick=g["lower_wick"],
                    direction=g["direction"],
                )
            )

        params = load_params("params.json")
        enriched: list[Candle] = []
        fires = 0
        for i in range(1000):
            window = raw[max(0, i - 119) : i + 1]
            if len(window) < 25:
                continue
            ind = compute_all_indicators(list(window), ema_fast=params.ema_fast, ema_slow=params.ema_slow)
            c = window[-1]
            enriched.append(
                Candle(
                    bot_id=c.bot_id,
                    ts=c.ts,
                    pair=c.pair,
                    timeframe=c.timeframe,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=c.volume,
                    ema9=ind.get("ema9"),
                    ema21=ind.get("ema21"),
                    rsi14=ind.get("rsi14"),
                    atr14=ind.get("atr14"),
                    bb_upper=ind.get("bb_upper"),
                    bb_lower=ind.get("bb_lower"),
                    bb_width=ind.get("bb_width"),
                    adx=ind.get("adx"),
                    volume_ma20=ind.get("volume_ma20"),
                    volume_ratio=ind.get("volume_ratio"),
                    body_size=c.body_size,
                    total_range=c.total_range,
                    body_ratio=c.body_ratio,
                    upper_wick=c.upper_wick,
                    lower_wick=c.lower_wick,
                    direction=c.direction,
                )
            )
            sig, _ = evaluate(enriched, params, "t", "s", "dev")
            if sig is not None:
                fires += 1

        # Empirically the regime-aware mock fires ~4 signals per 1000 candles
        # under default params. Require ≥1 so the dashboard sees activity.
        assert fires >= 1, "mock produced 0 fires across 1000 candles — pipeline broken?"
