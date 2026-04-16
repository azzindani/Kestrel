"""Unit tests for src/signal/indicators.py (Tier 1 — 90% branch / 100% function)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.signal.indicators import (
    compute_adx,
    compute_all_indicators,
    compute_atr,
    compute_bb,
    compute_ema,
    compute_rsi,
    compute_volume_ma,
    compute_volume_stddev,
)
from tests.helpers.factories import make_candle, make_candle_series

# ---------------------------------------------------------------------------
# compute_ema
# ---------------------------------------------------------------------------


class TestComputeEma:
    def test_compute_ema_single_value_returns_that_value(self):
        assert compute_ema([100.0], 1) == pytest.approx(100.0)

    def test_compute_ema_constant_series_returns_constant(self):
        prices = [50.0] * 30
        assert compute_ema(prices, 9) == pytest.approx(50.0, abs=1e-6)

    def test_compute_ema_short_series_returns_average_fallback(self):
        # len(prices) < period → returns average
        result = compute_ema([100.0, 200.0], 9)
        assert result == pytest.approx(150.0)

    def test_compute_ema_rising_series_below_last_price(self):
        prices = list(range(1, 31))
        ema = compute_ema(prices, 9)
        assert ema < 30.0
        assert ema > 20.0

    def test_compute_ema_falling_series_above_last_price(self):
        prices = list(range(30, 0, -1))
        ema = compute_ema(prices, 9)
        assert ema > 1.0
        assert ema < 15.0

    def test_compute_ema_uses_k_factor_correctly(self):
        # For period=1, k=1.0 so EMA = last price
        prices = [10.0, 20.0, 30.0]
        assert compute_ema(prices, 1) == pytest.approx(30.0)

    @given(
        price=st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False),
        period=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=200)
    def test_compute_ema_constant_series_always_returns_constant(self, price: float, period: int):
        prices = [price] * (period + 10)
        result = compute_ema(prices, period)
        assert abs(result - price) < 1e-4


# ---------------------------------------------------------------------------
# compute_rsi
# ---------------------------------------------------------------------------


class TestComputeRsi:
    def test_compute_rsi_all_gains_returns_100(self):
        closes = [float(i) for i in range(1, 20)]
        assert compute_rsi(closes, 14) == pytest.approx(100.0)

    def test_compute_rsi_all_losses_returns_0(self):
        closes = [float(20 - i) for i in range(20)]
        assert compute_rsi(closes, 14) == pytest.approx(0.0)

    def test_compute_rsi_neutral_alternating_returns_near_50(self):
        closes = [100.0 + ((-1) ** i) * 1.0 for i in range(30)]
        rsi = compute_rsi(closes, 14)
        assert 40.0 < rsi < 60.0

    def test_compute_rsi_insufficient_data_returns_50(self):
        assert compute_rsi([100.0, 101.0], 14) == pytest.approx(50.0)

    def test_compute_rsi_exactly_at_period_plus_one_does_not_return_50(self):
        closes = [float(i) for i in range(16)]  # 16 values, period=14
        rsi = compute_rsi(closes, 14)
        assert rsi == pytest.approx(100.0)

    @given(
        closes=st.lists(
            st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False),
            min_size=16,
            max_size=50,
        )
    )
    @settings(max_examples=200)
    def test_compute_rsi_always_in_valid_range(self, closes: list[float]):
        rsi = compute_rsi(closes, 14)
        assert 0.0 <= rsi <= 100.0


# ---------------------------------------------------------------------------
# compute_bb
# ---------------------------------------------------------------------------


class TestComputeBollingerBands:
    def test_compute_bb_constant_series_returns_zero_width(self):
        closes = [100.0] * 25
        upper, lower, width = compute_bb(closes, 20)
        assert width == pytest.approx(0.0, abs=1e-9)
        assert upper == pytest.approx(100.0)
        assert lower == pytest.approx(100.0)

    def test_compute_bb_upper_above_lower_for_volatile_series(self):
        closes = [100.0 + i * 0.5 for i in range(25)]
        upper, lower, _ = compute_bb(closes, 20)
        assert upper > lower

    def test_compute_bb_width_positive_for_volatile_series(self):
        closes = [100.0 + (i % 5) for i in range(25)]
        _, _, width = compute_bb(closes, 20)
        assert width > 0.0

    def test_compute_bb_short_series_fallback_returns_avg_bounds(self):
        closes = [100.0, 110.0]
        upper, lower, width = compute_bb(closes, 20)
        # fallback: all three equal avg, width=0
        assert upper == pytest.approx(105.0)
        assert lower == pytest.approx(105.0)
        assert width == pytest.approx(0.0)

    def test_compute_bb_width_is_normalised_upper_minus_lower_over_middle(self):
        closes = [100.0 + i for i in range(25)]
        upper, lower, width = compute_bb(closes, 20)
        mean = sum(closes[-20:]) / 20
        expected_width = (upper - lower) / mean
        assert width == pytest.approx(expected_width, rel=1e-5)

    @given(
        base=st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False),
        noise=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=150)
    def test_compute_bb_upper_always_gte_lower(self, base: float, noise: float):
        closes = [base + (i % 3) * noise for i in range(25)]
        upper, lower, _ = compute_bb(closes, 20)
        assert upper >= lower


# ---------------------------------------------------------------------------
# compute_atr
# ---------------------------------------------------------------------------


class TestComputeAtr:
    def test_compute_atr_constant_candles_returns_zero_range(self):
        candles = [make_candle(100.0, high=100.0, low=100.0) for _ in range(20)]
        assert compute_atr(candles, 14) == pytest.approx(0.0)

    def test_compute_atr_positive_for_varying_prices(self):
        candles = [make_candle(100.0 + i, high=102.0 + i, low=98.0 + i) for i in range(20)]
        assert compute_atr(candles, 14) > 0.0

    def test_compute_atr_single_candle_returns_zero(self):
        assert compute_atr([make_candle(100.0, high=102.0, low=98.0)], 14) == pytest.approx(0.0)

    def test_compute_atr_short_series_returns_average(self):
        candles = [make_candle(100.0 + i, high=102.0 + i, low=98.0 + i) for i in range(5)]
        atr = compute_atr(candles, 14)
        assert atr > 0.0

    def test_compute_atr_wilder_smoothing_dampens_spike(self):
        # Series of flat candles then a spike — ATR should not spike as much
        base = [make_candle(100.0, high=101.0, low=99.0) for _ in range(16)]
        spike = make_candle(100.0, high=120.0, low=80.0)
        candles = base + [spike]
        atr = compute_atr(candles, 14)
        assert atr < 20.0  # TR of spike is 40, but ATR should be smoothed

    @given(
        n=st.integers(min_value=2, max_value=30),
        rng=st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_compute_atr_always_non_negative(self, n: int, rng: float):
        candles = [make_candle(100.0, high=100.0 + rng, low=100.0 - rng) for _ in range(n)]
        assert compute_atr(candles, 14) >= 0.0


# ---------------------------------------------------------------------------
# compute_adx
# ---------------------------------------------------------------------------


class TestComputeAdx:
    def test_compute_adx_insufficient_data_returns_zero(self):
        candles = [make_candle(100.0 + i) for i in range(3)]
        assert compute_adx(candles, 14) == pytest.approx(0.0)

    def test_compute_adx_strong_trend_returns_positive(self):
        # Strong upward trend: ADX should be above 0
        candles = [make_candle(100.0 + i * 2, high=102.0 + i * 2, low=99.0 + i * 2) for i in range(40)]
        adx = compute_adx(candles, 14)
        assert adx > 0.0

    def test_compute_adx_returns_float(self):
        candles = [make_candle(100.0 + i, high=101.0 + i, low=99.0 + i) for i in range(30)]
        adx = compute_adx(candles, 14)
        assert isinstance(adx, float)
        assert not math.isnan(adx)


# ---------------------------------------------------------------------------
# compute_volume_ma
# ---------------------------------------------------------------------------


class TestComputeVolumeMa:
    def test_compute_volume_ma_equal_volumes_returns_that_volume(self):
        assert compute_volume_ma([50.0] * 25, 20) == pytest.approx(50.0)

    def test_compute_volume_ma_uses_last_period_only(self):
        volumes = [1.0] * 10 + [100.0] * 20
        assert compute_volume_ma(volumes, 20) == pytest.approx(100.0)

    def test_compute_volume_ma_empty_returns_zero(self):
        assert compute_volume_ma([], 20) == pytest.approx(0.0)

    def test_compute_volume_ma_single_value_returns_it(self):
        assert compute_volume_ma([42.0], 20) == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# compute_volume_stddev
# ---------------------------------------------------------------------------


class TestComputeVolumeStddev:
    def test_compute_volume_stddev_constant_series_returns_zero(self):
        assert compute_volume_stddev([100.0] * 25, 20) == pytest.approx(0.0)

    def test_compute_volume_stddev_varying_series_positive(self):
        volumes = [100.0, 200.0] * 15
        assert compute_volume_stddev(volumes, 20) > 0.0

    def test_compute_volume_stddev_single_value_returns_zero(self):
        assert compute_volume_stddev([50.0], 20) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_all_indicators
# ---------------------------------------------------------------------------


class TestComputeAllIndicators:
    def test_compute_all_indicators_empty_returns_empty_dict(self):
        assert compute_all_indicators([]) == {}

    def test_compute_all_indicators_returns_all_expected_keys(self):
        candles = make_candle_series([100.0 + i * 0.1 for i in range(30)])
        result = compute_all_indicators(candles)
        expected_keys = {
            "ema9",
            "ema21",
            "rsi14",
            "atr14",
            "bb_upper",
            "bb_lower",
            "bb_width",
            "adx",
            "volume_ma20",
            "volume_ratio",
        }
        assert expected_keys.issubset(result.keys())

    def test_compute_all_indicators_volume_ratio_positive(self):
        candles = make_candle_series([100.0] * 25)
        result = compute_all_indicators(candles)
        assert result["volume_ratio"] > 0.0

    def test_compute_all_indicators_rsi_in_valid_range(self):
        candles = make_candle_series([100.0 + i for i in range(30)])
        result = compute_all_indicators(candles)
        assert 0.0 <= result["rsi14"] <= 100.0

    def test_compute_all_indicators_ema9_above_ema21_in_uptrend(self):
        closes = [100.0 + i * 1.0 for i in range(40)]
        candles = make_candle_series(closes)
        result = compute_all_indicators(candles)
        assert result["ema9"] > result["ema21"]
