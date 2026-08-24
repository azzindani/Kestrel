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
        rules = {r.table: r.days for r in load_rules({})}
        assert rules["candles"] == 90
        assert rules["signals"] == 60
        assert rules["events"] == 30

    def test_microstructure_default_is_generous(self):
        # Irreplaceable research data — no historical L2 feed exists to re-fetch it.
        rules = {r.table: r.days for r in load_rules({})}
        assert rules["microstructure"] >= 365

    def test_env_widens_retention(self):
        # Widening capacity must be a config change, not a code change.
        rules = {r.table: r.days for r in load_rules({"CANDLES_RETENTION_DAYS": "365"})}
        assert rules["candles"] == 365

    def test_zero_disables_that_table(self):
        rules = {r.table: r.days for r in load_rules({"MICROSTRUCTURE_RETENTION_DAYS": "0"})}
        assert rules["microstructure"] == 0

    def test_candles_rule_guards_trade_context_links(self):
        # §19: a candle referenced by trade_context is training data, kept forever.
        candles = next(r for r in load_rules({}) if r.table == "candles")
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
