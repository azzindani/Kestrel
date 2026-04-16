"""Unit tests for src/signal/indicators.py"""
import math
import pytest

from src.signal.indicators import (
    compute_ema,
    compute_rsi,
    compute_bb,
    compute_atr,
    compute_adx,
    compute_volume_ma,
)
from src.config import Candle


def _make_candle(close: float, high: float = None, low: float = None, volume: float = 100.0) -> Candle:
    h = high or close * 1.01
    l = low or close * 0.99
    return Candle(bot_id="test", ts=0, pair="BTCUSDT", timeframe="5m",
                  open=close, high=h, low=l, close=close, volume=volume)


class TestEMA:
    def test_single_value_returns_that_value(self):
        assert compute_ema([100.0], 1) == pytest.approx(100.0)

    def test_constant_series_returns_constant(self):
        prices = [50.0] * 30
        assert compute_ema(prices, 9) == pytest.approx(50.0, abs=1e-6)

    def test_period_fallback_when_short(self):
        result = compute_ema([100.0, 200.0], 9)
        assert result == pytest.approx(150.0)

    def test_rising_series_ema_below_last_price(self):
        prices = list(range(1, 31))  # 1..30
        ema = compute_ema(prices, 9)
        assert ema < 30.0
        assert ema > 20.0


class TestRSI:
    def test_all_gains_returns_100(self):
        closes = [float(i) for i in range(1, 20)]
        rsi = compute_rsi(closes, 14)
        assert rsi == pytest.approx(100.0)

    def test_all_losses_returns_0(self):
        closes = [float(20 - i) for i in range(20)]
        rsi = compute_rsi(closes, 14)
        assert rsi == pytest.approx(0.0)

    def test_neutral_returns_50(self):
        # Alternating up/down of equal magnitude → RSI ≈ 50
        closes = [100.0 + ((-1) ** i) * 1.0 for i in range(30)]
        rsi = compute_rsi(closes, 14)
        assert 40 < rsi < 60

    def test_insufficient_data_returns_50(self):
        assert compute_rsi([100.0, 101.0], 14) == pytest.approx(50.0)


class TestBollingerBands:
    def test_constant_series_zero_width(self):
        closes = [100.0] * 25
        upper, lower, width = compute_bb(closes, 20)
        assert width == pytest.approx(0.0, abs=1e-9)
        assert upper == pytest.approx(100.0)
        assert lower == pytest.approx(100.0)

    def test_upper_above_lower(self):
        closes = [100.0 + i * 0.5 for i in range(25)]
        upper, lower, width = compute_bb(closes, 20)
        assert upper > lower

    def test_width_positive(self):
        closes = [100.0 + (i % 5) for i in range(25)]
        _, _, width = compute_bb(closes, 20)
        assert width > 0


class TestATR:
    def test_constant_candles_returns_zero_range(self):
        candles = [_make_candle(100.0, 100.0, 100.0) for _ in range(20)]
        assert compute_atr(candles, 14) == pytest.approx(0.0)

    def test_positive_range(self):
        candles = [_make_candle(100.0 + i, 100.0 + i + 2, 100.0 + i - 2) for i in range(20)]
        atr = compute_atr(candles, 14)
        assert atr > 0

    def test_insufficient_returns_average(self):
        candles = [_make_candle(100.0, 102.0, 98.0)]
        atr = compute_atr(candles, 14)
        assert atr == pytest.approx(0.0)  # only 1 candle → no TR computable


class TestVolumeMA:
    def test_equal_volumes(self):
        assert compute_volume_ma([50.0] * 25, 20) == pytest.approx(50.0)

    def test_uses_last_period_bars(self):
        volumes = [1.0] * 10 + [100.0] * 20
        ma = compute_volume_ma(volumes, 20)
        assert ma == pytest.approx(100.0)
