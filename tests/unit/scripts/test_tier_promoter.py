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
SIGEXIT = {**HW33, "tp_atr_multiplier": 50.0, "max_hold_candles": 24, "indicator_exit_mode": "sigexit"}
DAY = 86_400_000
NOW = 100 * DAY  # midnight UTC, so day boundaries are exact


def _bot(env, sym, label, pattern="tsmom_z", tf="5m", params=None, **extra):
    return {
        "bot_id": f"{env}-{sym}USDT-{tf}-{label}-01",
        "pair": f"{sym}/USDT",
        "timeframe_entry": tf,
        "timeframe_regime": tf,
        "max_active_buckets": 1,
        "strategy": label,
        "patterns": [pattern],
        "params": dict(HW33 if params is None else params),
        **extra,
    }


def _samples(n, net, start=NOW - 5 * DAY, span_days=4.0, noise=1.0, skill=1.0):
    """n trades spread evenly over span_days; net alternates net±noise (mean = net)."""
    step = int(span_days * DAY / n)
    return [
        tp.Sample(
            entry_ts=start + i * step,
            skill_bps=skill,
            gross_bps=net + 5.0,
            net_bps=net + (noise if i % 2 else -noise),
        )
        for i in range(n)
    ]


def _days(samples, recipe="r"):
    return tp.aggregate({recipe: samples})[recipe]


def _recipe(pattern="tsmom_z", tf="5m", params=None, **extra):
    return tp.recipe_key(_bot("dev", "BTC", "hw33_" + pattern, pattern, tf=tf, params=params, **extra))


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


class TestEvidence:
    def test_net_mean_t_and_halves(self):
        ev = tp.summarize(_days(_samples(200, 3.0)), None, NOW)
        assert ev.n == 200
        assert ev.net_bps == pytest.approx(3.0)
        assert ev.net_t > 20
        assert ev.net_h1 > 0 and ev.net_h2 > 0
        assert ev.days >= 4

    def test_single_trade_has_zero_t(self):
        assert tp.summarize(_days(_samples(1, 5.0)), None, NOW).net_t == 0.0

    def test_skill_counts_only_replayed_trades(self):
        s = _samples(10, 1.0) + [tp.Sample(NOW - 2 * DAY, None, 0.0, 1.0)]
        ev = tp.summarize(_days(s), None, NOW)
        assert (ev.n, ev.skill_n) == (11, 10)

    def test_since_excludes_the_move_day_and_earlier(self):
        days = _days(_samples(40, 1.0, start=NOW - 6 * DAY, span_days=4.0))
        since = NOW - 4 * DAY + 3_600_000  # moved during day 96
        ev = tp.summarize(days, since, NOW)
        assert ev.n == sum(a.n for d, a in days.items() if d > tp.day_of(since))
        assert 0 < ev.n < 40


class TestMergeDays:
    def test_fuller_day_wins_so_a_reset_does_not_erase_history(self):
        before = _days(_samples(50, 1.0, start=NOW - 2 * DAY, span_days=1.0))
        after_reset = _days(_samples(5, -9.0, start=NOW - 2 * DAY, span_days=0.1))
        merged = tp.merge_days({"r": before}, {"r": after_reset}, "1970-01-01")["r"]
        assert merged == before

    def test_new_days_are_added_and_old_days_pruned(self):
        old = _days(_samples(10, 1.0, start=NOW - 30 * DAY, span_days=0.5))
        new = _days(_samples(10, 1.0, start=NOW - 1 * DAY, span_days=0.5))
        merged = tp.merge_days({"r": old}, {"r": new}, tp.day_of(NOW - 10 * DAY))["r"]
        assert set(merged) == set(new)


class TestRecipeKey:
    def test_dev_and_curated_labels_share_a_recipe(self):
        assert tp.recipe_key(_bot("dev", "BTC", "hw33_x")) == tp.recipe_key(_bot("lab", "ETH", "cur_x"))

    def test_every_timeframe_and_exit_is_in_scope(self):
        assert tp.recipe_key(_bot("dev", "BTC", "x", tf="1h")) is not None
        assert tp.recipe_key(_bot("dev", "BTC", "x", tf="4h", params=SIGEXIT)) is not None
        assert tp.recipe_key(_bot("dev", "BTC", "x", params={**HW33, "trailing_enabled": True})) is not None

    def test_bot_without_params_out_of_scope(self):
        bot = _bot("lab", "BTC", "mom_adx")
        del bot["params"]
        assert tp.recipe_key(bot) is None

    def test_extra_param_makes_a_distinct_recipe(self):
        a = tp.recipe_key(_bot("dev", "BTC", "x", pattern="xs_rev"))
        b = tp.recipe_key(_bot("dev", "BTC", "x", pattern="xs_rev", params={**HW33, "xs_lookback": 6}))
        assert a != b

    def test_leverage_is_part_of_the_recipe_only_when_set(self):
        plain = tp.recipe_key(_bot("dev", "BTC", "x"))
        assert "leverage" not in json.loads(plain)  # ledger keys from before stay valid
        assert tp.recipe_key(_bot("dev", "BTC", "x", leverage=3)) != plain


class TestRecipeTraits:
    def test_replayable_only_for_plain_brackets(self):
        assert tp.replayable(HW33)
        assert not tp.replayable(SIGEXIT)
        assert not tp.replayable({**HW33, "trailing_enabled": True})
        assert not tp.replayable({"tp_sl_pct_enabled": True, "tp_pct": 1.0, "sl_pct": 0.25, "max_hold_candles": 48})

    def test_min_n_scales_with_timeframe(self):
        assert [tp.min_n(_recipe(tf=tf)) for tf in ("5m", "15m", "1h", "4h")] == [150, 100, 60, 40]

    def test_cross_sectional(self):
        assert tp.cross_sectional(_recipe("xs_mom", tf="4h"))
        assert not tp.cross_sectional(_recipe())


class TestLabelsAndPairs:
    def test_existing_curated_label_wins(self):
        tiers = {"dev": [_bot("dev", "BTC", "hw33_tsmom_z")], "lab": [_bot("lab", "BTC", "cur_tsm")], "staging": []}
        assert tp.tier_label(_recipe(), tiers) == "cur_tsm"

    def test_derived_from_dev_label(self):
        tiers = {"dev": [_bot("dev", "BTC", "hw33_tsmom_z")], "lab": [], "staging": []}
        assert tp.tier_label(_recipe(), tiers) == "cur_tsmom_z"

    def test_non_5m_labels_carry_their_timeframe(self):
        tiers = {"dev": [_bot("dev", "BTC", "sma_cross", pattern="sma_cross", tf="1h")], "lab": [], "staging": []}
        assert tp.tier_label(_recipe("sma_cross", tf="1h"), tiers) == "cur_sma_cross_1h"

    def test_target_pairs_prefers_order_and_stays_inside_available(self):
        assert tp.target_pairs(["ADA", "BTC", "ZEC"], ["BTC", "ETH", "ADA"], 2) == ["BTC", "ADA"]
        assert tp.target_pairs(["ADA", "BTC", "ZEC"], ["ETH"], 5) == ["ADA", "BTC", "ZEC"]


def _decide(tiers, samples, ledger=None, retired=frozenset()):
    labels = {r: "cur_" + json.loads(r)["pattern"] for t in tiers.values() for r in t}
    evidence = tp.aggregate(samples)
    return tp.decide(tiers, evidence, ledger or {}, labels, set(retired), NOW)


def _only_dev(r):
    return {"dev": {r}, "lab": set(), "staging": set()}


class TestDecide:
    def test_profitable_dev_recipe_promoted_to_lab(self):
        r = _recipe()
        moves = _decide(_only_dev(r), {r: _samples(200, 3.0)})
        assert [(m.kind, m.src, m.dst) for m in moves] == [("promote", "dev", "lab")]

    def test_small_sample_not_promoted(self):
        r = _recipe()
        assert _decide(_only_dev(r), {r: _samples(tp.min_n(r) - 1, 3.0)}) == []

    def test_slower_timeframe_needs_fewer_trades(self):
        r = _recipe("sma_cross", tf="1h")
        moves = _decide(_only_dev(r), {r: _samples(70, 3.0)})
        assert [(m.kind, m.dst) for m in moves] == [("promote", "lab")]

    def test_halves_must_both_be_profitable(self):
        r = _recipe()
        s = _samples(100, -1.0, start=NOW - 6 * DAY, span_days=2.0, noise=0.1) + _samples(
            100, 9.0, start=NOW - 3 * DAY, span_days=2.0, noise=0.1
        )
        assert _decide(_only_dev(r), {r: s}) == []

    def test_wide_bracket_now_promotable(self):
        r = _recipe(params={**HW33, "tp_atr_multiplier": 2.0})
        assert [m.dst for m in _decide(_only_dev(r), {r: _samples(200, 3.0)})] == ["lab"]

    def test_drift_rider_blocked_when_bracket_is_replayable(self):
        r = _recipe()
        assert _decide(_only_dev(r), {r: _samples(200, 3.0, skill=-0.5)}) == []

    def test_unreplayable_exit_judged_on_net_alone(self):
        r = _recipe("sma_cross", tf="4h", params=SIGEXIT)
        s = [tp.Sample(x.entry_ts, None, x.gross_bps, x.net_bps) for x in _samples(60, 30.0, noise=10.0)]
        assert [m.dst for m in _decide(_only_dev(r), {r: s})] == ["lab"]

    def test_losing_recipe_not_promoted(self):
        r = _recipe()
        assert _decide(_only_dev(r), {r: _samples(200, -3.0)}) == []

    def test_retired_recipe_never_promoted(self):
        r = _recipe()
        assert _decide(_only_dev(r), {r: _samples(200, 3.0)}, retired={r}) == []

    def test_slightly_losing_staging_recipe_falls_back_to_lab(self):
        r = _recipe()
        s = _samples(200, -0.05, noise=5.0)  # net t ≈ −0.14: out of staging (<0), kept in lab (≥−1)
        moves = _decide({"dev": {r}, "lab": set(), "staging": {r}}, {r: s})
        assert [(m.kind, m.src, m.dst) for m in moves] == [("demote", "staging", "lab")]

    def test_clearly_losing_staging_recipe_drops_to_dev(self):
        r = _recipe()
        moves = _decide({"dev": {r}, "lab": {r}, "staging": {r}}, {r: _samples(200, -2.0)})
        assert [(m.kind, m.src, m.dst) for m in moves] == [("demote", "staging", "dev")]

    def test_losing_lab_recipe_demoted(self):
        r = _recipe()
        moves = _decide({"dev": {r}, "lab": {r}, "staging": set()}, {r: _samples(200, -2.0)})
        assert [(m.kind, m.src, m.dst) for m in moves] == [("demote", "lab", "dev")]

    def test_flat_lab_recipe_kept(self):
        r = _recipe()
        s = _samples(200, -0.05, noise=5.0)
        assert _decide({"dev": {r}, "lab": {r}, "staging": set()}, {r: s}) == []

    def test_lab_dwell_blocks_staging_promotion(self):
        r = _recipe()
        ledger = {r: tp.LedgerEntry(label="cur_tsmom_z", lab_since=NOW - 1 * DAY)}
        assert _decide({"dev": {r}, "lab": {r}, "staging": set()}, {r: _samples(200, 3.0)}, ledger) == []

    def test_staging_promotion_uses_only_post_lab_evidence(self):
        r = _recipe()
        ledger = {r: tp.LedgerEntry(label="cur_tsmom_z", lab_since=NOW - 4 * DAY)}
        before = _samples(200, 3.0, start=NOW - 10 * DAY)
        assert _decide({"dev": {r}, "lab": {r}, "staging": set()}, {r: before}, ledger) == []
        after = before + _samples(200, 3.0, start=NOW - 3 * DAY, span_days=2.5)
        moves = _decide({"dev": {r}, "lab": {r}, "staging": set()}, {r: after}, ledger)
        assert [(m.kind, m.src, m.dst) for m in moves] == [("promote", "lab", "staging")]

    def test_cooldown_freezes_a_recipe(self):
        r = _recipe()
        ledger = {r: tp.LedgerEntry(label="cur_tsmom_z", last_move=NOW - 2 * DAY)}
        assert _decide(_only_dev(r), {r: _samples(200, 3.0)}, ledger) == []


class TestScoreboard:
    def test_rows_name_tier_gate_and_blockers(self):
        good, bad = _recipe(), _recipe("bb_break")
        tiers = {"dev": {good, bad}, "lab": set(), "staging": set()}
        ev = tp.aggregate({good: _samples(200, 3.0), bad: _samples(20, -1.0)})
        labels = {good: "cur_good", bad: "cur_bad"}
        rows = {r["label"]: r for r in tp.scoreboard(tiers, ev, {}, labels, set(), [], NOW)}
        assert rows["cur_good"]["status"] == "eligible" and rows["cur_good"]["blockers"] == ""
        assert rows["cur_bad"]["status"] == "blocked"
        assert "n 20<150" in rows["cur_bad"]["blockers"]
        assert rows["cur_good"]["tf"] == "5m" and rows["cur_good"]["min_n"] == 150


class TestApplyMoves:
    def _tiers(self):
        dev = [_bot("dev", s, "hw33_tsmom_z") for s in ["BTC", "ETH", "CHZ", "PEPE", "ZEC"]]
        return {"dev": dev, "lab": [_bot("lab", "BTC", "mom_adx", pattern="mom_adx")], "staging": []}

    def _move(self, kind, src, dst, recipe=None, label="cur_tsmom_z"):
        ev = tp.summarize(_days(_samples(10, 1.0)), None, NOW)
        return tp.Move(recipe or _recipe(), label, kind, src, dst, "test", ev)

    def test_promote_adds_lab_bots_on_preferred_pairs_only(self):
        out = tp.apply_moves(self._tiers(), [self._move("promote", "dev", "lab")], set())
        ids = [b["bot_id"] for b in out["lab"] if b["strategy"] == "cur_tsmom_z"]
        assert ids[:2] == ["lab-CHZUSDT-5m-cur_tsmom_z-01", "lab-PEPEUSDT-5m-cur_tsmom_z-01"]
        assert len(ids) == 5
        assert out["dev"] == self._tiers()["dev"]

    def test_cross_sectional_recipe_takes_its_whole_universe_and_keeps_leverage(self):
        syms = [f"P{i:02d}" for i in range(20)]
        dev = [_bot("dev", s, "xsmom28", pattern="xs_mom", tf="4h", leverage=3) for s in syms]
        r = tp.recipe_key(dev[0])
        out = tp.apply_moves(
            {"dev": dev, "lab": [], "staging": []}, [self._move("promote", "dev", "lab", r, "x")], set()
        )
        assert len(out["lab"]) == 20
        assert {b["leverage"] for b in out["lab"]} == {3}
        assert all(tp.recipe_key(b) == r for b in out["lab"])

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
        ev = tp.summarize(_days(_samples(10, 1.0)), None, NOW)
        led = tp.update_ledger({}, [tp.Move(r, "cur_x", "promote", "dev", "lab", "", ev)], 10)
        led = tp.update_ledger(led, [tp.Move(r, "cur_x", "promote", "lab", "staging", "", ev)], 20)
        led = tp.update_ledger(led, [tp.Move(r, "cur_x", "demote", "staging", "lab", "", ev)], 30)
        assert (led[r].lab_since, led[r].staging_since, led[r].last_move) == (10, None, 30)

    def test_seed_marks_incumbents_with_full_evidence(self):
        r = _recipe()
        led = tp.seed_ledger({}, {"dev": {r}, "lab": {r}, "staging": set()}, {r: "cur_x"})
        assert (led[r].lab_since, led[r].staging_since) == (0, None)


class TestMoveEvents:
    _H = {"ts": 1_000_000, "kind": "demote", "label": "cur_x", "src": "lab", "dst": "dev"}

    def test_live_logged_move_seconds_later_is_not_duplicated(self):
        logged = [(1_030_000, "tier_demoted", "cur_x", "lab", "dev")]
        assert tp.missing_move_events([self._H], logged, 0) == []

    def test_wiped_move_is_restored(self):
        assert tp.missing_move_events([self._H], [], 0) == [self._H]

    def test_moves_older_than_retention_are_skipped(self):
        assert tp.missing_move_events([self._H], [], 2_000_000) == []


class TestScoreTrades:
    _TRADE = {
        "bot_id": "dev-BTCUSDT-5m-hw33_tsmom_z-01",
        "pair": "BTC/USDT",
        "timeframe": "5m",
        "direction": "long",
        "entry_ts": 300_000 + 36_000,
        "notional_usdt": 200.0,
        "pnl_gross_usdt": 0.2,
        "pnl_net_usdt": 0.1,
    }

    def _series(self):
        ts = [i * 300_000 for i in range(12)]
        return {("BTC/USDT", "5m"): (ts, [100 + 0.3 * i for i in range(12)], [1.0] * 12)}

    def test_groups_by_recipe_and_skips_unknown_bots(self):
        r = _recipe()
        stray = {**self._TRADE, "bot_id": "dev-BTCUSDT-5m-gone-01"}
        out = tp.score_trades([self._TRADE, stray], self._series(), {self._TRADE["bot_id"]: r})
        assert list(out) == [r]
        (s,) = out[r]
        assert s.skill_bps > 0 and s.gross_bps == 10.0 and s.net_bps == 5.0

    def test_unreplayable_recipe_keeps_net_without_skill(self):
        r = _recipe(params=SIGEXIT)
        (s,) = tp.score_trades([self._TRADE], self._series(), {self._TRADE["bot_id"]: r})[r]
        assert s.skill_bps is None and s.net_bps == 5.0
