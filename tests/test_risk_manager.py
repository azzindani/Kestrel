"""Unit tests for src/risk/manager.py"""
import time
import pytest

from src.config import AppConfig, BucketState, Direction, Env, Signal
from src.risk.manager import validate


def _cfg(**kwargs) -> AppConfig:
    defaults = {
        "env": Env.DEV, "bot_id": "test", "exchange": "binance",
        "api_key": "k", "api_secret": "s", "testnet": True,
        "db_host": "localhost", "db_port": 5432, "db_name": "kestrel",
        "db_user": "u", "db_password": "p",
        "pair": "BTCUSDT", "timeframe_entry": "5m", "timeframe_regime": "15m",
        "leverage": 20, "bucket_size_usdt": 10.0, "max_active_buckets": 1,
        "telegram_token": "t", "telegram_chat_id": "c", "log_level": "DEBUG",
    }
    defaults.update(kwargs)
    return AppConfig(**defaults)


def _signal(
    entry: float = 83421.0,
    tp: float = None,
    sl: float = None,
    direction: Direction = Direction.LONG,
) -> Signal:
    # Default: ATR ~210, tp = entry + 1.6*210, sl = entry - 1.0*210
    tp = tp or entry + 336.0
    sl = sl or entry - 210.0
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
        entry_price=entry, tp_price=tp, sl_price=sl,
        size_usdt=10.0,
    )


def _state(**kwargs) -> BucketState:
    defaults = {
        "active_positions": 0,
        "last_ws_reconnect_ts": None,
        "session_net_pnl": 0.0,
        "current_ts": int(time.time() * 1000),
    }
    defaults.update(kwargs)
    return BucketState(**defaults)


class TestRiskManager:
    def test_valid_signal_passes(self):
        result = validate(_signal(), _state(), _cfg())
        assert result.passed is True
        assert result.reason is None

    def test_bucket_limit_rejected(self):
        result = validate(_signal(), _state(active_positions=1), _cfg())
        assert result.passed is False
        assert result.reason == "bucket_limit"

    def test_rr_below_minimum_rejected(self):
        # tp_dist = 100, sl_dist = 500 → R/R = 0.2 < 1.2
        sig = _signal(entry=1000.0, tp=1100.0, sl=500.0)
        result = validate(sig, _state(), _cfg())
        assert result.passed is False
        assert result.reason == "rr_below_minimum"

    def test_daily_loss_limit_rejected(self):
        result = validate(_signal(), _state(session_net_pnl=-5.01), _cfg())
        assert result.passed is False
        assert result.reason == "daily_loss_limit"

    def test_stale_data_rejected(self):
        # reconnect only 30s ago → stale
        recent_reconnect = int(time.time() * 1000) - 30_000
        current_ts = int(time.time() * 1000)
        result = validate(
            _signal(), _state(last_ws_reconnect_ts=recent_reconnect, current_ts=current_ts), _cfg()
        )
        assert result.passed is False
        assert result.reason == "stale_data"

    def test_stale_data_passes_after_60s(self):
        old_reconnect = int(time.time() * 1000) - 65_000
        current_ts = int(time.time() * 1000)
        result = validate(
            _signal(), _state(last_ws_reconnect_ts=old_reconnect, current_ts=current_ts), _cfg()
        )
        assert result.passed is True

    def test_never_reconnected_passes(self):
        result = validate(_signal(), _state(last_ws_reconnect_ts=None), _cfg())
        assert result.passed is True
