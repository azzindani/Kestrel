"""Test data factories for the Kestrel test suite.

All factories return domain objects with sensible defaults. Override any
field via keyword arguments. No I/O — pure construction.
"""

from __future__ import annotations

import time
from typing import Optional

from src.config import (
    AppConfig,
    BucketState,
    Candle,
    Direction,
    Env,
    Params,
    Signal,
    compute_candle_geometry,
)

# ---------------------------------------------------------------------------
# Candle factory
# ---------------------------------------------------------------------------


def make_candle(
    close: float = 100.0,
    high: Optional[float] = None,
    low: Optional[float] = None,
    open_: Optional[float] = None,
    volume: float = 100.0,
    ts: int = 0,
    bot_id: str = "test",
    pair: str = "BTCUSDT",
    timeframe: str = "5m",
    with_geometry: bool = True,
    **kwargs,
) -> Candle:
    """Create a Candle with sensible defaults.

    Geometry fields (body_size, total_range, etc.) are auto-computed unless
    overridden in kwargs or with_geometry=False.
    """
    h = high if high is not None else round(close * 1.01, 8)
    lo = low if low is not None else round(close * 0.99, 8)
    o = open_ if open_ is not None else close

    geom = compute_candle_geometry(o, h, lo, close) if with_geometry else {}
    merged = {**geom, **kwargs}

    return Candle(
        bot_id=bot_id,
        ts=ts,
        pair=pair,
        timeframe=timeframe,
        open=o,
        high=h,
        low=lo,
        close=close,
        volume=volume,
        **merged,
    )


def make_candle_series(
    closes: list[float],
    base_volume: float = 100.0,
    ts_start: int = 0,
    ts_step: int = 300_000,  # 5 minutes in ms
    **kwargs,
) -> list[Candle]:
    """Create a series of candles from a list of close prices."""
    return [
        make_candle(
            close=c,
            volume=base_volume,
            ts=ts_start + i * ts_step,
            **kwargs,
        )
        for i, c in enumerate(closes)
    ]


def make_trending_candles(
    n: int = 60,
    start_price: float = 100.0,
    step: float = 0.5,
    volume: float = 150.0,
    ts_start: int = 0,
) -> list[Candle]:
    """Create a trending (upward) candle series with indicators set to trigger TRENDING regime."""
    candles = []
    for i in range(n):
        price = start_price + i * step
        # Make candles with EMA/ADX indicators that indicate trending
        c = make_candle(
            close=price,
            open_=price - step * 0.3,
            high=price + step,
            low=price - step * 0.5,
            volume=volume,
            ts=ts_start + i * 300_000,
            ema9=price * 1.001,
            ema21=price * 0.995,
            rsi14=58.0,
            atr14=price * 0.003,
            adx=25.0,
            volume_ma20=100.0,
            volume_ratio=1.5,
            regime="TRENDING",
        )
        candles.append(c)
    return candles


# ---------------------------------------------------------------------------
# Params factory
# ---------------------------------------------------------------------------


def make_params(**overrides) -> Params:
    """Create Params with default values matching params.json defaults."""
    defaults = dict(
        ema_fast=9,
        ema_slow=21,
        rsi_low=45.0,
        rsi_high=55.0,
        volume_ratio_min=1.3,
        tp_atr_multiplier=1.6,
        sl_atr_multiplier=1.0,
        min_confidence=0.55,
        adx_trend_min=20.0,
        bb_width_threshold=0.02,
        max_hold_candles=4,
        max_active_buckets=1,
        body_ratio_min=0.6,
        wick_ratio_min=2.0,
        compression_factor=0.5,
        ema_spread_threshold=0.001,
        atr_volatile_multiplier=1.5,
        atr_quiet_multiplier=0.5,
        retracement_min=0.3,
        retracement_max=0.5,
        anomaly_volume_stddev=2.5,
        anomaly_price_atr=2.5,
        momentum_acceleration_candles=3,
    )
    defaults.update(overrides)
    return Params(**defaults)


# ---------------------------------------------------------------------------
# Signal factory
# ---------------------------------------------------------------------------


def make_signal(
    entry: float = 83000.0,
    direction: Direction = Direction.LONG,
    confidence: float = 0.75,
    tp_offset: float = 336.0,
    sl_offset: float = 210.0,
    **overrides,
) -> Signal:
    """Create a Signal with sensible defaults."""
    if direction is Direction.LONG:
        tp = entry + tp_offset
        sl = entry - sl_offset
    else:
        tp = entry - tp_offset
        sl = entry + sl_offset

    defaults = dict(
        bot_id="test",
        session_id="s",
        env="dev",
        ts=int(time.time() * 1000),
        pair="BTCUSDT",
        timeframe="5m",
        candle_ts=0,
        pattern="impulse_retracement",
        direction=direction,
        confidence=confidence,
        regime="TRENDING",
        layer_regime=1,
        layer_trend=1,
        layer_momentum=1,
        layer_volume=1,
        layers_passed=4,
        entry_price=entry,
        tp_price=tp,
        sl_price=sl,
        size_usdt=10.0,
    )
    defaults.update(overrides)
    return Signal(**defaults)


# ---------------------------------------------------------------------------
# AppConfig factory
# ---------------------------------------------------------------------------


def make_app_config(**overrides) -> AppConfig:
    """Create an AppConfig with test defaults."""
    defaults = dict(
        env=Env.DEV,
        bot_id="test",
        exchange="binance",
        api_key="k",
        api_secret="s",
        testnet=True,
        db_host="localhost",
        db_port=5432,
        db_name="kestrel",
        db_user="u",
        db_password="p",
        pair="BTCUSDT",
        timeframe_entry="5m",
        timeframe_regime="15m",
        leverage=20,
        bucket_size_usdt=10.0,
        max_active_buckets=1,
        telegram_token="t",
        telegram_chat_id="c",
        log_level="DEBUG",
    )
    defaults.update(overrides)
    return AppConfig(**defaults)


# ---------------------------------------------------------------------------
# BucketState factory
# ---------------------------------------------------------------------------


def make_bucket_state(**overrides) -> BucketState:
    """Create a BucketState with safe defaults (no open positions, no reconnect)."""
    defaults = dict(
        active_positions=0,
        last_ws_reconnect_ts=None,
        session_net_pnl=0.0,
        current_ts=int(time.time() * 1000),
    )
    defaults.update(overrides)
    return BucketState(**defaults)
