"""Unit tests for the pattern-memory rebuilder (scripts/rebuild_pattern_memory.py).

This connects the write half of a loop whose read half has been live all along, so
the failure mode to guard against is subtle: keys that never match. The detector
builds its lookup key from get_trading_session(...).value and
`candles[-1].regime or "UNKNOWN"`. If the rebuilder derived either differently it
would populate a table nobody can read — the same inert-loop bug in a new costume.

Also pinned: maintenance closes must never teach the detector anything, and the
aggregation must be idempotent, since the whole reason this is a rebuilder rather
than a close-trade hook is that recomputing from source cannot double-count.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from rebuild_pattern_memory import _EXCLUDED_REASONS, aggregate  # noqa: E402

from src.config import get_trading_session  # noqa: E402

_LONDON_TS = 10 * 3_600_000  # 10:00 UTC
_ASIAN_TS = 3 * 3_600_000  # 03:00 UTC


def _trade(pnl_pct, ts=_LONDON_TS, pattern="bb_break", direction="long", regime="TRENDING"):
    return {
        "pattern": pattern,
        "direction": direction,
        "entry_ts": ts,
        "pnl_pct": pnl_pct,
        "regime": regime,
    }


class TestKeyAlignment:
    """The rebuilder's keys must be byte-identical to the detector's lookups."""

    def test_session_uses_the_canonical_classifier(self):
        row = aggregate([_trade(1.0, ts=_LONDON_TS)])[0]
        assert row.session == get_trading_session(_LONDON_TS).value

    def test_different_hours_split_into_different_sessions(self):
        rows = aggregate([_trade(1.0, ts=_LONDON_TS), _trade(1.0, ts=_ASIAN_TS)])
        assert len({r.session for r in rows}) == 2

    def test_missing_regime_falls_back_to_unknown(self):
        # Matches detector.py's `candles[-1].regime or "UNKNOWN"`.
        assert aggregate([_trade(1.0, regime=None)])[0].regime == "UNKNOWN"

    def test_direction_is_part_of_the_key(self):
        rows = aggregate([_trade(1.0, direction="long"), _trade(1.0, direction="short")])
        assert len(rows) == 2


class TestAggregation:
    def test_counts_and_win_rate(self):
        rows = aggregate([_trade(1.0), _trade(2.0), _trade(-1.0), _trade(-2.0)])
        assert len(rows) == 1
        assert rows[0].sample_count == 4
        assert rows[0].win_count == 2
        assert rows[0].win_rate == 0.5

    def test_avg_pnl_pct_is_the_mean(self):
        rows = aggregate([_trade(3.0), _trade(-1.0)])
        assert abs(rows[0].avg_pnl_pct - 1.0) < 1e-9

    def test_zero_pnl_is_not_a_win(self):
        # Break-even is not a win; win_rate must not be inflated by scratches.
        assert aggregate([_trade(0.0)])[0].win_count == 0

    def test_is_idempotent_over_the_same_input(self):
        trades = [_trade(1.0), _trade(-1.0), _trade(2.0)]
        assert aggregate(trades) == aggregate(trades)

    def test_separate_patterns_do_not_pool(self):
        rows = aggregate([_trade(1.0, pattern="bb_break"), _trade(1.0, pattern="cci_mom")])
        assert len(rows) == 2

    def test_empty_input_yields_nothing(self):
        assert aggregate([]) == []


class TestIncompleteRows:
    """Explicit absence — an incomplete row teaches nothing and must be skipped."""

    def test_missing_pnl_is_skipped(self):
        assert aggregate([{**_trade(1.0), "pnl_pct": None}]) == []

    def test_missing_pattern_is_skipped(self):
        assert aggregate([{**_trade(1.0), "pattern": None}]) == []

    def test_missing_entry_ts_is_skipped(self):
        assert aggregate([{**_trade(1.0), "entry_ts": None}]) == []

    def test_good_rows_survive_alongside_bad_ones(self):
        rows = aggregate([_trade(1.0), {**_trade(1.0), "pattern": None}])
        assert len(rows) == 1 and rows[0].sample_count == 1


class TestExcludedReasons:
    def test_maintenance_closes_are_excluded_at_source(self):
        # These are filtered in SQL, not in aggregate(); pin the contract so the
        # list cannot quietly lose an entry. A `manual` close is a maintenance
        # stop and an orphan is crash recovery — neither is a strategy outcome.
        assert "manual" in _EXCLUDED_REASONS
        assert "orphaned_crash_recovery" in _EXCLUDED_REASONS
