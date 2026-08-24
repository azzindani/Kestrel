"""Unit tests for the high-win 5m fleet builder (scripts/build_hiwin_fleet.py).

The properties worth locking down are the ones that would quietly destroy work:
the merge must be ADDITIVE (the reset policy depends on existing bot_ids and
their forward-test history surviving a deploy untouched), bot_ids must stay
parseable by the dashboards' split_part(bot_id,'-',4), and the bracket must keep
the inverted geometry that produces the win rate while still clearing risk
Rule 3's 0.25 floor.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from build_hiwin_fleet import (  # noqa: E402
    _ARMS,
    _HIWIN33,
    _TIGHT,
    build_tier,
    make_bot,
    merge_additive,
)


class TestMakeBot:
    def test_bot_id_shape(self):
        bot = make_bot("dev", "BTC/USDT", "bb_break")
        assert bot["bot_id"] == "dev-BTCUSDT-5m-hw33_bb_break-01"

    def test_strategy_segment_is_parseable(self):
        # Dashboards read split_part(bot_id,'-',4) as the strategy label, so the
        # label itself must contain no dashes or the panels mis-group.
        bot = make_bot("dev", "BTC/USDT", "bb_break")
        assert bot["bot_id"].split("-")[3] == bot["strategy"]
        assert "-" not in bot["strategy"]

    def test_is_five_minute_on_both_timeframes(self):
        # §13 mandates minutes-candle hunting; the fleet had drifted to 100% 1h.
        bot = make_bot("dev", "ETH/USDT", "mom_adx")
        assert bot["timeframe_entry"] == "5m"
        assert bot["timeframe_regime"] == "5m"

    def test_pattern_matches_arm(self):
        bot = make_bot("lab", "SOL/USDT", "cci_mom")
        assert bot["patterns"] == ["cci_mom"]

    def test_env_prefixes_bot_id(self):
        for env in ("dev", "lab", "staging"):
            assert make_bot(env, "BTC/USDT", "bb_break")["bot_id"].startswith(f"{env}-")

    def test_params_are_copied_not_shared(self):
        # A shared dict would let one bot's mutation leak across the whole fleet.
        a = make_bot("dev", "BTC/USDT", "bb_break")
        b = make_bot("dev", "ETH/USDT", "bb_break")
        a["params"]["tp_atr_multiplier"] = 99.0
        assert b["params"]["tp_atr_multiplier"] == _HIWIN33["tp_atr_multiplier"]


class TestGeometry:
    def test_bracket_is_inverted(self):
        # tp < sl is the whole point: win rate ~ 1/(1+g), so only g < 1 reaches 70%.
        assert _HIWIN33["tp_atr_multiplier"] < _HIWIN33["sl_atr_multiplier"]

    def test_clears_risk_rule_3_floor(self):
        # v2.6 lowered the R/R floor to 0.25; below it the entry is rejected outright.
        ratio = _HIWIN33["tp_atr_multiplier"] / _HIWIN33["sl_atr_multiplier"]
        assert ratio >= 0.25

    def test_within_params_json_ranges(self):
        # params.json contract: tp [0.4,3.0], sl [0.5,2.0], max_hold [2,8].
        assert 0.4 <= _HIWIN33["tp_atr_multiplier"] <= 3.0
        assert 0.5 <= _HIWIN33["sl_atr_multiplier"] <= 2.0
        assert 2 <= _HIWIN33["max_hold_candles"] <= 8

    def test_trailing_disabled(self):
        # Trailing would truncate the small TP the geometry depends on.
        assert _HIWIN33["trailing_enabled"] is False


class TestBuildTier:
    def test_grid_size(self):
        got = build_tier("dev", ["BTC/USDT", "ETH/USDT"], ["bb_break", "mom_adx"])
        assert len(got) == 4

    def test_all_bot_ids_unique(self):
        got = build_tier("dev", ["BTC/USDT", "ETH/USDT", "SOL/USDT"], _ARMS)
        assert len({b["bot_id"] for b in got}) == len(got)

    def test_empty_pairs_yields_nothing(self):
        assert build_tier("dev", [], _ARMS) == []


class TestMergeAdditive:
    def test_appends_to_empty(self):
        new = build_tier("dev", ["BTC/USDT"], ["bb_break"])
        merged, added = merge_additive([], new)
        assert added == 1 and len(merged) == 1

    def test_preserves_existing_entries_verbatim(self):
        # The reset policy hinges on this: an additive deploy must not disturb a
        # single running bot, or its forward-test history is invalidated.
        existing = [{"bot_id": "dev-BTCUSDT-1h-macd_cross-01", "pair": "BTC/USDT"}]
        merged, _ = merge_additive(existing, build_tier("dev", ["BTC/USDT"], ["bb_break"]))
        assert merged[0] == existing[0]

    def test_skips_duplicate_bot_ids(self):
        new = build_tier("dev", ["BTC/USDT"], ["bb_break"])
        merged, added = merge_additive(list(new), new)
        assert added == 0 and len(merged) == 1

    def test_is_idempotent(self):
        new = build_tier("dev", ["BTC/USDT", "ETH/USDT"], _ARMS)
        once, _ = merge_additive([], new)
        twice, added = merge_additive(once, new)
        assert added == 0 and twice == once

    def test_partial_overlap_adds_only_the_new(self):
        first = build_tier("dev", ["BTC/USDT"], ["bb_break"])
        second = build_tier("dev", ["BTC/USDT", "ETH/USDT"], ["bb_break"])
        merged, added = merge_additive(first, second)
        assert added == 1 and len(merged) == 2

    def test_never_removes(self):
        existing = build_tier("dev", ["BTC/USDT"], ["mom_adx"])
        merged, _ = merge_additive(existing, build_tier("dev", ["ETH/USDT"], ["bb_break"]))
        assert all(b in merged for b in existing)


class TestVwmaCrossPreset:
    """vwma_cross rides the TIGHT bracket, not the inverted one.

    The measured edge is bracket-specific: +0.86/+0.90 bps across two eras on
    tight, versus -0.00/-2.29 on hiwin33. Pairing the entry with the wrong
    geometry discards the signal entirely, so this is worth pinning.
    """

    def test_bare_label_when_prefix_empty(self):
        # Baseline-cohort convention: strategy label is the arm name itself.
        bot = make_bot("dev", "BTC/USDT", "vwma_cross", "", _TIGHT)
        assert bot["strategy"] == "vwma_cross"
        assert bot["bot_id"] == "dev-BTCUSDT-5m-vwma_cross-01"
        assert bot["bot_id"].split("-")[3] == "vwma_cross"

    def test_uses_tight_bracket(self):
        bot = make_bot("dev", "BTC/USDT", "vwma_cross", "", _TIGHT)
        assert bot["params"]["tp_atr_multiplier"] == 1.4
        assert bot["params"]["sl_atr_multiplier"] == 1.0

    def test_tight_is_not_inverted(self):
        # The distinguishing property versus the hiwin family.
        assert _TIGHT["tp_atr_multiplier"] > _TIGHT["sl_atr_multiplier"]

    def test_tight_clears_risk_rule_3(self):
        assert _TIGHT["tp_atr_multiplier"] / _TIGHT["sl_atr_multiplier"] >= 0.25

    def test_default_prefix_still_hiwin(self):
        # The default path must be unchanged by the parameterisation.
        assert make_bot("dev", "BTC/USDT", "bb_break")["strategy"] == "hw33_bb_break"

    def test_default_bracket_still_hiwin33(self):
        assert make_bot("dev", "BTC/USDT", "bb_break")["params"] == dict(_HIWIN33)
