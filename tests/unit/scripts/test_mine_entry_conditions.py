"""Unit tests for the entry-condition miner (scripts/mine_entry_conditions.py).

A mining tool's danger is not crashing — it is reporting confident nonsense. The
properties pinned here are the ones that keep it honest: thin buckets must be
DROPPED (a 3-trade bucket at 100% win is noise, and printing it is how mining turns
into self-deception), monotonicity must mean an actual consistent direction rather
than one lucky slice, and oscillators must be direction-aligned so a long at RSI 80
and a short at RSI 20 register as the same "chasing an extended move" condition
instead of cancelling out.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from mine_entry_conditions import (  # noqa: E402
    bucketize,
    features_for,
    is_monotone,
    net_bps,
    quantile_edges,
    separation,
)


def _rows(pairs):
    """(value, won, bps) triples from (value, bps) with win implied by sign."""
    return [(v, b > 0, b) for v, b in pairs]


class TestQuantileEdges:
    def test_splits_into_interior_cuts(self):
        assert len(quantile_edges(list(range(100)), 4)) == 3

    def test_too_few_values_yields_no_edges(self):
        assert quantile_edges([1.0, 2.0], 5) == []

    def test_collapses_duplicate_cuts(self):
        # A constant feature must not yield several identical buckets.
        assert quantile_edges([5.0] * 100, 4) == []

    def test_edges_are_ascending(self):
        edges = quantile_edges([float(i) for i in range(50)], 5)
        assert edges == sorted(edges)


class TestBucketize:
    def test_drops_buckets_thinner_than_min_n(self):
        rows = _rows([(1.0, 10.0)] * 3 + [(9.0, -10.0)] * 50)
        got = bucketize(rows, [5.0], min_n=10)
        assert len(got) == 1  # the 3-trade bucket is dropped, not reported

    def test_keeps_buckets_at_min_n(self):
        rows = _rows([(1.0, 10.0)] * 10 + [(9.0, -10.0)] * 10)
        assert len(bucketize(rows, [5.0], min_n=10)) == 2

    def test_win_pct_and_avg_are_computed_per_bucket(self):
        rows = _rows([(1.0, 10.0), (1.0, 10.0), (1.0, -10.0), (1.0, 10.0)])
        got = bucketize(rows, [], min_n=1)
        assert len(got) == 1
        assert abs(got[0].win_pct - 75.0) < 1e-9
        assert abs(got[0].avg_bps - 5.0) < 1e-9

    def test_no_edges_is_one_bucket(self):
        assert len(bucketize(_rows([(1.0, 1.0)] * 20), [], min_n=5)) == 1

    def test_empty_input_yields_nothing(self):
        assert bucketize([], [1.0], min_n=1) == []


class TestSeparation:
    def test_single_bucket_has_no_separation(self):
        assert separation(bucketize(_rows([(1.0, 5.0)] * 10), [], min_n=1)) == 0.0

    def test_spread_between_best_and_worst(self):
        rows = _rows([(1.0, 10.0)] * 10 + [(9.0, -20.0)] * 10)
        assert abs(separation(bucketize(rows, [5.0], min_n=5)) - 30.0) < 1e-9


class TestIsMonotone:
    def test_needs_at_least_three_buckets(self):
        rows = _rows([(1.0, 10.0)] * 10 + [(9.0, -10.0)] * 10)
        assert not is_monotone(bucketize(rows, [5.0], min_n=5))

    def test_descending_is_monotone(self):
        rows = _rows([(1.0, 30.0)] * 10 + [(5.0, 10.0)] * 10 + [(9.0, -10.0)] * 10)
        assert is_monotone(bucketize(rows, [3.0, 7.0], min_n=5))

    def test_ascending_is_monotone(self):
        rows = _rows([(1.0, -30.0)] * 10 + [(5.0, 0.0)] * 10 + [(9.0, 30.0)] * 10)
        assert is_monotone(bucketize(rows, [3.0, 7.0], min_n=5))

    def test_v_shape_is_not_monotone(self):
        # One lucky slice in the middle must not read as a directional feature.
        rows = _rows([(1.0, 30.0)] * 10 + [(5.0, -30.0)] * 10 + [(9.0, 30.0)] * 10)
        assert not is_monotone(bucketize(rows, [3.0, 7.0], min_n=5))


class TestDirectionAlignment:
    def _row(self, direction, rsi, ema9=101.0, ema21=100.0, close=100.0):
        return {
            "direction": direction,
            "rsi14": rsi,
            "adx": 20.0,
            "atr14": 1.0,
            "bb_width": 0.02,
            "volume_ratio": 1.2,
            "ema9": ema9,
            "ema21": ema21,
            "close": close,
            "utc_hour": 3,
        }

    def test_long_rsi_is_unchanged(self):
        assert features_for(self._row("long", 80.0))["rsi14_aligned"] == 80.0

    def test_short_rsi_is_mirrored(self):
        # A short at RSI 20 is as "extended" as a long at RSI 80.
        assert features_for(self._row("short", 20.0))["rsi14_aligned"] == 80.0

    def test_opposite_trades_align_to_the_same_condition(self):
        long_v = features_for(self._row("long", 80.0))["rsi14_aligned"]
        short_v = features_for(self._row("short", 20.0))["rsi14_aligned"]
        assert long_v == short_v  # pooled RAW these would cancel out

    def test_ema_spread_flips_sign_for_shorts(self):
        long_s = features_for(self._row("long", 50.0))["ema_spread_bps_aligned"]
        short_s = features_for(self._row("short", 50.0))["ema_spread_bps_aligned"]
        assert long_s == -short_s


class TestNetBps:
    def test_scales_pnl_by_notional(self):
        assert abs(net_bps({"pnl_net_usdt": 0.10, "notional_usdt": 100.0}) - 10.0) < 1e-9

    def test_zero_notional_is_none(self):
        # Explicit absence rather than a divide-by-zero.
        assert net_bps({"pnl_net_usdt": 1.0, "notional_usdt": 0.0}) is None

    def test_missing_pnl_is_none(self):
        assert net_bps({"pnl_net_usdt": None, "notional_usdt": 100.0}) is None
