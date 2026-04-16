"""Unit tests for src/signal/patterns.py"""
import pytest

from src.config import Candle, Direction, Params, PatternType
from src.signal.patterns import (
    detect_impulse_retracement,
    detect_wick_rejection,
    detect_compression_breakout,
    detect_momentum_continuation,
    detect_anomaly_fade,
    registry,
)


def _params() -> Params:
    from src.config import load_params
    return load_params("params.json")


def _candle(
    close: float, open_: float = None, high: float = None, low: float = None,
    volume: float = 100.0, ts: int = 0,
    body_size: float = None, body_ratio: float = None,
    upper_wick: float = None, lower_wick: float = None,
    volume_ratio: float = None, volume_ma20: float = None,
    bb_upper: float = None, bb_lower: float = None, bb_width: float = None,
    atr14: float = None, direction: str = None,
) -> Candle:
    o = open_ if open_ is not None else close
    h = high if high is not None else max(o, close) * 1.01
    l = low if low is not None else min(o, close) * 0.99
    bs = body_size if body_size is not None else abs(close - o)
    tr = h - l
    br = body_ratio if body_ratio is not None else (bs / tr if tr > 0 else 0.0)
    uw = upper_wick if upper_wick is not None else h - max(o, close)
    lw = lower_wick if lower_wick is not None else min(o, close) - l
    dir_ = direction if direction else ("bullish" if close >= o else "bearish")
    return Candle(
        bot_id="test", ts=ts, pair="BTCUSDT", timeframe="5m",
        open=o, high=h, low=l, close=close, volume=volume,
        body_size=bs, body_ratio=br, upper_wick=uw, lower_wick=lw, direction=dir_,
        volume_ma20=volume_ma20 or 100.0, volume_ratio=volume_ratio,
        bb_upper=bb_upper, bb_lower=bb_lower, bb_width=bb_width,
        atr14=atr14,
    )


class TestRegistry:
    def test_all_five_patterns_registered(self):
        expected = {
            "impulse_retracement", "wick_rejection", "compression_breakout",
            "momentum_continuation", "anomaly_fade",
        }
        assert expected == set(registry.keys())


class TestImpulseRetracement:
    def test_valid_long_setup_fires(self):
        params = _params()
        # Trigger candle: bullish, high body ratio, high volume
        trigger = _candle(
            close=110.0, open_=100.0, high=112.0, low=99.0,
            volume=200.0, body_size=10.0, body_ratio=0.65,
            volume_ratio=1.5, volume_ma20=133.0,
        )
        # Retracement candle: small body, lower volume
        retrace = _candle(
            close=106.0, open_=109.0, high=110.0, low=105.0,
            volume=80.0, body_size=3.0, body_ratio=0.6,
            volume_ratio=0.6, volume_ma20=133.0,
        )
        candles = [_candle(100.0)] * 20 + [trigger, retrace]
        result = detect_impulse_retracement(candles, params)
        assert result is not None
        assert result.direction is Direction.LONG

    def test_insufficient_candles_returns_none(self):
        params = _params()
        result = detect_impulse_retracement([_candle(100.0)], params)
        assert result is None

    def test_retrace_too_deep_returns_none(self):
        params = _params()
        trigger = _candle(
            close=110.0, open_=100.0, high=112.0, low=99.0,
            volume=200.0, body_size=10.0, body_ratio=0.65,
            volume_ratio=1.5, volume_ma20=133.0,
        )
        # Retrace body = 8/10 = 80% → exceeds retracement_max (0.50)
        retrace = _candle(
            close=102.0, open_=110.0, high=111.0, low=101.0,
            volume=50.0, body_size=8.0, body_ratio=0.8,
            volume_ratio=0.5, volume_ma20=133.0,
        )
        candles = [_candle(100.0)] * 20 + [trigger, retrace]
        result = detect_impulse_retracement(candles, params)
        assert result is None


class TestWickRejection:
    def test_valid_wick_rejection_long(self):
        params = _params()
        # Long wick candle: lower_wick >> body, close in top 30%
        rejection_c = _candle(
            close=109.0, open_=107.0, high=110.0, low=100.0,
            body_size=2.0, lower_wick=7.0, upper_wick=1.0,
            body_ratio=0.2,
            volume=100.0, atr14=5.0,
        )
        candles = [_candle(105.0 + i * 0.1) for i in range(12)] + [rejection_c]
        result = detect_wick_rejection(candles, params)
        assert result is not None
        assert result.direction is Direction.LONG


class TestMomentumContinuation:
    def test_valid_three_candle_setup(self):
        params = _params()
        # 3 accelerating bullish candles + small bearish retracement
        c1 = _candle(close=102.0, open_=100.0, volume=100.0, body_size=2.0)
        c2 = _candle(close=105.0, open_=102.0, volume=120.0, body_size=3.0)
        c3 = _candle(close=109.0, open_=105.0, volume=150.0, body_size=4.0)
        retrace = _candle(close=108.5, open_=109.0, volume=80.0, body_size=0.5, direction="bearish")
        candles = [_candle(100.0)] * 5 + [c1, c2, c3, retrace]
        result = detect_momentum_continuation(candles, params)
        assert result is not None
        assert result.direction is Direction.LONG
