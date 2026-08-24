"""Unit tests for vwma_cross — the volume-weighted entry deployed to dev 2026-08-24.

The first registry entry whose signal uses VOLUME rather than price alone. It was
the strongest GROSS entry on record and near-identical across two independent eras
(+0.86 bps recent / +0.90 bps prior-year lockbox), which is why it earned a dev
forward test — it still loses to the fee floor and is barred from staging by its
43-46% win rate.

Two things to lock down. The four-step arming checklist (§9 + the 2026-06-27
mislabel), since missing any one step makes a pattern silently inert or logs it
under a neighbour's name. And that the entry stays EDGE-triggered on the cross and
keeps matching scripts/algo_search.py's `_vwma_cross`: the measured edge belongs to
that exact form, and a state-based variant is a different, unvalidated signal.
"""

from __future__ import annotations

from src.config import Direction, PatternType
from src.signal.patterns import (
    _VWMA_PERIOD,
    SELF_DIRECTING_PATTERNS,
    _vwma,
    detect_vwma_cross,
    registry,
)
from src.signal.regime import Regime, regime_permits_pattern
from tests.helpers.factories import make_candle, make_params


def _series(closes, volumes=None):
    vols = volumes or [100.0] * len(closes)
    return [make_candle(close=c, volume=v, ts=i * 300_000) for i, (c, v) in enumerate(zip(closes, vols))]


class TestArmingChecklist:
    def test_registered(self):
        assert registry.get("vwma_cross") is detect_vwma_cross

    def test_self_directing(self):
        # The cross side supplies direction, so it must bypass the trend gate.
        assert "vwma_cross" in SELF_DIRECTING_PATTERNS

    def test_permitted_in_all_non_quiet_regimes(self):
        for regime in (Regime.TRENDING, Regime.VOLATILE, Regime.RANGING):
            assert regime_permits_pattern(regime, "vwma_cross"), regime

    def test_blocked_in_quiet(self):
        assert not regime_permits_pattern(Regime.QUIET, "vwma_cross")

    def test_has_own_pattern_type(self):
        assert PatternType.VWMA_CROSS.value == "vwma_cross"


class TestVwmaHelper:
    def test_equal_volume_matches_simple_average(self):
        candles = _series([10.0] * 19 + [20.0])
        expected = (10.0 * 19 + 20.0) / 20
        got = _vwma(candles, _VWMA_PERIOD)
        assert got is not None and abs(got - expected) < 1e-9

    def test_volume_pulls_the_average_toward_where_it_traded(self):
        # Same prices; the heavily-traded high price must dominate.
        flat = _series([10.0] * 19 + [20.0], [1.0] * 19 + [1000.0])
        got = _vwma(flat, _VWMA_PERIOD)
        assert got is not None and got > 19.0

    def test_insufficient_history_is_none(self):
        assert _vwma(_series([10.0] * 5), _VWMA_PERIOD) is None

    def test_zero_volume_window_is_none(self):
        # Explicit absence rather than a divide-by-zero.
        assert _vwma(_series([10.0] * 20, [0.0] * 20), _VWMA_PERIOD) is None


class TestDirection:
    def test_cross_up_is_long(self):
        # Flat below, then a jump that carries close above the VWMA.
        candles = _series([10.0] * 20 + [40.0])
        got = detect_vwma_cross(candles, make_params())
        assert got is not None and got.direction is Direction.LONG

    def test_cross_down_is_short(self):
        candles = _series([40.0] * 20 + [10.0])
        got = detect_vwma_cross(candles, make_params())
        assert got is not None and got.direction is Direction.SHORT

    def test_labels_its_own_pattern_type(self):
        got = detect_vwma_cross(_series([10.0] * 20 + [40.0]), make_params())
        assert got is not None and got.pattern is PatternType.VWMA_CROSS

    def test_flat_series_does_not_fire(self):
        assert detect_vwma_cross(_series([10.0] * 25), make_params()) is None

    def test_insufficient_history_does_not_fire(self):
        assert detect_vwma_cross(_series([10.0] * 5), make_params()) is None


class TestEdgeTriggeredNotState:
    """The measured edge belongs to the CROSS, not to sitting on one side."""

    def test_does_not_refire_while_price_stays_above(self):
        params = make_params()
        candles = _series([10.0] * 20 + [40.0])
        assert detect_vwma_cross(candles, params) is not None  # the crossing candle
        # Price stays above and keeps rising: already-crossed, so no new entry.
        held = candles + [make_candle(close=41.0, volume=100.0, ts=21 * 300_000)]
        assert detect_vwma_cross(held, params) is None
