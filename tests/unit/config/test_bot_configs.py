"""Unit tests for load_bot_configs() in src/config.py."""

from __future__ import annotations

import json
import os

import pytest

from src.config import load_bot_configs
from tests.helpers.factories import make_app_config


def _write_bots(entries: list, tmp_dir: str) -> str:
    path = os.path.join(tmp_dir, "bots.json")
    with open(path, "w") as fh:
        json.dump(entries, fh)
    return path


# ---------------------------------------------------------------------------
# Single-bot fallback
# ---------------------------------------------------------------------------


class TestSingleBotFallback:
    def test_missing_file_returns_base(self, tmp_path):
        base = make_app_config()
        result = load_bot_configs(str(tmp_path / "missing.json"), base)
        assert result == [base]

    def test_empty_list_returns_base(self, tmp_path):
        path = _write_bots([], str(tmp_path))
        base = make_app_config()
        result = load_bot_configs(path, base)
        assert result == [base]

    def test_single_entry_returns_one_config(self, tmp_path):
        path = _write_bots([{"bot_id": "dev-BTCUSDT-5m-01", "pair": "BTCUSDT"}], str(tmp_path))
        result = load_bot_configs(path, make_app_config())
        assert len(result) == 1

    def test_single_entry_bot_id_overrides_base(self, tmp_path):
        path = _write_bots([{"bot_id": "dev-BTCUSDT-5m-01", "pair": "BTCUSDT"}], str(tmp_path))
        base = make_app_config(bot_id="base-bot")
        result = load_bot_configs(path, base)
        assert result[0].bot_id == "dev-BTCUSDT-5m-01"

    def test_single_entry_pair_overrides_base(self, tmp_path):
        path = _write_bots([{"bot_id": "dev-BTCUSDT-5m-01", "pair": "BTCUSDT"}], str(tmp_path))
        base = make_app_config(pair="ETHUSDT")
        result = load_bot_configs(path, base)
        assert result[0].pair == "BTCUSDT"


# ---------------------------------------------------------------------------
# Multi-bot
# ---------------------------------------------------------------------------


class TestMultiBot:
    def test_two_entries_returns_two_configs(self, tmp_path):
        path = _write_bots(
            [
                {"bot_id": "dev-BTCUSDT-5m-01", "pair": "BTCUSDT"},
                {"bot_id": "dev-ETHUSDT-5m-01", "pair": "ETHUSDT"},
            ],
            str(tmp_path),
        )
        result = load_bot_configs(path, make_app_config())
        assert len(result) == 2

    def test_each_entry_has_correct_pair(self, tmp_path):
        path = _write_bots(
            [
                {"bot_id": "dev-BTCUSDT-5m-01", "pair": "BTCUSDT"},
                {"bot_id": "dev-ETHUSDT-5m-01", "pair": "ETHUSDT"},
            ],
            str(tmp_path),
        )
        result = load_bot_configs(path, make_app_config())
        assert result[0].pair == "BTCUSDT"
        assert result[1].pair == "ETHUSDT"

    def test_shared_fields_inherited_from_base(self, tmp_path):
        path = _write_bots([{"bot_id": "dev-BTCUSDT-5m-01", "pair": "BTCUSDT"}], str(tmp_path))
        base = make_app_config(leverage=25, api_key="mykey")
        result = load_bot_configs(path, base)
        assert result[0].leverage == 25
        assert result[0].api_key == "mykey"

    def test_timeframe_entry_override(self, tmp_path):
        path = _write_bots(
            [{"bot_id": "dev-BTCUSDT-1m-01", "pair": "BTCUSDT", "timeframe_entry": "1m"}],
            str(tmp_path),
        )
        base = make_app_config(timeframe_entry="5m")
        result = load_bot_configs(path, base)
        assert result[0].timeframe_entry == "1m"

    def test_timeframe_entry_defaults_to_base(self, tmp_path):
        path = _write_bots([{"bot_id": "dev-BTCUSDT-5m-01", "pair": "BTCUSDT"}], str(tmp_path))
        base = make_app_config(timeframe_entry="15m")
        result = load_bot_configs(path, base)
        assert result[0].timeframe_entry == "15m"

    def test_max_active_buckets_override(self, tmp_path):
        path = _write_bots([{"bot_id": "b", "pair": "BTCUSDT", "max_active_buckets": 3}], str(tmp_path))
        base = make_app_config(max_active_buckets=1)
        result = load_bot_configs(path, base)
        assert result[0].max_active_buckets == 3

    def test_max_active_buckets_defaults_to_base(self, tmp_path):
        path = _write_bots([{"bot_id": "b", "pair": "BTCUSDT"}], str(tmp_path))
        base = make_app_config(max_active_buckets=2)
        result = load_bot_configs(path, base)
        assert result[0].max_active_buckets == 2

    def test_five_bots(self, tmp_path):
        entries = [{"bot_id": f"dev-BOT{i}-5m-01", "pair": f"PAIR{i}"} for i in range(5)]
        path = _write_bots(entries, str(tmp_path))
        result = load_bot_configs(path, make_app_config())
        assert len(result) == 5
        assert [r.pair for r in result] == [f"PAIR{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_bot_id_raises_value_error(self, tmp_path):
        path = _write_bots([{"pair": "BTCUSDT"}], str(tmp_path))
        with pytest.raises(ValueError, match="bot_id"):
            load_bot_configs(path, make_app_config())

    def test_missing_pair_raises_value_error(self, tmp_path):
        path = _write_bots([{"bot_id": "dev-BTCUSDT-5m-01"}], str(tmp_path))
        with pytest.raises(ValueError, match="pair"):
            load_bot_configs(path, make_app_config())

    def test_empty_bot_id_raises_value_error(self, tmp_path):
        path = _write_bots([{"bot_id": "", "pair": "BTCUSDT"}], str(tmp_path))
        with pytest.raises(ValueError):
            load_bot_configs(path, make_app_config())

    def test_configs_are_frozen_dataclasses(self, tmp_path):
        path = _write_bots([{"bot_id": "dev-BTCUSDT-5m-01", "pair": "BTCUSDT"}], str(tmp_path))
        result = load_bot_configs(path, make_app_config())
        with pytest.raises((AttributeError, TypeError)):
            result[0].pair = "MODIFIED"  # type: ignore[misc]
