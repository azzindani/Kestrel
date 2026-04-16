"""Unit tests for src/execution/simulation.py"""
import asyncio
import pytest

from src.config import AppConfig, Direction, Env, Signal
from src.execution.simulation import SimulationExecution
from src.execution.interface import ExecutionError
import time


def _cfg() -> AppConfig:
    return AppConfig(
        env=Env.DEV, bot_id="test", exchange="binance",
        api_key="k", api_secret="s", testnet=True,
        db_host="localhost", db_port=5432, db_name="kestrel",
        db_user="u", db_password="p",
        pair="BTCUSDT", timeframe_entry="5m", timeframe_regime="15m",
        leverage=20, bucket_size_usdt=10.0, max_active_buckets=1,
        telegram_token="t", telegram_chat_id="c", log_level="DEBUG",
    )


def _signal(direction: Direction = Direction.LONG) -> Signal:
    entry = 83000.0
    return Signal(
        bot_id="test", session_id="s", env="dev",
        ts=int(time.time() * 1000),
        pair="BTCUSDT", timeframe="5m", candle_ts=0,
        pattern="impulse_retracement",
        direction=direction,
        confidence=0.75,
        regime="TRENDING",
        layer_regime=1, layer_trend=1, layer_momentum=1, layer_volume=1,
        layers_passed=4,
        entry_price=entry,
        tp_price=entry + 336.0 if direction is Direction.LONG else entry - 336.0,
        sl_price=entry - 210.0 if direction is Direction.LONG else entry + 210.0,
        size_usdt=10.0,
    )


class TestSimulationInit:
    def test_single_init_positions_empty(self):
        """Verify __init__ is not duplicated — both _positions and _prices initialise."""
        sim = SimulationExecution(_cfg())
        assert hasattr(sim, "_positions")
        assert hasattr(sim, "_prices")
        assert sim._positions == {}
        assert sim._prices == {}


class TestPlaceOrder:
    def test_place_long_order(self):
        sim = SimulationExecution(_cfg())
        order = asyncio.run(sim.place_order(_signal(Direction.LONG)))
        assert order["direction"] == "long"
        assert order["entry_price"] > 83000.0  # slippage added
        assert order["fee_usdt"] > 0
        assert order["liquidation_price"] < order["entry_price"]

    def test_place_short_order(self):
        sim = SimulationExecution(_cfg())
        order = asyncio.run(sim.place_order(_signal(Direction.SHORT)))
        assert order["direction"] == "short"
        assert order["entry_price"] < 83000.0  # slippage subtracted
        assert order["liquidation_price"] > order["entry_price"]

    def test_duplicate_position_raises(self):
        sim = SimulationExecution(_cfg())
        asyncio.run(sim.place_order(_signal()))
        with pytest.raises(ExecutionError):
            asyncio.run(sim.place_order(_signal()))


class TestClosePosition:
    def test_close_long_at_tp(self):
        sim = SimulationExecution(_cfg())
        sig = _signal(Direction.LONG)
        asyncio.run(sim.place_order(sig))
        sim.update_price("BTCUSDT", sig.tp_price + 1.0)
        result = asyncio.run(sim.close_position("BTCUSDT", "take_profit"))
        assert result["pnl_net_usdt"] > 0
        assert "BTCUSDT" not in sim._positions

    def test_close_long_at_sl(self):
        sim = SimulationExecution(_cfg())
        sig = _signal(Direction.LONG)
        asyncio.run(sim.place_order(sig))
        sim.update_price("BTCUSDT", sig.sl_price - 1.0)
        result = asyncio.run(sim.close_position("BTCUSDT", "stop_loss"))
        assert result["pnl_net_usdt"] < 0

    def test_close_nonexistent_raises(self):
        sim = SimulationExecution(_cfg())
        with pytest.raises(ExecutionError):
            asyncio.run(sim.close_position("BTCUSDT", "manual"))


class TestCheckExits:
    def test_tp_detected(self):
        sim = SimulationExecution(_cfg())
        sig = _signal(Direction.LONG)
        asyncio.run(sim.place_order(sig))
        sim.update_price("BTCUSDT", sig.tp_price + 10.0)
        assert sim.check_exits("BTCUSDT") == "take_profit"

    def test_sl_detected(self):
        sim = SimulationExecution(_cfg())
        sig = _signal(Direction.LONG)
        asyncio.run(sim.place_order(sig))
        sim.update_price("BTCUSDT", sig.sl_price - 10.0)
        assert sim.check_exits("BTCUSDT") == "stop_loss"

    def test_no_exit_in_range(self):
        sim = SimulationExecution(_cfg())
        sig = _signal(Direction.LONG)
        asyncio.run(sim.place_order(sig))
        sim.update_price("BTCUSDT", sig.entry_price + 10.0)
        assert sim.check_exits("BTCUSDT") is None

    def test_no_position_returns_none(self):
        sim = SimulationExecution(_cfg())
        assert sim.check_exits("BTCUSDT") is None
