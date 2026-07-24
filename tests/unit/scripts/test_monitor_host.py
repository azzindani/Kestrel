"""Unit tests for the host-disk watchdog's pure logic (scripts/monitor_host.py).

The 2026-07-20 incident (host disk 100% → fleet dead silently for 2.5 days)
motivated the watchdog; these tests pin its threshold classification and the
statvfs math so the alarm can't silently rot.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from monitor_host import _CRIT_PCT, _WARN_PCT, classify, disk_used_pct  # noqa: E402


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
