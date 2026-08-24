"""Unit tests for the host-disk watchdog's pure logic (scripts/monitor_host.py).

The 2026-07-20 incident (host disk 100% → fleet dead silently for 2.5 days)
motivated the watchdog; these tests pin its threshold classification and the
statvfs math so the alarm can't silently rot.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from monitor_host import (  # noqa: E402
    _CRIT_PCT,
    _FLEET_DEGRADED_RATIO,
    _WARN_PCT,
    classify,
    classify_fleet,
    disk_used_pct,
)


class TestClassify:
    def test_below_warn_is_ok(self):
        assert classify(_WARN_PCT - 0.1) == "OK"

    def test_at_warn_is_warn(self):
        assert classify(_WARN_PCT) == "WARN"

    def test_between_warn_and_crit_is_warn(self):
        assert classify((_WARN_PCT + _CRIT_PCT) / 2) == "WARN"

    def test_at_crit_is_critical(self):
        assert classify(_CRIT_PCT) == "CRITICAL"

    def test_full_disk_is_critical(self):
        assert classify(100.0) == "CRITICAL"

    def test_incident_level_92_pct_warns(self):
        # The observed pre-incident level (91-92%) must at least WARN.
        assert classify(92.0) == "WARN"


class TestClassifyFleet:
    """The 2026-08-09 recurrence: disk alarms fired for two weeks while every bot
    sat dead, because nothing watched liveness itself. These pin that probe."""

    def test_full_fleet_is_ok(self):
        assert classify_fleet(322, 336) == "OK"

    def test_zero_beating_is_critical(self):
        # The exact 2026-08-09 state: containers exited, heartbeats frozen.
        assert classify_fleet(0, 336) == "CRITICAL"

    def test_below_ratio_is_warn(self):
        assert classify_fleet(int(336 * _FLEET_DEGRADED_RATIO) - 1, 336) == "WARN"

    def test_at_ratio_is_ok(self):
        # Boundary: exactly the ratio is not yet degraded (strict <).
        assert classify_fleet(int(336 * _FLEET_DEGRADED_RATIO) + 1, 336) == "OK"

    def test_one_dead_cohort_of_many_does_not_alarm(self):
        # Retiring a cohort must not page anyone — only a real collapse should.
        assert classify_fleet(300, 336) == "OK"

    def test_fresh_db_is_ok(self):
        # No bot has ever registered: nothing to be down, so not an outage.
        assert classify_fleet(0, 0) == "OK"

    def test_single_bot_down_is_critical(self):
        assert classify_fleet(0, 1) == "CRITICAL"


class TestDiskUsedPct:
    def test_matches_statvfs_math_for_root(self):
        got = disk_used_pct("/")
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        expected = (total - free) / total * 100.0
        assert abs(got - expected) < 1.0  # same instant, tiny drift allowed

    def test_bounded_0_to_100(self):
        got = disk_used_pct("/")
        assert 0.0 <= got <= 100.0
