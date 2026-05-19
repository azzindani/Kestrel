"""Unit tests for src/execution/providers/ibkr.py (pure logic — no IB connection)."""

from __future__ import annotations

import pytest

from src.execution.providers import _REGISTRY, registered_providers
from src.execution.providers.ibkr import _parse_host_port, _qty


class TestIBKRRegistration:
    def test_ibkr_registered_in_registry(self):
        assert "ibkr" in _REGISTRY

    def test_ibkr_in_registered_providers_list(self):
        assert "ibkr" in registered_providers()


class TestParseHostPort:
    def test_parse_host_and_port(self):
        host, port = _parse_host_port("127.0.0.1:7497")
        assert host == "127.0.0.1"
        assert port == 7497

    def test_parse_default_port_when_no_colon(self):
        host, port = _parse_host_port("127.0.0.1")
        assert host == "127.0.0.1"
        assert port == 7497

    def test_parse_gateway_live_port(self):
        _, port = _parse_host_port("127.0.0.1:4001")
        assert port == 4001

    def test_parse_gateway_paper_port(self):
        _, port = _parse_host_port("127.0.0.1:4002")
        assert port == 4002

    def test_parse_tws_paper_port(self):
        _, port = _parse_host_port("127.0.0.1:7496")
        assert port == 7496

    def test_parse_remote_host(self):
        host, port = _parse_host_port("192.168.1.10:7497")
        assert host == "192.168.1.10"
        assert port == 7497


class TestIBKRQty:
    def test_qty_basic_calculation(self):
        # size=10, lev=20 → notional=200; at price=100 → qty=2.0
        result = _qty(10.0, 20, 100.0)
        assert result == pytest.approx(2.0, abs=1e-8)

    def test_qty_higher_price_lower_qty(self):
        assert _qty(10.0, 20, 100.0) > _qty(10.0, 20, 1000.0)

    def test_qty_higher_leverage_higher_qty(self):
        assert _qty(10.0, 20, 100.0) > _qty(10.0, 10, 100.0)

    def test_qty_returns_float(self):
        assert isinstance(_qty(10.0, 20, 83000.0), float)


class TestIBKRMakeContract:
    def test_make_contract_crypto_btc(self):
        """BTC.USD resolves to a Crypto contract (if ib_insync installed)."""
        pytest.importorskip("ib_insync")
        from ib_insync import Crypto

        from src.execution.providers.ibkr import _make_contract

        contract = _make_contract("BTC.USD")
        assert isinstance(contract, Crypto)
        assert contract.symbol == "BTC"

    def test_make_contract_forex_eur(self):
        pytest.importorskip("ib_insync")
        from ib_insync import Forex

        from src.execution.providers.ibkr import _make_contract

        contract = _make_contract("EUR.USD")
        assert isinstance(contract, Forex)

    def test_make_contract_stock_aapl(self):
        pytest.importorskip("ib_insync")
        from ib_insync import Stock

        from src.execution.providers.ibkr import _make_contract

        contract = _make_contract("AAPL.USD")
        assert isinstance(contract, Stock)
        assert contract.symbol == "AAPL"

    def test_make_contract_no_dot_defaults_to_stock(self):
        pytest.importorskip("ib_insync")
        from ib_insync import Stock

        from src.execution.providers.ibkr import _make_contract

        contract = _make_contract("TSLA")
        assert isinstance(contract, Stock)
