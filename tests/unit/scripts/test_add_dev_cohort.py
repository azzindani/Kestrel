"""Tests for the xs_mom additions to scripts/add_dev_cohort.py and scripts/bot_registry.py (iter 75).

Loaded by file path; the scripts dir goes on sys.path first because add_dev_cohort
imports its sibling build_momentum_lab the way it does when run as a script.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCRIPTS = _ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adc = _load("add_dev_cohort")
registry = _load("bot_registry")


class TestCohortLeverage:
    def test_leverage_is_written_only_when_requested(self):
        plain = adc.cohort("hw33_x", "turtle_soup", "hiwin33", ["BTC/USDT"], "5m")
        levered = adc.cohort("xsmom28", "xs_mom", "xsmom_notp", ["BTC/USDT"], "4h", leverage=3)
        assert "leverage" not in plain[0]
        assert levered[0]["leverage"] == 3

    def test_xsmom_presets_are_pct_brackets_with_the_group_exit(self):
        for name, tp in (("xsmom_notp", 1.0), ("xsmom_tp10", 0.10)):
            (bot,) = adc.cohort("x", "xs_mom", name, ["ETH/USDT"], "4h", leverage=3)
            p = bot["params"]
            assert p["tp_sl_pct_enabled"] is True and p["sl_pct"] == 0.25 and p["tp_pct"] == tp
            assert p["indicator_exit_mode"] == "sigexit" and p["max_hold_candles"] == 48
            assert bot["bot_id"] == "dev-ETHUSDT-4h-x-01"

    def test_preset_values_sit_inside_their_params_json_ranges(self):
        contract = json.loads((_ROOT / "params.json").read_text())
        for name in ("xsmom_notp", "xsmom_tp10"):
            for key, value in adc.BRACKETS[name].items():
                if key not in contract or isinstance(value, bool):
                    continue
                lo, hi = contract[key]["range"]
                assert lo <= value <= hi, (name, key)


class TestRegistryFingerprint:
    BOT = {"bot_id": "a", "pair": "BTC/USDT", "timeframe_entry": "4h", "strategy": "s", "patterns": ["xs_mom"]}

    def test_leverage_changes_the_fingerprint(self):
        plain = registry._fingerprint(registry._canonical(self.BOT))
        levered = registry._fingerprint(registry._canonical({**self.BOT, "leverage": 3}))
        assert plain != levered

    def test_bots_without_leverage_keep_their_old_fingerprint(self):
        assert "leverage" not in registry._canonical(self.BOT)
