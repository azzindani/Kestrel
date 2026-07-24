"""Unit tests for scripts/fetch_funding.py pure logic (rate lookup, no I/O)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from fetch_funding import rate_at  # noqa: E402

_H8 = 8 * 3_600_000
_EVENTS = [(0, 0.0001), (_H8, -0.0003), (2 * _H8, 0.0002)]


class TestRateAt:
    def test_before_history_returns_none(self):
        assert rate_at(_EVENTS, -1) is None

    def test_exact_event_ts(self):
        assert rate_at(_EVENTS, 0) == 0.0001
        assert rate_at(_EVENTS, _H8) == -0.0003

    def test_between_events_uses_most_recent(self):
        assert rate_at(_EVENTS, _H8 + 1) == -0.0003
        assert rate_at(_EVENTS, 2 * _H8 - 1) == -0.0003

    def test_after_last_event(self):
        assert rate_at(_EVENTS, 10 * _H8) == 0.0002

    def test_empty_events(self):
        assert rate_at([], 12345) is None

    def test_single_event(self):
        assert rate_at([(100, 0.0005)], 99) is None
        assert rate_at([(100, 0.0005)], 100) == 0.0005
        assert rate_at([(100, 0.0005)], 101) == 0.0005
