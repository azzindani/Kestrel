"""
Layer 0 — types, enums, constants, pure utilities.
No I/O of any kind. All other layers depend on this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Env(str, Enum):
    DEV = "dev"
    PROD = "prod"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class Regime(str, Enum):
    TRENDING = "TRENDING"
    VOLATILE = "VOLATILE"
    RANGING = "RANGING"
    QUIET = "QUIET"


class PatternType(str, Enum):
    IMPULSE_RETRACEMENT = "impulse_retracement"
    WICK_REJECTION = "wick_rejection"
    COMPRESSION_BREAKOUT = "compression_breakout"
    MOMENTUM_CONTINUATION = "momentum_continuation"
    ANOMALY_FADE = "anomaly_fade"


class SignalOutcome(str, Enum):
    FIRED = "fired"
    REJECTED = "rejected"
    EXPIRED = "expired"


class CloseReason(str, Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIMEOUT = "timeout"
    MANUAL = "manual"
    LIQUIDATED = "liquidated"


class LogLevel(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogCategory(str, Enum):
    SIGNAL = "signal"
    ORDER = "order"
    POSITION = "position"
    RISK = "risk"
    CONNECTION = "connection"
    SYSTEM = "system"


class TradingSession(str, Enum):
    ASIAN = "asian"
    LONDON = "london"
    US = "us"
    OVERLAP = "overlap"


# ---------------------------------------------------------------------------
# Candle (domain object — populated by data layer, read by signal layer)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candle:
    """Completed OHLCV candle with precomputed indicators and geometry."""

    # identity
    bot_id: str
    ts: int  # unix ms — candle open time
    pair: str
    timeframe: str

    # OHLCV
    open: float
    high: float
    low: float
    close: float
    volume: float

    # indicators — None until computed at candle close
    ema9: Optional[float] = None
    ema21: Optional[float] = None
    rsi14: Optional[float] = None
    atr14: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_width: Optional[float] = None
    adx: Optional[float] = None
    volume_ma20: Optional[float] = None
    volume_ratio: Optional[float] = None
    regime: Optional[str] = None

    # geometry — precomputed at candle close
    body_size: Optional[float] = None
    total_range: Optional[float] = None
    body_ratio: Optional[float] = None
    upper_wick: Optional[float] = None
    lower_wick: Optional[float] = None
    direction: Optional[str] = None

    # DB primary key (None before persistence)
    id: Optional[int] = None


# ---------------------------------------------------------------------------
# Params (loaded from params.json — schema only, no I/O here)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Params:
    """Tunable strategy parameters. Loaded from params.json by the startup layer."""

    ema_fast: int
    ema_slow: int
    rsi_low: float
    rsi_high: float
    volume_ratio_min: float
    tp_atr_multiplier: float
    sl_atr_multiplier: float
    min_confidence: float
    adx_trend_min: float
    bb_width_threshold: float
    max_hold_candles: int
    max_active_buckets: int
    body_ratio_min: float
    wick_ratio_min: float
    compression_factor: float
    ema_spread_threshold: float
    atr_volatile_multiplier: float
    atr_quiet_multiplier: float
    retracement_min: float
    retracement_max: float
    anomaly_volume_stddev: float
    anomaly_price_atr: float
    momentum_acceleration_candles: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Params":
        """Construct Params from the params.json value-dict (extracts 'value' keys).

        Raises ValueError listing all missing keys rather than a bare KeyError.
        """
        required_keys = [
            "ema_fast",
            "ema_slow",
            "rsi_low",
            "rsi_high",
            "volume_ratio_min",
            "tp_atr_multiplier",
            "sl_atr_multiplier",
            "min_confidence",
            "adx_trend_min",
            "bb_width_threshold",
            "max_hold_candles",
            "max_active_buckets",
            "body_ratio_min",
            "wick_ratio_min",
            "compression_factor",
            "ema_spread_threshold",
            "atr_volatile_multiplier",
            "atr_quiet_multiplier",
            "retracement_min",
            "retracement_max",
            "anomaly_volume_stddev",
            "anomaly_price_atr",
            "momentum_acceleration_candles",
        ]
        missing = [k for k in required_keys if k not in d]
        if missing:
            raise ValueError(f"params.json missing keys: {', '.join(missing)}")
        return cls(
            ema_fast=int(d["ema_fast"]["value"]),
            ema_slow=int(d["ema_slow"]["value"]),
            rsi_low=float(d["rsi_low"]["value"]),
            rsi_high=float(d["rsi_high"]["value"]),
            volume_ratio_min=float(d["volume_ratio_min"]["value"]),
            tp_atr_multiplier=float(d["tp_atr_multiplier"]["value"]),
            sl_atr_multiplier=float(d["sl_atr_multiplier"]["value"]),
            min_confidence=float(d["min_confidence"]["value"]),
            adx_trend_min=float(d["adx_trend_min"]["value"]),
            bb_width_threshold=float(d["bb_width_threshold"]["value"]),
            max_hold_candles=int(d["max_hold_candles"]["value"]),
            max_active_buckets=int(d["max_active_buckets"]["value"]),
            body_ratio_min=float(d["body_ratio_min"]["value"]),
            wick_ratio_min=float(d["wick_ratio_min"]["value"]),
            compression_factor=float(d["compression_factor"]["value"]),
            ema_spread_threshold=float(d["ema_spread_threshold"]["value"]),
            atr_volatile_multiplier=float(d["atr_volatile_multiplier"]["value"]),
            atr_quiet_multiplier=float(d["atr_quiet_multiplier"]["value"]),
            retracement_min=float(d["retracement_min"]["value"]),
            retracement_max=float(d["retracement_max"]["value"]),
            anomaly_volume_stddev=float(d["anomaly_volume_stddev"]["value"]),
            anomaly_price_atr=float(d["anomaly_price_atr"]["value"]),
            momentum_acceleration_candles=int(d["momentum_acceleration_candles"]["value"]),
        )


# ---------------------------------------------------------------------------
# Signal pipeline typed results and rejections
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rejection:
    """Typed rejection returned by any pipeline stage."""

    stage: str  # 'regime' | 'trend' | 'pattern' | 'volume' | 'risk'
    reason: str


@dataclass(frozen=True)
class RegimeResult:
    regime: Regime
    adx: float
    ema_spread: float
    atr14: float
    atr50: float


@dataclass(frozen=True)
class TrendResult:
    direction: Direction
    ema_fast: float
    ema_slow: float
    rsi: float


@dataclass(frozen=True)
class PatternResult:
    pattern: PatternType
    direction: Direction
    confidence: float
    details: dict[str, Any]


@dataclass(frozen=True)
class VolumeResult:
    volume_ratio: float
    volume_ma20: float


# ---------------------------------------------------------------------------
# Signal — the output of the signal pipeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    """Fully constructed signal ready for risk validation and execution."""

    bot_id: str
    session_id: str
    env: str
    ts: int  # unix ms — signal creation time
    pair: str
    timeframe: str
    candle_ts: int  # unix ms — candle that triggered signal

    pattern: str
    direction: Direction
    confidence: float
    regime: str

    # layer pass/fail flags (0 = fail, 1 = pass)
    layer_regime: int
    layer_trend: int
    layer_momentum: int
    layer_volume: int
    layers_passed: int

    # order levels (computed by detector from ATR)
    entry_price: float
    tp_price: float
    sl_price: float

    # size is determined from confidence by risk/execution layer
    size_usdt: float


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


@dataclass
class BucketState:
    """Runtime state passed to the risk manager for validation."""

    active_positions: int
    last_ws_reconnect_ts: Optional[int]  # unix ms; None = never reconnected
    session_net_pnl: float  # resets 00:00 UTC
    current_ts: int  # unix ms


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    reason: Optional[str]  # None when passed


# ---------------------------------------------------------------------------
# App configuration (schema only — populated by startup layer from os.environ)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppConfig:
    """Full application configuration. Constructed by startup layer; never modified."""

    env: Env
    bot_id: str
    exchange: str
    api_key: str
    api_secret: str
    testnet: bool

    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str

    pair: str
    timeframe_entry: str
    timeframe_regime: str

    leverage: int
    bucket_size_usdt: float
    max_active_buckets: int

    telegram_token: str
    telegram_chat_id: str
    log_level: str

    @classmethod
    def from_mapping(cls, m: Mapping[str, str]) -> "AppConfig":
        """Construct from a string→string mapping (e.g. os.environ).
        Raises ValueError for missing or invalid keys.
        """
        required = [
            "ENV",
            "BOT_ID",
            "EXCHANGE",
            "API_KEY",
            "API_SECRET",
            "TESTNET",
            "DB_HOST",
            "DB_PORT",
            "DB_NAME",
            "DB_USER",
            "DB_PASSWORD",
            "PAIR",
            "TIMEFRAME_ENTRY",
            "TIMEFRAME_REGIME",
            "LEVERAGE",
            "BUCKET_SIZE_USDT",
            "MAX_ACTIVE_BUCKETS",
            "TELEGRAM_TOKEN",
            "TELEGRAM_CHAT_ID",
            "LOG_LEVEL",
        ]
        missing = [k for k in required if not m.get(k)]
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")

        return cls(
            env=Env(m["ENV"]),
            bot_id=m["BOT_ID"],
            exchange=m["EXCHANGE"],
            api_key=m["API_KEY"],
            api_secret=m["API_SECRET"],
            testnet=m["TESTNET"].lower() in ("1", "true", "yes"),
            db_host=m["DB_HOST"],
            db_port=int(m["DB_PORT"]),
            db_name=m["DB_NAME"],
            db_user=m["DB_USER"],
            db_password=m["DB_PASSWORD"],
            pair=m["PAIR"],
            timeframe_entry=m["TIMEFRAME_ENTRY"],
            timeframe_regime=m["TIMEFRAME_REGIME"],
            leverage=int(m["LEVERAGE"]),
            bucket_size_usdt=float(m["BUCKET_SIZE_USDT"]),
            max_active_buckets=int(m["MAX_ACTIVE_BUCKETS"]),
            telegram_token=m["TELEGRAM_TOKEN"],
            telegram_chat_id=m["TELEGRAM_CHAT_ID"],
            log_level=m["LOG_LEVEL"].upper(),
        )


# ---------------------------------------------------------------------------
# Pure utilities
# ---------------------------------------------------------------------------


def get_trading_session(ts_ms: int) -> TradingSession:
    """Return the trading session for a given Unix millisecond timestamp (UTC hour)."""
    hour = (ts_ms // 3_600_000) % 24
    if 13 <= hour < 16:
        return TradingSession.OVERLAP
    if 8 <= hour < 16:
        return TradingSession.LONDON
    if 13 <= hour < 21:
        return TradingSession.US
    return TradingSession.ASIAN


def session_volume_multiplier(session: TradingSession) -> float:
    """Volume ratio multiplier applied per session (see CLAUDE.md §22)."""
    return {
        TradingSession.ASIAN: 1.2,
        TradingSession.LONDON: 1.0,
        TradingSession.US: 0.9,
        TradingSession.OVERLAP: 0.9,
    }[session]


def session_confidence_multiplier(session: TradingSession) -> float:
    """Confidence multiplier applied per session (see CLAUDE.md §22)."""
    return {
        TradingSession.ASIAN: 1.1,
        TradingSession.LONDON: 1.0,
        TradingSession.US: 1.0,
        TradingSession.OVERLAP: 1.0,
    }[session]


def compute_candle_geometry(open_: float, high: float, low: float, close: float) -> dict[str, float | str]:
    """Compute precomputed geometry fields for a completed candle. Pure function."""
    body_size = abs(close - open_)
    total_range = high - low
    body_ratio = body_size / total_range if total_range > 0.0 else 0.0
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low
    direction = "bullish" if close >= open_ else "bearish"
    return {
        "body_size": body_size,
        "total_range": total_range,
        "body_ratio": body_ratio,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "direction": direction,
    }


def compute_liquidation_price(
    entry: float,
    direction: Direction,
    leverage: int,
    maintenance_margin_rate: float = 0.005,
) -> float:
    """Compute liquidation price per CLAUDE.md §17 formula."""
    if direction is Direction.LONG:
        return entry * (1.0 - 1.0 / leverage + maintenance_margin_rate)
    return entry * (1.0 + 1.0 / leverage - maintenance_margin_rate)


def round_trip_fee_pct() -> float:
    """Total round-trip cost: taker 0.04% × 2 + slippage 0.05% × 2."""
    return 0.04 + 0.04 + 0.05 + 0.05  # = 0.18 %


def load_params(path: str) -> Params:
    """Load params.json from disk and return a Params instance.
    This is the one concession: params loading lives here for convenience,
    called only by the boundary/startup layer.
    """
    with open(path) as fh:
        raw = json.load(fh)
    return Params.from_dict(raw)
