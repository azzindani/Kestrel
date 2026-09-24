"""Pure-logic tests for scripts/tier_promoter.py (no DB, no docker).

Loaded by file path; the scripts dir goes on sys.path first because the module
imports its sibling scripts (build_curated_tiers, retired_ledger) the way it does
when run as `python scripts/tier_promoter.py`.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

_SCRIPTS = pathlib.Path(__file__).parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("tier_promoter", _SCRIPTS / "tier_promoter.py")
tp = importlib.util.module_from_spec(_spec)
sys.modules["tier_promoter"] = tp  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(tp)

HW33 = {
    "tp_atr_multiplier": 0.5,
    "sl_atr_multiplier": 1.5,
    "max_hold_candles": 6,
    "trailing_enabled": False,
    "volume_ratio_min": 1.1,
    "max_loss_pct_per_trade": 0.01,
}
DAY = 86_400_000
NOW = 100 * DAY


def _bot(env, sym, label, pattern="tsmom_z", tf="5m", params=None):
    return {
        "bot_id": f"{env}-{sym}USDT-{tf}-{label}-01",
        "pair": f"{sym}/USDT",
        "timeframe_entry": tf,
        "timeframe_regime": tf,
        "max_active_buckets": 1,
        "strategy": label,
        "patterns": [pattern],
        "params": dict(HW33 if params is None else params),
    }


def _samples(n, skill, start=NOW - 5 * DAY, noise=1.0):
    """n samples whose skill alternates skill±noise (mean = skill, t grows with n)."""
    return [
        tp.Sample(
            entry_ts=start + i * 60_000, skill_bps=skill + (noise if i % 2 else -noise), gross_bps=skill, net_bps=0
        )
        for i in range(n)
    ]


def _recipe(pattern="tsmom_z"):
    return tp.recipe_key(_bot("dev", "BTC", "hw33_" + pattern, pattern))


class TestWalkExit:
    def test_long_take_profit_fills_at_that_close(self):
        assert tp.walk_exit([100, 100.2, 100.6], 0, 1, 0.5, 1.5, 6) == pytest.approx(60.0)

    def test_long_stop_loss(self):
        assert round(tp.walk_exit([100, 99.0, 98.4], 0, 1, 0.5, 1.5, 6), 6) == -160.0

    def test_short_mirror(self):
        assert tp.walk_exit([100, 99.4], 0, -1, 0.5, 1.5, 6) == pytest.approx(60.0)

    def test_timeout_on_max_hold_close(self):
        assert round(tp.walk_exit([100, 100.1, 100.2, 100.3], 0, 1, 1.0, 1.0, 3), 6) == 30.0

    def test_unresolved_when_series_ends(self):
        assert tp.walk_exit([100, 100.1], 0, 1, 1.0, 1.0, 3) is None


class TestPairedSkill:
    def test_uptrend_rewards_long_and_penalises_short_symmetrically(self):
        closes = [100 + 0.3 * i for i in range(10)]
        long_skill = tp.paired_skill(closes, 1.0, 0, 1, HW33)
        short_skill = tp.paired_skill(closes, 1.0, 0, -1, HW33)
        assert long_skill > 0
        assert short_skill == -long_skill

    def test_none_when_either_side_unresolved(self):
        assert tp.paired_skill([100, 100.1], 1.0, 0, 1, HW33) is None


class TestEntryIndex:
    def test_latest_closed_candle(self):
        ts = [0, 300_000, 600_000]
        assert tp.entry_index(ts, 300_000, 636_000) == 1  # candle 1 closed at 600k, candle 2 still open

    def test_none_before_first_close(self):
        assert tp.entry_index([0], 300_000, 100) is None


class TestSummarize:
    def test_mean_t_and_halves(self):
        ev = tp.summarize(_samples(200, 3.0))
        assert ev.n == 200
        assert round(ev.skill_bps, 9) == 3.0
        assert ev.skill_t > 20
        assert ev.skill_h1 > 0 and ev.skill_h2 > 0

    def test_single_sample_has_zero_t(self):
        assert tp.summarize(_samples(1, 5.0)).skill_t == 0.0


class TestRecipeKey:
    def test_dev_and_curated_labels_share_a_recipe(self):
        assert tp.recipe_key(_bot("dev", "BTC", "hw33_x")) == tp.recipe_key(_bot("lab", "ETH", "cur_x"))

    def test_hours_timeframe_out_of_scope(self):
        assert tp.recipe_key(_bot("dev", "BTC", "x", tf="1h")) is None

    def test_trailing_and_indicator_exits_out_of_scope(self):
        assert tp.recipe_key(_bot("dev", "BTC", "x", params={**HW33, "trailing_enabled": True})) is None
        assert tp.recipe_key(_bot("dev", "BTC", "x", params={**HW33, "tp_atr_multiplier": 50.0})) is None

    def test_bot_without_params_out_of_scope(self):
        bot = _bot("lab", "BTC", "mom_adx")
        del bot["params"]
        assert tp.recipe_key(bot) is None

    def test_extra_param_makes_a_distinct_recipe(self):
        a = tp.recipe_key(_bot("dev", "BTC", "x", pattern="xs_rev"))
        b = tp.recipe_key(_bot("dev", "BTC", "x", pattern="xs_rev", params={**HW33, "xs_lookback": 6}))
        assert a != b


class TestLabelsAndPairs:
    def test_existing_curated_label_wins(self):
        tiers = {"dev": [_bot("dev", "BTC", "hw33_tsmom_z")], "lab": [_bot("lab", "BTC", "cur_tsm")], "staging": []}
        assert tp.tier_label(_recipe(), tiers) == "cur_tsm"

    def test_derived_from_dev_label(self):
        tiers = {"dev": [_bot("dev", "BTC", "hw33_tsmom_z")], "lab": [], "staging": []}
        assert tp.tier_label(_recipe(), tiers) == "cur_tsmom_z"

    def test_target_pairs_prefers_order_and_stays_inside_available(self):
        assert tp.target_pairs(["ADA", "BTC", "ZEC"], ["BTC", "ETH", "ADA"], 2) == ["BTC", "ADA"]
        assert tp.target_pairs(["ADA", "BTC", "ZEC"], ["ETH"], 5) == ["ADA", "BTC", "ZEC"]


def _decide(tiers, samples, ledger=None, retired=frozenset()):
    labels = {r: "cur_" + json.loads(r)["pattern"] for t in tiers.values() for r in t}
    return tp.decide(tiers, samples, ledger or {}, labels, set(retired), NOW)


class TestDecide:
    def test_strong_dev_recipe_promoted_to_lab(self):
        r = _recipe()
        moves = _decide({"dev": {r}, "lab": set(), "staging": set()}, {r: _samples(200, 3.0)})
        assert [(m.kind, m.src, m.dst) for m in moves] == [("promote", "dev", "lab")]

    def test_small_sample_not_promoted(self):
        r = _recipe()
        assert _decide({"dev": {r}, "lab": set(), "staging": set()}, {r: _samples(tp.MIN_N - 1, 3.0)}) == []

    def test_halves_must_agree(self):
        r = _recipe()
        s = _samples(100, -1.0, noise=0.1) + _samples(100, 9.0, start=NOW - 2 * DAY, noise=0.1)
        assert _decide({"dev": {r}, "lab": set(), "staging": set()}, {r: s}) == []

    def test_wide_bracket_stays_in_dev(self):
        r = tp.recipe_key(_bot("dev", "BTC", "wide_ts", params={**HW33, "tp_atr_multiplier": 2.0}))
        assert not tp.high_win_design(r)
        assert _decide({"dev": {r}, "lab": set(), "staging": set()}, {r: _samples(200, 3.0)}) == []

    def test_retired_recipe_never_promoted(self):
        r = _recipe()
        assert _decide({"dev": {r}, "lab": set(), "staging": set()}, {r: _samples(200, 3.0)}, retired={r}) == []

    def test_weak_staging_recipe_falls_back_to_lab(self):
        r = _recipe()
        s = _samples(200, 0.05, noise=5.0)  # t ≈ 0.14: kept in lab (≥0), out of staging (<1)
        moves = _decide({"dev": {r}, "lab": set(), "staging": {r}}, {r: s})
        assert [(m.kind, m.src, m.dst) for m in moves] == [("demote", "staging", "lab")]

    def test_negative_staging_recipe_drops_to_dev(self):
        r = _recipe()
        moves = _decide({"dev": {r}, "lab": {r}, "staging": {r}}, {r: _samples(200, -2.0)})
        assert [(m.kind, m.src, m.dst) for m in moves] == [("demote", "staging", "dev")]

    def test_negative_lab_recipe_demoted(self):
        r = _recipe()
        moves = _decide({"dev": {r}, "lab": {r}, "staging": set()}, {r: _samples(200, -2.0)})
        assert [(m.kind, m.src, m.dst) for m in moves] == [("demote", "lab", "dev")]

    def test_lab_dwell_blocks_staging_promotion(self):
        r = _recipe()
        ledger = {r: tp.LedgerEntry(label="cur_tsmom_z", lab_since=NOW - 1 * DAY)}
        moves = _decide({"dev": {r}, "lab": {r}, "staging": set()}, {r: _samples(200, 3.0)}, ledger)
        assert moves == []

    def test_staging_promotion_uses_only_post_lab_evidence(self):
        r = _recipe()
        ledger = {r: tp.LedgerEntry(label="cur_tsmom_z", lab_since=NOW - 4 * DAY)}
        before = _samples(200, 3.0, start=NOW - 10 * DAY)
        assert _decide({"dev": {r}, "lab": {r}, "staging": set()}, {r: before}, ledger) == []
        after = before + _samples(200, 3.0, start=NOW - 3 * DAY)
        moves = _decide({"dev": {r}, "lab": {r}, "staging": set()}, {r: after}, ledger)
        assert [(m.kind, m.src, m.dst) for m in moves] == [("promote", "lab", "staging")]

    def test_cooldown_freezes_a_recipe(self):
        r = _recipe()
        ledger = {r: tp.LedgerEntry(label="cur_tsmom_z", last_move=NOW - 2 * DAY)}
        assert _decide({"dev": {r}, "lab": set(), "staging": set()}, {r: _samples(200, 3.0)}, ledger) == []


class TestApplyMoves:
    def _tiers(self):
        dev = [_bot("dev", s, "hw33_tsmom_z") for s in ["BTC", "ETH", "CHZ", "PEPE", "ZEC"]]
        return {"dev": dev, "lab": [_bot("lab", "BTC", "mom_adx", pattern="mom_adx")], "staging": []}

    def _move(self, kind, src, dst):
        ev = tp.summarize(_samples(10, 1.0))
        return tp.Move(_recipe(), "cur_tsmom_z", kind, src, dst, "test", ev)

    def test_promote_adds_lab_bots_on_preferred_pairs_only(self):
        out = tp.apply_moves(self._tiers(), [self._move("promote", "dev", "lab")], set())
        ids = [b["bot_id"] for b in out["lab"] if b["strategy"] == "cur_tsmom_z"]
        assert ids[:2] == ["lab-CHZUSDT-5m-cur_tsmom_z-01", "lab-PEPEUSDT-5m-cur_tsmom_z-01"]
        assert len(ids) == 5
        assert out["dev"] == self._tiers()["dev"]

    def test_demote_to_dev_clears_lab_and_staging_but_keeps_owner_bots(self):
        tiers = self._tiers()
        tiers["lab"] += [_bot("lab", "BTC", "cur_tsmom_z")]
        tiers["staging"] = [_bot("staging", "BTC", "cur_tsmom_z")]
        keep = {"lab-BTCUSDT-5m-mom_adx-01"}
        out = tp.apply_moves(tiers, [self._move("demote", "staging", "dev")], keep)
        assert out["staging"] == []
        assert [b["bot_id"] for b in out["lab"]] == ["lab-BTCUSDT-5m-mom_adx-01"]


class TestLedger:
    def test_promote_then_staging_demote_keeps_lab_since(self):
        r = _recipe()
        ev = tp.summarize(_samples(10, 1.0))
        led = tp.update_ledger({}, [tp.Move(r, "cur_x", "promote", "dev", "lab", "", ev)], 10)
        led = tp.update_ledger(led, [tp.Move(r, "cur_x", "promote", "lab", "staging", "", ev)], 20)
        led = tp.update_ledger(led, [tp.Move(r, "cur_x", "demote", "staging", "lab", "", ev)], 30)
        assert (led[r].lab_since, led[r].staging_since, led[r].last_move) == (10, None, 30)

    def test_seed_marks_incumbents_with_full_evidence(self):
        r = _recipe()
        led = tp.seed_ledger({}, {"dev": {r}, "lab": {r}, "staging": set()}, {r: "cur_x"})
        assert (led[r].lab_since, led[r].staging_since) == (0, None)


class TestScoreTrades:
    def test_groups_by_recipe_and_skips_unknown_bots(self):
        r = _recipe()
        ts = [i * 300_000 for i in range(12)]
        closes = [100 + 0.3 * i for i in range(12)]
        series = {("BTC/USDT", "5m"): (ts, closes, [1.0] * 12)}
        trade = {
            "bot_id": "dev-BTCUSDT-5m-hw33_tsmom_z-01",
            "pair": "BTC/USDT",
            "timeframe": "5m",
            "direction": "long",
            "entry_ts": 300_000 + 36_000,
            "notional_usdt": 200.0,
            "pnl_gross_usdt": 0.2,
            "pnl_net_usdt": 0.1,
        }
        stray = {**trade, "bot_id": "dev-BTCUSDT-5m-gone-01"}
        out = tp.score_trades([trade, stray], series, {trade["bot_id"]: r}, {r: HW33})
        assert list(out) == [r]
        (s,) = out[r]
        assert s.skill_bps > 0 and s.gross_bps == 10.0 and s.net_bps == 5.0
