"""Tests for per-bot bake-off strategy config (load_bot_configs overrides)."""

from __future__ import annotations

import json

import pytest

from src.config import AppConfig, load_bot_configs, load_params

_ENV = {
    "ENV": "dev",
    "BOT_ID": "dev-base-5m-01",
    "EXCHANGE": "mock",
    "API_KEY": "k",
    "API_SECRET": "s",
    "TESTNET": "true",
    "DB_HOST": "h",
    "DB_PORT": "5432",
    "DB_NAME": "n",
    "DB_USER": "u",
    "DB_PASSWORD": "p",
    "PAIR": "BTC/USDT",
    "TIMEFRAME_ENTRY": "5m",
    "TIMEFRAME_REGIME": "15m",
    "LEVERAGE": "20",
    "BUCKET_SIZE_USDT": "10",
    "MAX_ACTIVE_BUCKETS": "1",
    "TELEGRAM_TOKEN": "t",
    "TELEGRAM_CHAT_ID": "c",
    "LOG_LEVEL": "INFO",
}


def _base() -> AppConfig:
    return AppConfig.from_mapping(_ENV)


def _write(tmp_path, bots) -> str:
    p = tmp_path / "bots.json"
    p.write_text(json.dumps(bots))
    return str(p)


def test_per_bot_params_override_merges_onto_base(tmp_path):
    base, params = _base(), load_params("params.json")
    path = _write(
        tmp_path,
        [
            {
                "bot_id": "dev-BTCUSDT-5m-momwide-01",
                "pair": "BTC/USDT",
                "strategy": "momwide",
                "patterns": ["trend_momentum"],
                "params": {"tp_atr_multiplier": 2.4, "max_hold_candles": 8},
            }
        ],
    )
    (c,) = load_bot_configs(path, base, params)
    assert c.strategy == "momwide"
    assert c.enabled_patterns == ("trend_momentum",)
    assert c.params is not None
    assert c.params.tp_atr_multiplier == 2.4
    assert c.params.max_hold_candles == 8
    # untouched fields inherit the base params
    assert c.params.sl_atr_multiplier == params.sl_atr_multiplier


def test_strategy_without_param_override_carries_base_params(tmp_path):
    base, params = _base(), load_params("params.json")
    path = _write(
        tmp_path,
        [
            {
                "bot_id": "dev-BTCUSDT-5m-mom-01",
                "pair": "BTC/USDT",
                "strategy": "mom",
                "patterns": ["trend_momentum"],
            }
        ],
    )
    (c,) = load_bot_configs(path, base, params)
    assert c.enabled_patterns == ("trend_momentum",)
    assert c.params == params  # concrete base params, not None


def test_unknown_param_key_raises(tmp_path):
    base, params = _base(), load_params("params.json")
    path = _write(tmp_path, [{"bot_id": "b", "pair": "BTC/USDT", "params": {"not_a_param": 1}}])
    with pytest.raises(ValueError):
        load_bot_configs(path, base, params)


def test_plain_bot_defaults_to_global_strategy(tmp_path):
    base = _base()
    path = _write(tmp_path, [{"bot_id": "b", "pair": "BTC/USDT"}])
    (c,) = load_bot_configs(path, base)
    assert c.strategy == "default"
    assert c.enabled_patterns is None
    assert c.params is None
