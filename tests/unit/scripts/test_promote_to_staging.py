"""Pure-helper tests for scripts/promote_to_staging.py (no DB, no venue).

The script is loaded by file path so it needs no package wiring on sys.path.
Only the side-effect-free helpers are exercised; the DB leaderboard path is I/O.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).parents[3] / "scripts" / "promote_to_staging.py"
_spec = importlib.util.spec_from_file_location("promote_to_staging", _PATH)
promote = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(promote)


def _dev_bot(bot_id="dev-ETHUSDT-1h-macd_rsi-01", **over):
    b = {
        "bot_id": bot_id,
        "pair": "ETH/USDT",
        "timeframe_entry": "1h",
        "timeframe_regime": "1h",
        "max_active_buckets": 1,
        "strategy": "macd_rsi",
        "patterns": ["macd_rsi"],
        "params": {"tp_atr_multiplier": 2.0, "sl_atr_multiplier": 1.0, "max_hold_candles": 6},
    }
    b.update(over)
    return b


class TestParseBotId:
    def test_basic(self):
        assert promote._parse_bot_id("dev-BTCUSDT-1h-macd_rsi-01") == ("BTCUSDT", "1h", "macd_rsi")

    def test_underscore_strategy_kept_whole(self):
        assert promote._parse_bot_id("dev-ETHUSDT-5m-trend_momentum-01") == ("ETHUSDT", "5m", "trend_momentum")

    def test_short_id_raises(self):
        with pytest.raises(ValueError):
            promote._parse_bot_id("dev-bad")


class TestParseManual:
    def test_basic(self):
        assert promote._parse_manual("ETHUSDT:macd_rsi,DOGEUSDT:macd_cross") == [
            ("ETHUSDT", "macd_rsi"),
            ("DOGEUSDT", "macd_cross"),
        ]

    def test_whitespace_and_case(self):
        assert promote._parse_manual(" ethusdt : macd_rsi ") == [("ETHUSDT", "macd_rsi")]

    def test_missing_colon_raises(self):
        with pytest.raises(ValueError):
            promote._parse_manual("ETHUSDT-macd_rsi")


class TestStageClone:
    def test_prefix_swapped_and_config_preserved(self):
        dev = _dev_bot()
        st = promote._stage_clone(dev)
        assert st["bot_id"] == "staging-ETHUSDT-1h-macd_rsi-01"
        # Exit/params/tf/patterns come along verbatim — staging keeps the measured bracket.
        assert st["params"] == dev["params"]
        assert st["timeframe_entry"] == "1h"
        assert st["patterns"] == ["macd_rsi"]

    def test_does_not_mutate_source(self):
        dev = _dev_bot()
        promote._stage_clone(dev)
        assert dev["bot_id"] == "dev-ETHUSDT-1h-macd_rsi-01"


class TestLockboxSeed:
    def test_clones_only_lead_strategies_on_validated_pairs(self):
        bots = [
            _dev_bot("dev-ETHUSDT-1h-macd_rsi-01"),
            _dev_bot("dev-BTCUSDT-1h-macd_cross-01", strategy="macd_cross", patterns=["macd_cross"]),
            _dev_bot("dev-SOLUSDT-5m-trend_momentum-01", strategy="trend_momentum", patterns=["trend_momentum"]),
            # macd on a NON-validated (broadened forward-test) pair → excluded from the seed
            _dev_bot("dev-LINKUSDT-1h-macd_rsi-01", pair="LINK/USDT"),
        ]
        seed = promote._lockbox_seed(bots)
        ids = {b["bot_id"] for b in seed}
        assert ids == {"staging-ETHUSDT-1h-macd_rsi-01", "staging-BTCUSDT-1h-macd_cross-01"}


class TestLoadDevFleet:
    def test_indexes_by_tf_and_tf_agnostic(self, tmp_path):
        import json

        p = tmp_path / "bots.json"
        p.write_text(json.dumps([_dev_bot()]))
        bots, idx = promote._load_dev_fleet(str(p))
        assert len(bots) == 1
        assert ("ETHUSDT", "1h", "macd_rsi") in idx
        assert ("ETHUSDT", "macd_rsi") in idx  # tf-agnostic fallback for --manual
