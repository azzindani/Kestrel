"""Unit tests for src/config.py (Layer 0 — Tier 0 coverage target: 95% branch / 100% function)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.config import (
    AppConfig,
    Direction,
    Env,
    Params,
    TradingSession,
    compute_candle_geometry,
    compute_liquidation_price,
    get_trading_session,
    round_trip_fee_pct,
    session_confidence_multiplier,
    session_volume_multiplier,
)
from tests.helpers.factories import make_app_config

# ---------------------------------------------------------------------------
# compute_candle_geometry
# ---------------------------------------------------------------------------


class TestComputeCandleGeometry:
    def test_compute_candle_geometry_bullish_returns_bullish_direction(self):
        result = compute_candle_geometry(100.0, 110.0, 95.0, 105.0)
        assert result["direction"] == "bullish"

    def test_compute_candle_geometry_bearish_returns_bearish_direction(self):
        result = compute_candle_geometry(105.0, 110.0, 95.0, 100.0)
        assert result["direction"] == "bearish"

    def test_compute_candle_geometry_doji_returns_bullish_when_close_equals_open(self):
        result = compute_candle_geometry(100.0, 105.0, 95.0, 100.0)
        assert result["direction"] == "bullish"

    def test_compute_candle_geometry_body_size_equals_abs_close_minus_open(self):
        result = compute_candle_geometry(100.0, 110.0, 90.0, 108.0)
        assert result["body_size"] == pytest.approx(8.0)

    def test_compute_candle_geometry_total_range_equals_high_minus_low(self):
        result = compute_candle_geometry(100.0, 115.0, 85.0, 105.0)
        assert result["total_range"] == pytest.approx(30.0)

    def test_compute_candle_geometry_upper_wick_correct(self):
        result = compute_candle_geometry(100.0, 115.0, 90.0, 108.0)
        # upper_wick = high - max(open, close) = 115 - 108 = 7
        assert result["upper_wick"] == pytest.approx(7.0)

    def test_compute_candle_geometry_lower_wick_correct(self):
        result = compute_candle_geometry(100.0, 115.0, 90.0, 108.0)
        # lower_wick = min(open, close) - low = 100 - 90 = 10
        assert result["lower_wick"] == pytest.approx(10.0)

    def test_compute_candle_geometry_zero_range_returns_zero_body_ratio(self):
        result = compute_candle_geometry(100.0, 100.0, 100.0, 100.0)
        assert result["body_ratio"] == pytest.approx(0.0)

    @given(
        open_=st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False),
        pct=st.floats(min_value=0.001, max_value=0.1, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_compute_candle_geometry_body_ratio_always_in_unit_interval(self, open_: float, pct: float):
        high = open_ * (1 + pct)
        low = open_ * (1 - pct)
        close = open_ * (1 + pct * 0.3)
        result = compute_candle_geometry(open_, high, low, close)
        assert 0.0 <= result["body_ratio"] <= 1.0


# ---------------------------------------------------------------------------
# compute_liquidation_price
# ---------------------------------------------------------------------------


class TestComputeLiquidationPrice:
    def test_compute_liquidation_price_long_is_below_entry(self):
        liq = compute_liquidation_price(1000.0, Direction.LONG, 20)
        assert liq < 1000.0

    def test_compute_liquidation_price_short_is_above_entry(self):
        liq = compute_liquidation_price(1000.0, Direction.SHORT, 20)
        assert liq > 1000.0

    def test_compute_liquidation_price_long_formula_correct(self):
        entry = 1000.0
        leverage = 20
        mmr = 0.005
        expected = entry * (1.0 - 1.0 / leverage + mmr)
        assert compute_liquidation_price(entry, Direction.LONG, leverage, mmr) == pytest.approx(expected)

    def test_compute_liquidation_price_short_formula_correct(self):
        entry = 1000.0
        leverage = 20
        mmr = 0.005
        expected = entry * (1.0 + 1.0 / leverage - mmr)
        assert compute_liquidation_price(entry, Direction.SHORT, leverage, mmr) == pytest.approx(expected)

    def test_compute_liquidation_price_higher_leverage_closer_to_entry(self):
        entry = 1000.0
        liq_10x = compute_liquidation_price(entry, Direction.LONG, 10)
        liq_50x = compute_liquidation_price(entry, Direction.LONG, 50)
        assert liq_50x > liq_10x  # higher leverage → closer to entry

    @given(
        entry=st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        leverage=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=200)
    def test_compute_liquidation_price_long_always_below_entry(self, entry: float, leverage: int):
        liq = compute_liquidation_price(entry, Direction.LONG, leverage)
        assert liq < entry

    @given(
        entry=st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        leverage=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=200)
    def test_compute_liquidation_price_short_always_above_entry(self, entry: float, leverage: int):
        liq = compute_liquidation_price(entry, Direction.SHORT, leverage)
        assert liq > entry


# ---------------------------------------------------------------------------
# round_trip_fee_pct
# ---------------------------------------------------------------------------


class TestRoundTripFeePct:
    def test_round_trip_fee_pct_returns_018(self):
        assert round_trip_fee_pct() == pytest.approx(0.18)

    def test_round_trip_fee_pct_is_always_constant(self):
        assert round_trip_fee_pct() == round_trip_fee_pct()


# ---------------------------------------------------------------------------
# get_trading_session
# ---------------------------------------------------------------------------


class TestGetTradingSession:
    def test_get_trading_session_asian_at_midnight(self):
        # Hour 0 UTC
        ts = 0  # 1970-01-01 00:00:00 UTC
        assert get_trading_session(ts) is TradingSession.ASIAN

    def test_get_trading_session_london_at_0900(self):
        # 9 AM UTC
        ts = 9 * 3_600_000
        assert get_trading_session(ts) is TradingSession.LONDON

    def test_get_trading_session_overlap_at_1400(self):
        # 14:00 UTC — 13-16 overlap window
        ts = 14 * 3_600_000
        assert get_trading_session(ts) is TradingSession.OVERLAP

    def test_get_trading_session_us_after_overlap_at_1700(self):
        # 17:00 UTC — US session (13-21) but not overlap (13-16)
        ts = 17 * 3_600_000
        assert get_trading_session(ts) is TradingSession.US

    def test_get_trading_session_asian_after_us_at_2200(self):
        # 22:00 UTC — past US session
        ts = 22 * 3_600_000
        assert get_trading_session(ts) is TradingSession.ASIAN


# ---------------------------------------------------------------------------
# session multipliers
# ---------------------------------------------------------------------------


class TestSessionMultipliers:
    def test_session_volume_multiplier_asian_is_highest(self):
        asian = session_volume_multiplier(TradingSession.ASIAN)
        london = session_volume_multiplier(TradingSession.LONDON)
        assert asian > london

    def test_session_volume_multiplier_london_returns_1_0(self):
        assert session_volume_multiplier(TradingSession.LONDON) == pytest.approx(1.0)

    def test_session_confidence_multiplier_asian_above_1(self):
        assert session_confidence_multiplier(TradingSession.ASIAN) > 1.0

    def test_session_confidence_multiplier_london_returns_1_0(self):
        assert session_confidence_multiplier(TradingSession.LONDON) == pytest.approx(1.0)

    def test_session_volume_multiplier_all_sessions_positive(self):
        for session in TradingSession:
            assert session_volume_multiplier(session) > 0.0

    def test_session_confidence_multiplier_all_sessions_positive(self):
        for session in TradingSession:
            assert session_confidence_multiplier(session) > 0.0


# ---------------------------------------------------------------------------
# Params.from_dict
# ---------------------------------------------------------------------------


class TestParamsFromDict:
    def _valid_dict(self) -> dict:
        return {
            k: {"value": v}
            for k, v in [
                ("ema_fast", 9),
                ("ema_slow", 21),
                ("rsi_low", 45.0),
                ("rsi_high", 55.0),
                ("volume_ratio_min", 1.3),
                ("tp_atr_multiplier", 1.6),
                ("sl_atr_multiplier", 1.0),
                ("min_confidence", 0.55),
                ("adx_trend_min", 20.0),
                ("bb_width_threshold", 0.02),
                ("max_hold_candles", 4),
                ("max_active_buckets", 1),
                ("body_ratio_min", 0.6),
                ("wick_ratio_min", 2.0),
                ("compression_factor", 0.5),
                ("ema_spread_threshold", 0.001),
                ("atr_volatile_multiplier", 1.5),
                ("atr_quiet_multiplier", 0.5),
                ("retracement_min", 0.3),
                ("retracement_max", 0.5),
                ("anomaly_volume_stddev", 2.5),
                ("anomaly_price_atr", 2.5),
                ("momentum_acceleration_candles", 3),
            ]
        }

    def test_params_from_dict_valid_returns_params(self):
        p = Params.from_dict(self._valid_dict())
        assert p.ema_fast == 9
        assert p.ema_slow == 21

    def test_params_from_dict_missing_key_raises_value_error(self):
        d = self._valid_dict()
        del d["ema_fast"]
        with pytest.raises(ValueError, match="missing keys"):
            Params.from_dict(d)

    def test_params_from_dict_multiple_missing_keys_lists_all(self):
        d = self._valid_dict()
        del d["ema_fast"]
        del d["rsi_low"]
        with pytest.raises(ValueError) as exc:
            Params.from_dict(d)
        msg = str(exc.value)
        assert "ema_fast" in msg
        assert "rsi_low" in msg


# ---------------------------------------------------------------------------
# AppConfig.from_mapping
# ---------------------------------------------------------------------------


class TestAppConfigFromMapping:
    def _valid_env(self) -> dict:
        return {
            "ENV": "dev",
            "BOT_ID": "test",
            "EXCHANGE": "binance",
            "API_KEY": "k",
            "API_SECRET": "s",
            "TESTNET": "true",
            "DB_HOST": "localhost",
            "DB_PORT": "5432",
            "DB_NAME": "kestrel",
            "DB_USER": "u",
            "DB_PASSWORD": "p",
            "PAIR": "BTCUSDT",
            "TIMEFRAME_ENTRY": "5m",
            "TIMEFRAME_REGIME": "15m",
            "LEVERAGE": "20",
            "BUCKET_SIZE_USDT": "10.0",
            "MAX_ACTIVE_BUCKETS": "1",
            "TELEGRAM_TOKEN": "t",
            "TELEGRAM_CHAT_ID": "c",
            "LOG_LEVEL": "DEBUG",
        }

    def test_app_config_from_mapping_valid_returns_config(self):
        cfg = AppConfig.from_mapping(self._valid_env())
        assert cfg.env is Env.DEV
        assert cfg.leverage == 20

    def test_app_config_from_mapping_testnet_true_string_parses_to_bool(self):
        env = self._valid_env()
        env["TESTNET"] = "true"
        cfg = AppConfig.from_mapping(env)
        assert cfg.testnet is True

    def test_app_config_from_mapping_testnet_false_string_parses_to_bool(self):
        env = self._valid_env()
        env["TESTNET"] = "false"
        cfg = AppConfig.from_mapping(env)
        assert cfg.testnet is False

    def test_app_config_from_mapping_missing_key_raises_value_error(self):
        env = self._valid_env()
        del env["API_KEY"]
        with pytest.raises(ValueError, match="Missing required env vars"):
            AppConfig.from_mapping(env)

    def test_app_config_from_mapping_prod_env_sets_prod(self):
        env = self._valid_env()
        env["ENV"] = "prod"
        cfg = AppConfig.from_mapping(env)
        assert cfg.env is Env.PROD

    def test_app_config_from_mapping_log_level_is_uppercased(self):
        env = self._valid_env()
        env["LOG_LEVEL"] = "debug"
        cfg = AppConfig.from_mapping(env)
        assert cfg.log_level == "DEBUG"

    def test_app_config_factory_creates_valid_config(self):
        cfg = make_app_config()
        assert cfg.env is Env.DEV
        assert cfg.leverage == 20
        assert cfg.bucket_size_usdt == 10.0
