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
    DEV = "dev"  # Phase 1 — labs: SimulationExecution (paper), many bots, no venue.
    STAGING = "staging"  # Phase 2 — quarantine: LiveExecution on a demo/testnet venue
    #                      (e.g. BingX VST) with virtual money. Routes to the live code
    #                      path (env is not DEV) but TESTNET=true → set_sandbox_mode.
    PROD = "prod"  # Phase 3 — live: LiveExecution, real keys, real capital (§18 gated).


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


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
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
    rsi_long_max: float
    rsi_short_min: float
    # --- position sizing / capital management (equity-scaled; see signal/sizing.py) ---
    size_fraction_full: float = 1.0
    size_fraction_half: float = 0.5
    size_min_usdt: float = 1.0
    drawdown_derisk_threshold: float = 0.20
    drawdown_derisk_factor: float = 0.5
    consec_loss_cooloff: int = 3
    consec_loss_factor: float = 0.5
    # --- trailing-close (ratchet the exit toward price as profit grows; execution/simulation.py + backtest/runner.py) ---
    trailing_enabled: bool = False
    trail_activation_r: float = 1.0
    trail_distance_r: float = 0.8
    # --- fixed-percent reward:risk TP/SL (alternative to ATR-based; signal/detector.py) ---
    # When enabled, TP/SL are placed at fixed fractions of entry price instead of ATR
    # multiples, giving a deterministic reward:risk = tp_pct / sl_pct. The stop is
    # clamped to stay INSIDE the liquidation distance (~1/leverage) so liquidation can
    # never front-run it. Composes with trailing (the trail R-unit becomes sl_pct×entry).
    tp_sl_pct_enabled: bool = False
    tp_pct: float = 0.05
    sl_pct: float = 0.025
    # --- risk hardening: cap per-trade loss (signal/sizing.py cap_size_for_risk) ---
    # Max fraction of bucket equity a single stop-out may lose. 0.0 ⇒ disabled.
    max_loss_pct_per_trade: float = 0.0
    # --- confluence momentum (signal/patterns.py mom_adx / triple_mom) ---
    # ADX floor above which a price streak is treated as a STRONG-trend momentum
    # entry. Distinct from adx_trend_min (the regime classifier threshold): this is
    # the entry gate validated for the mom_adx / triple_mom patterns. Optional so
    # older params.json (without the key) loads unchanged at the validated default.
    adx_strong_min: float = 25.0

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
            "rsi_long_max",
            "rsi_short_min",
            "size_fraction_full",
            "size_fraction_half",
            "size_min_usdt",
            "drawdown_derisk_threshold",
            "drawdown_derisk_factor",
            "consec_loss_cooloff",
            "consec_loss_factor",
            "trailing_enabled",
            "trail_activation_r",
            "trail_distance_r",
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
            rsi_long_max=float(d["rsi_long_max"]["value"]),
            rsi_short_min=float(d["rsi_short_min"]["value"]),
            size_fraction_full=float(d["size_fraction_full"]["value"]),
            size_fraction_half=float(d["size_fraction_half"]["value"]),
            size_min_usdt=float(d["size_min_usdt"]["value"]),
            drawdown_derisk_threshold=float(d["drawdown_derisk_threshold"]["value"]),
            drawdown_derisk_factor=float(d["drawdown_derisk_factor"]["value"]),
            consec_loss_cooloff=int(d["consec_loss_cooloff"]["value"]),
            consec_loss_factor=float(d["consec_loss_factor"]["value"]),
            trailing_enabled=bool(d["trailing_enabled"]["value"]),
            trail_activation_r=float(d["trail_activation_r"]["value"]),
            trail_distance_r=float(d["trail_distance_r"]["value"]),
            # Optional (backward compatible): older params.json without these keys
            # loads with the risk cap disabled and ATR-based TP/SL.
            max_loss_pct_per_trade=(
                float(d["max_loss_pct_per_trade"]["value"]) if "max_loss_pct_per_trade" in d else 0.0
            ),
            tp_sl_pct_enabled=(bool(d["tp_sl_pct_enabled"]["value"]) if "tp_sl_pct_enabled" in d else False),
            tp_pct=(float(d["tp_pct"]["value"]) if "tp_pct" in d else 0.05),
            sl_pct=(float(d["sl_pct"]["value"]) if "sl_pct" in d else 0.025),
            adx_strong_min=(float(d["adx_strong_min"]["value"]) if "adx_strong_min" in d else 25.0),
        )


# ---------------------------------------------------------------------------
# Signal pipeline typed results and rejections
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rejection:
    """Typed rejection returned by any pipeline stage."""

    stage: str  # 'regime' | 'trend' | 'pattern' | 'volume' | 'risk'
    reason: str


@dataclass(frozen=True, slots=True)
class RegimeResult:
    regime: Regime
    adx: float
    ema_spread: float
    atr14: float
    atr50: float


@dataclass(frozen=True, slots=True)
class TrendResult:
    direction: Direction
    ema_fast: float
    ema_slow: float
    rsi: float


@dataclass(frozen=True, slots=True)
class PatternResult:
    pattern: PatternType
    direction: Direction
    confidence: float
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VolumeResult:
    volume_ratio: float
    volume_ma20: float


# ---------------------------------------------------------------------------
# Signal — the output of the signal pipeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
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


@dataclass(slots=True)
class BucketState:
    """Runtime state passed to the risk manager for validation."""

    active_positions: int
    last_ws_reconnect_ts: Optional[int]  # unix ms; None = never reconnected
    session_net_pnl: float  # resets 00:00 UTC
    current_ts: int  # unix ms


@dataclass(frozen=True, slots=True)
class SizingState:
    """Per-bucket capital state for equity-scaled position sizing (signal/sizing.py).

    Assembled from authoritative DB state (CLAUDE.md §11) and passed into the
    detector so position size compounds with realised PnL instead of using a
    fixed bucket. None ⇒ fall back to the fixed-bucket model (backward compatible).
    """

    equity_usdt: float  # current bucket equity = starting bucket + cumulative realised PnL
    peak_equity_usdt: float  # high-water mark of equity (for drawdown de-risking)
    consec_losses: int  # trailing consecutive losing trades (for cool-off)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    passed: bool
    reason: Optional[str]  # None when passed


# ---------------------------------------------------------------------------
# App configuration (schema only — populated by startup layer from os.environ)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
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

    # Per-bot strategy overrides for the multi-bot bake-off (set by
    # load_bot_configs from bots.json; None ⇒ use the global params / all patterns).
    params: Optional["Params"] = None
    enabled_patterns: Optional[tuple] = None
    strategy: str = "default"

    # Market-data transport: "ws" (ccxt.pro WebSocket, default) or "poll" (ccxt
    # REST polling). Poll is reliable for HIGH timeframes where the WS rollover
    # update is sparse/missed (a 4h close is one event every 4h — a missed WS push
    # = a lost candle). Optional/back-compatible: absent ⇒ "ws".
    feed_mode: str = "ws"

    # Execution cost model. False (default) = TAKER: market fills with taker fee +
    # slippage on every entry and exit (the live-safe model). True = MAKER: model
    # post-only LIMIT fills — entries and take-profit exits pay the maker fee with
    # NO slippage (you set the fill price); stop-loss / timeout / liquidation still
    # market out (taker + slippage). This cuts the fixed round-trip cost ~4x AND
    # removes entry/TP slippage, which lifts WINS more than losses → directly
    # improves the realised reward:risk (the "lose 10% / win 3%" asymmetry).
    # CAVEAT: optimistic for MOMENTUM entries — a real post-only limit may not fill
    # on a breakout — so it is a best-case ceiling, not a guarantee, and it does NOT
    # by itself create edge. Set MAKER_EXECUTION=true for the paper lab. Absent ⇒ False.
    maker_execution: bool = False

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
            feed_mode=(m.get("FEED_MODE") or "ws").lower(),
            maker_execution=(m.get("MAKER_EXECUTION") or "").lower() in ("1", "true", "yes"),
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


def load_bot_configs(path: str, base: "AppConfig", base_params: "Optional[Params]" = None) -> "list[AppConfig]":
    """Load bots.json and return one AppConfig per bot entry.

    Falls back to [base] if bots.json is absent or empty (single-bot mode).
    Each entry must have 'bot_id' and 'pair'; all other fields are optional.

    Per-bot overridable fields:
        bot_id, pair, timeframe_entry, timeframe_regime, max_active_buckets

    Per-bot strategy (multi-bot bake-off; 'params' override requires base_params):
        strategy  — label for grouping/reporting (also encode it into bot_id)
        patterns  — list of enabled pattern names; None ⇒ all registered patterns
        params    — dict of params.json overrides (e.g. {"tp_atr_multiplier": 2.4})

    Shared fields inherited from base .env: exchange, api_*, db_*, leverage,
    bucket_size_usdt, telegram_*, log_level, env.

    Raises ValueError if an entry is missing 'bot_id'/'pair' or names an
    unknown params override key.
    """
    import dataclasses

    try:
        with open(path) as fh:
            entries = json.load(fh)
    except FileNotFoundError:
        return [base]

    if not isinstance(entries, list) or not entries:
        return [base]

    valid_param_keys = {f.name for f in dataclasses.fields(Params)}

    configs: list[AppConfig] = []
    for i, entry in enumerate(entries):
        missing = [k for k in ("bot_id", "pair") if not entry.get(k)]
        if missing:
            raise ValueError(f"bots.json entry {i} missing required fields: {', '.join(missing)}")

        # Per-bot params: merge overrides onto the base params set.
        bot_params: "Optional[Params]" = None
        overrides = entry.get("params")
        if overrides:
            if base_params is None:
                raise ValueError(f"bots.json entry {i} has 'params' but no base params provided")
            bad = [k for k in overrides if k not in valid_param_keys]
            if bad:
                raise ValueError(f"bots.json entry {i} unknown params keys: {', '.join(bad)}")
            bot_params = dataclasses.replace(base_params, **overrides)
        elif base_params is not None and (entry.get("patterns") or entry.get("strategy")):
            bot_params = base_params  # concrete params set for a strategy bot

        patterns = entry.get("patterns")
        enabled = tuple(patterns) if patterns else None

        configs.append(
            dataclasses.replace(
                base,
                bot_id=entry["bot_id"],
                pair=entry["pair"],
                timeframe_entry=entry.get("timeframe_entry", base.timeframe_entry),
                timeframe_regime=entry.get("timeframe_regime", base.timeframe_regime),
                max_active_buckets=int(entry.get("max_active_buckets", base.max_active_buckets)),
                params=bot_params,
                enabled_patterns=enabled,
                strategy=entry.get("strategy", "default"),
            )
        )
    return configs
