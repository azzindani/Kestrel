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


class TestTokenToPair:
    def test_usdt_token(self):
        assert promote._token_to_pair("ETHUSDT") == "ETH/USDT"

    def test_multichar_base(self):
        assert promote._token_to_pair("DOGEUSDT") == "DOGE/USDT"

    def test_usd_token(self):
        assert promote._token_to_pair("BTCUSD") == "BTC/USD"

    def test_unknown_quote_raises(self):
        with pytest.raises(ValueError):
            promote._token_to_pair("ETHEUR")


class TestParseManual:
    def test_basic(self):
        assert promote._parse_manual("ETHUSDT:triple_mom,DOGEUSDT:mom_adx") == [
            ("ETHUSDT", "triple_mom"),
            ("DOGEUSDT", "mom_adx"),
        ]

    def test_whitespace_and_case(self):
        assert promote._parse_manual(" ethusdt : triple_mom ") == [("ETHUSDT", "triple_mom")]

    def test_missing_colon_raises(self):
        with pytest.raises(ValueError):
            promote._parse_manual("ETHUSDT-triple_mom")


class TestBotEntry:
    def test_shape_and_prefix(self):
        e = promote._bot_entry("ETHUSDT", "triple_mom")
        assert e["bot_id"] == "staging-ETHUSDT-4h-triple_mom-01"
        assert e["pair"] == "ETH/USDT"
        assert e["timeframe_entry"] == "4h"
        assert e["timeframe_regime"] == "4h"
        assert e["strategy"] == "triple_mom"
        assert e["patterns"] == ["triple_mom"]

    def test_carries_validated_exit_profile(self):
        e = promote._bot_entry("DOGEUSDT", "mom_adx")
        # Must carry the exact exit profile the lab ships (the TestExitProfileInSyncWithLab
        # test pins promote._EXIT == build_momentum_lab._EXIT, so reference it here).
        assert e["params"] == dict(promote._EXIT)
        assert e["params"]["trailing_enabled"] is True


class TestExitProfileInSyncWithLab:
    def test_matches_build_momentum_lab(self):
        lab_spec = importlib.util.spec_from_file_location("build_momentum_lab", _PATH.parent / "build_momentum_lab.py")
        lab = importlib.util.module_from_spec(lab_spec)
        lab_spec.loader.exec_module(lab)
        assert promote._EXIT == lab._EXIT
