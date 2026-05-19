"""Unit tests for src/execution/providers/alpaca.py (pure logic — no Alpaca API calls)."""

from __future__ import annotations

import pytest

from src.execution.providers import _REGISTRY, registered_providers
from src.execution.providers.alpaca import _qty, _trading_client


class TestAlpacaRegistration:
    def test_alpaca_registered_in_registry(self):
        assert "alpaca" in _REGISTRY

    def test_alpaca_in_registered_providers_list(self):
        assert "alpaca" in registered_providers()


class TestAlpacaQty:
    def test_qty_returns_string(self):
        result = _qty(10.0, 20, 83000.0)
        assert isinstance(result, str)

    def test_qty_basic_calculation(self):
        # size=10, lev=20 → notional=200; at price=100 → qty=2.0
        result = _qty(10.0, 20, 100.0)
        assert float(result) == pytest.approx(2.0, abs=1e-6)

    def test_qty_higher_price_lower_qty(self):
        cheap = float(_qty(10.0, 20, 100.0))
        expensive = float(_qty(10.0, 20, 1000.0))
        assert cheap > expensive

    def test_qty_higher_leverage_higher_qty(self):
        low = float(_qty(10.0, 10, 100.0))
        high = float(_qty(10.0, 20, 100.0))
        assert high > low

    def test_qty_fractional_precision(self):
        # BTC at 83000 with size=10, lev=20 → ~0.00241 BTC
        result = float(_qty(10.0, 20, 83000.0))
        assert result == pytest.approx(200.0 / 83000.0, rel=1e-6)

    def test_qty_eight_decimal_places(self):
        result = _qty(10.0, 20, 83000.0)
        # Should have up to 8 decimal places
        assert "." in result
        decimals = result.split(".")[1]
        assert len(decimals) <= 8


class TestAlpacaClientImportError:
    def test_trading_client_raises_on_missing_alpaca(self, monkeypatch):
        """Verify helpful ImportError when alpaca-py is not installed."""
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("alpaca"):
                raise ImportError("No module named 'alpaca'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        from tests.helpers.factories import make_app_config

        cfg = make_app_config(exchange="alpaca")

        with pytest.raises(ImportError, match="alpaca-py"):
            _trading_client(cfg)
