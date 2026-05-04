"""Unit tests for src/data/providers/alpaca.py (pure logic — no Alpaca API calls)."""

from __future__ import annotations

from src.data.providers import _REGISTRY, registered_feeds
from src.data.providers.alpaca import _alpaca_symbol


class TestAlpacaFeedRegistration:
    def test_alpaca_feed_registered(self):
        assert "alpaca" in _REGISTRY

    def test_alpaca_feed_in_registered_list(self):
        assert "alpaca" in registered_feeds()


class TestAlpacaSymbol:
    def test_crypto_pair_slash_removed(self):
        assert _alpaca_symbol("BTC/USD") == "BTCUSD"

    def test_eth_pair_slash_removed(self):
        assert _alpaca_symbol("ETH/USD") == "ETHUSD"

    def test_stock_symbol_unchanged(self):
        assert _alpaca_symbol("AAPL") == "AAPL"

    def test_sol_pair(self):
        assert _alpaca_symbol("SOL/USD") == "SOLUSD"
