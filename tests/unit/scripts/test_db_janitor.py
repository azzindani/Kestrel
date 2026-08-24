"""Unit tests for the retention janitor's pure logic (scripts/db_janitor.py).

Context: the §15 cleanup.sh retention contract was never scheduled (no crontab),
so retention had never run once — candles held 145 days against a 90-day policy
and microstructure grew unbounded. db_janitor.py is the missing scheduler.

What matters to lock down here is the part that DELETES: the cutoff math, the
0-means-disabled switch, and the guard that keeps trade_context-linked candles
(labelled training data, §19) out of the delete predicate forever. The actual
DELETE/VACUUM are I/O and are not exercised here.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from db_janitor import _MS_PER_DAY, cutoff_ms, load_rules, seconds_until_hour  # noqa: E402


class TestCutoffMs:
    def test_zero_days_is_now(self):
        assert cutoff_ms(1_700_000_000_000, 0) == 1_700_000_000_000

    def test_ninety_days_back(self):
        now = 1_700_000_000_000
        assert cutoff_ms(now, 90) == now - 90 * _MS_PER_DAY

    def test_is_strictly_in_the_past(self):
        now = 1_700_000_000_000
        assert cutoff_ms(now, 30) < now


class TestLoadRules:
    def test_defaults_match_section_19_policy(self):
        rules = {r.label: r.days for r in load_rules({})}
        assert rules["candles"] == 90
        assert rules["signals"] == 60
        assert rules["events"] == 30

    def test_microstructure_default_is_generous(self):
        # Irreplaceable research data — no historical L2 feed exists to re-fetch it.
        rules = {r.label: r.days for r in load_rules({})}
        assert rules["microstructure"] >= 365

    def test_env_widens_retention(self):
        # Widening capacity must be a config change, not a code change.
        rules = {r.label: r.days for r in load_rules({"CANDLES_RETENTION_DAYS": "365"})}
        assert rules["candles"] == 365

    def test_zero_disables_that_table(self):
        rules = {r.label: r.days for r in load_rules({"MICROSTRUCTURE_RETENTION_DAYS": "0"})}
        assert rules["microstructure"] == 0

    def test_candles_rule_guards_trade_context_links(self):
        # §19: a candle referenced by trade_context is training data, kept forever.
        candles = next(r for r in load_rules({}) if r.label == "candles")
        assert candles.guard_sql is not None
        assert "trade_context" in candles.guard_sql
        assert "NOT EXISTS" in candles.guard_sql

    def test_protected_tables_are_never_swept(self):
        swept = {r.table for r in load_rules({})}
        # trades/trade_context/pattern_memory are indefinite (§19); heartbeats is the
        # liveness source the fleet alarm reads — deleting stale rows would hide an outage.
        assert swept.isdisjoint({"trades", "trade_context", "pattern_memory", "heartbeats"})


class TestSecondsUntilHour:
    def test_exactly_before_target(self):
        # 02:00 UTC -> 03:00 UTC is one hour away.
        assert seconds_until_hour(2 * 3600.0, 3) == 3600.0

    def test_just_after_target_wraps_to_tomorrow(self):
        # 03:00:01 must schedule ~24h out, not fire again immediately.
        got = seconds_until_hour(3 * 3600.0 + 1.0, 3)
        assert 86399.0 - 1 <= got <= 86399.0 + 1

    def test_exactly_on_target_wraps_a_full_day(self):
        # Guards against a busy-loop of repeated sweeps at the top of the hour.
        assert seconds_until_hour(3 * 3600.0, 3) == 86400.0

    def test_always_positive_across_the_day(self):
        for seconds_today in range(0, 86400, 907):
            assert 0 < seconds_until_hour(float(seconds_today), 3) <= 86400.0


class TestFastTimeframeRetention:
    """Fast-TF candles carry their own short window — the lever that lets `dev`
    keep expanding (owner 2026-08-24) without breaking the 10GB budget.

    Candles are stored PER bot_id, so N bots on one pair+timeframe store N copies.
    A 5m bot writes ~351 KB/day; held for the 90-day §19 window a 204-bot cohort
    would be ~6.4 GB on its own. Nothing reads that depth: bots warm up on ~120
    candles and algo_search fetches OHLCV from ccxt, not from this table.
    """

    def test_fast_window_is_much_shorter_than_slow(self):
        rules = {r.label: r.days for r in load_rules({})}
        assert rules["candles_fast"] < rules["candles"]

    def test_default_fast_window(self):
        rules = {r.label: r.days for r in load_rules({})}
        assert rules["candles_fast"] == 21

    def test_slow_window_still_section_19(self):
        # 1h bootstrap needs 720 candles ~= 30 days; the 90-day policy is untouched.
        rules = {r.label: r.days for r in load_rules({})}
        assert rules["candles"] == 90

    def test_both_rules_target_the_candles_table(self):
        candle_rules = [r for r in load_rules({}) if r.table == "candles"]
        assert len(candle_rules) == 2

    def test_windows_partition_the_table(self):
        # Every candle must fall under exactly one rule: IN (...) and NOT IN (...)
        # over the same list, so no row is swept twice and none is orphaned.
        fast = next(r for r in load_rules({}) if r.label == "candles_fast")
        slow = next(r for r in load_rules({}) if r.label == "candles")
        assert "timeframe IN (" in fast.filter_sql
        assert "timeframe NOT IN (" in slow.filter_sql
        tfs = fast.filter_sql.split("IN (")[1].rstrip(")")
        assert slow.filter_sql.split("NOT IN (")[1].rstrip(")") == tfs

    def test_fast_rule_still_protects_linked_candles(self):
        # The trade_context guard must apply to BOTH windows, not just the slow one.
        fast = next(r for r in load_rules({}) if r.label == "candles_fast")
        assert fast.guard_sql is not None and "trade_context" in fast.guard_sql

    def test_fast_list_is_configurable(self):
        fast = next(r for r in load_rules({"FAST_TF_LIST": "1m,5m"}) if r.label == "candles_fast")
        assert "'1m'" in fast.filter_sql and "'5m'" in fast.filter_sql
        assert "'15m'" not in fast.filter_sql

    def test_fast_window_is_configurable(self):
        rules = {r.label: r.days for r in load_rules({"CANDLES_FAST_RETENTION_DAYS": "45"})}
        assert rules["candles_fast"] == 45

    def test_zero_disables_fast_sweep(self):
        rules = {r.label: r.days for r in load_rules({"CANDLES_FAST_RETENTION_DAYS": "0"})}
        assert rules["candles_fast"] == 0
