"""Tests for the wave strategy family and the detector's counter-trend bypass.

Patterns under test (CLAUDE.md §9 registry):
    wave_ride — trend-aligned continuation after a shallow pullback
    vol_burst — trend-aligned entry only while volatility is expanding
    wave_flip — COUNTER-TREND fade of an exhausted run (the "flip the position" entry)

The detector change being guarded: COUNTER_TREND_PATTERNS (wave_flip) may set their
own direction; every other pattern must still agree with the trend filter.
"""

from __future__ import annotations

from src.config import Direction, Rejection, TradingSession
from src.signal.detector import _pattern_scan
from src.signal.patterns import (
    COUNTER_TREND_PATTERNS,
    detect_vol_burst,
    detect_wave_flip,
    detect_wave_ride,
)
from tests.helpers.factories import make_candle, make_params


def _uptrend_base(n: int, close0: float = 100.0, rng: float = 0.2, vol: float = 120.0) -> list:
    """n tight-range bullish candles in an EMA uptrend (ema9 > ema21)."""
    out = []
    for i in range(n):
        close = close0 + i * 0.3
        out.append(
            make_candle(
                close=close,
                open_=close - 0.15,
                high=close + rng / 2,
                low=close - rng / 2,
                volume=vol,
                ts=i * 300_000,
                ema9=close * 1.002,
                ema21=close * 0.998,
                rsi14=58.0,
                atr14=close * 0.003,
                adx=25.0,
                volume_ma20=100.0,
                volume_ratio=1.5,
                regime="TRENDING",
            )
        )
    return out


def _bearish(px: float, ts: int, drop: float = 1.0, regime: str = "TRENDING"):
    return make_candle(
        close=px - drop,
        open_=px,
        high=px + 0.05,
        low=px - drop - 0.05,
        volume=160.0,
        ts=ts,
        ema9=px * 1.002,
        ema21=px * 0.998,
        regime=regime,
    )


def _bullish_strong(px: float, ts: int, rise: float = 1.0, regime: str = "TRENDING"):
    return make_candle(
        close=px + rise,
        open_=px,
        high=px + rise + 0.05,
        low=px - 0.05,
        volume=160.0,
        ts=ts,
        ema9=(px + rise) * 1.002,
        ema21=(px + rise) * 0.998,
        regime=regime,
    )


# ---------------------------------------------------------------------------
# wave_ride
# ---------------------------------------------------------------------------


def test_wave_ride_fires_on_pullback_resumption():
    params = make_params()
    c = _uptrend_base(24)
    c[-2] = _bearish(c[-2].close, c[-2].ts, drop=0.4)  # pullback
    c[-1] = _bullish_strong(c[-2].close, c[-1].ts, rise=1.0)  # resumption
    res = detect_wave_ride(c, params)
    assert res is not None
    assert res.direction is Direction.LONG
    assert res.details["variant"] == "wave_ride"


def test_wave_ride_no_fire_without_pullback():
    # An uninterrupted bullish run (no pullback in the prior two candles) is a
    # blow-off, not a wave entry — wave_ride must stay silent.
    params = make_params()
    c = _uptrend_base(24)
    assert detect_wave_ride(c, params) is None


# ---------------------------------------------------------------------------
# vol_burst
# ---------------------------------------------------------------------------


def test_vol_burst_fires_on_expansion():
    params = make_params()  # atr_volatile_multiplier=1.5
    c = _uptrend_base(20, rng=0.2)  # tight base
    last = c[-1].close
    for i in range(5):  # 5 wide-range bullish candles
        px = last + (i + 1) * 0.5
        c.append(
            make_candle(
                close=px,
                open_=px - 1.2,
                high=px + 0.3,
                low=px - 1.3,
                volume=180.0,
                ts=(20 + i) * 300_000,
                ema9=px * 1.002,
                ema21=px * 0.998,
                regime="VOLATILE",
            )
        )
    res = detect_vol_burst(c, params)
    assert res is not None
    assert res.direction is Direction.LONG
    assert res.details["variant"] == "vol_burst"
    assert res.details["atr_expansion"] >= params.atr_volatile_multiplier


def test_vol_burst_no_fire_when_flat():
    params = make_params()
    c = _uptrend_base(25, rng=0.2)  # uniformly tight — no expansion
    assert detect_vol_burst(c, params) is None


# ---------------------------------------------------------------------------
# wave_flip (counter-trend)
# ---------------------------------------------------------------------------


def test_wave_flip_fades_exhausted_run():
    params = make_params()  # momentum_acceleration_candles=3 → run length 3
    c = _uptrend_base(6)  # candles[-4:-1] are a 3-candle bullish run
    c[-1] = _bearish(c[-1].close, c[-1].ts, drop=1.0, regime="RANGING")  # reversal
    res = detect_wave_flip(c, params)
    assert res is not None
    assert res.direction is Direction.SHORT
    assert res.details["variant"] == "wave_flip"


def test_wave_flip_is_registered_counter_trend():
    assert "wave_flip" in COUNTER_TREND_PATTERNS


# ---------------------------------------------------------------------------
# detector counter-trend bypass (_pattern_scan)
# ---------------------------------------------------------------------------


def test_pattern_scan_allows_counter_trend_direction():
    # wave_flip fires SHORT while the trend filter says LONG — the scan must still
    # return it (counter-trend bypass), not skip it on the direction mismatch.
    params = make_params()
    c = _uptrend_base(6)
    c[-1] = _bearish(c[-1].close, c[-1].ts, drop=1.0, regime="RANGING")
    res = _pattern_scan(c, params, frozenset({"wave_flip"}), Direction.LONG, {}, TradingSession.LONDON, 1.0)
    assert not isinstance(res, Rejection)
    pattern_result, _conf = res
    assert pattern_result.direction is Direction.SHORT


def test_pattern_scan_rejects_trend_pattern_on_direction_mismatch():
    # wave_ride is trend-following: fires LONG, but trend_direction is SHORT → skip.
    params = make_params()
    c = _uptrend_base(24)
    c[-2] = _bearish(c[-2].close, c[-2].ts, drop=0.4)
    c[-1] = _bullish_strong(c[-2].close, c[-1].ts, rise=1.0)
    res = _pattern_scan(c, params, frozenset({"wave_ride"}), Direction.SHORT, {}, TradingSession.LONDON, 1.0)
    assert isinstance(res, Rejection)


def test_pattern_scan_skips_trend_pattern_when_trend_is_none():
    # When the trend filter rejected (trend_direction=None) a trend-following
    # pattern cannot fire, but a counter-trend one still can.
    params = make_params()
    c = _uptrend_base(6)
    c[-1] = _bearish(c[-1].close, c[-1].ts, drop=1.0, regime="RANGING")
    trend_pattern = _pattern_scan(c, params, frozenset({"wave_ride"}), None, {}, TradingSession.LONDON, 1.0)
    assert isinstance(trend_pattern, Rejection)
    counter = _pattern_scan(c, params, frozenset({"wave_flip"}), None, {}, TradingSession.LONDON, 1.0)
    assert not isinstance(counter, Rejection)
