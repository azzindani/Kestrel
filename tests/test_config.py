"""Unit tests for src/config.py"""
import pytest

from src.config import (
    AppConfig, Direction, Env, Params, Regime, TradingSession,
    compute_candle_geometry, compute_liquidation_price, get_trading_session,
    round_trip_fee_pct, session_confidence_multiplier, session_volume_multiplier,
)


class TestCandleGeometry:
    def test_bullish_candle(self):
        g = compute_candle_geometry(100.0, 110.0, 95.0, 108.0)
        assert g["direction"] == "bullish"
        assert g["body_size"] == pytest.approx(8.0)
        assert g["total_range"] == pytest.approx(15.0)
        assert g["body_ratio"] == pytest.approx(8.0 / 15.0)
        assert g["upper_wick"] == pytest.approx(2.0)
        assert g["lower_wick"] == pytest.approx(5.0)

    def test_bearish_candle(self):
        g = compute_candle_geometry(108.0, 110.0, 95.0, 100.0)
        assert g["direction"] == "bearish"
        assert g["body_size"] == pytest.approx(8.0)

    def test_doji_zero_range_body_ratio(self):
        g = compute_candle_geometry(100.0, 100.0, 100.0, 100.0)
        assert g["body_ratio"] == pytest.approx(0.0)
        assert g["total_range"] == pytest.approx(0.0)


class TestLiquidationPrice:
    def test_long_liq_below_entry(self):
        liq = compute_liquidation_price(100.0, Direction.LONG, 20)
        assert liq < 100.0

    def test_short_liq_above_entry(self):
        liq = compute_liquidation_price(100.0, Direction.SHORT, 20)
        assert liq > 100.0

    def test_long_formula(self):
        # entry × (1 - 1/leverage + mmr)
        expected = 100.0 * (1.0 - 1.0 / 20 + 0.005)
        assert compute_liquidation_price(100.0, Direction.LONG, 20) == pytest.approx(expected)


class TestRoundTripFee:
    def test_total_is_0_18_pct(self):
        assert round_trip_fee_pct() == pytest.approx(0.18)


class TestTradingSession:
    def test_asian_session(self):
        # 04:00 UTC → Asian
        ts = 4 * 3_600_000
        assert get_trading_session(ts) is TradingSession.ASIAN

    def test_london_session(self):
        # 10:00 UTC → London
        ts = 10 * 3_600_000
        assert get_trading_session(ts) is TradingSession.LONDON

    def test_overlap_session(self):
        # 14:00 UTC → Overlap
        ts = 14 * 3_600_000
        assert get_trading_session(ts) is TradingSession.OVERLAP

    def test_session_volume_multipliers(self):
        assert session_volume_multiplier(TradingSession.ASIAN) == pytest.approx(1.2)
        assert session_volume_multiplier(TradingSession.LONDON) == pytest.approx(1.0)
        assert session_volume_multiplier(TradingSession.US) == pytest.approx(0.9)


class TestAppConfig:
    def test_from_mapping_valid(self):
        env = {
            "ENV": "dev", "BOT_ID": "dev-BTCUSDT-5m-01",
            "EXCHANGE": "binance", "API_KEY": "k", "API_SECRET": "s", "TESTNET": "true",
            "DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": "kestrel",
            "DB_USER": "u", "DB_PASSWORD": "p",
            "PAIR": "BTCUSDT", "TIMEFRAME_ENTRY": "5m", "TIMEFRAME_REGIME": "15m",
            "LEVERAGE": "20", "BUCKET_SIZE_USDT": "10.0", "MAX_ACTIVE_BUCKETS": "1",
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "c", "LOG_LEVEL": "DEBUG",
        }
        cfg = AppConfig.from_mapping(env)
        assert cfg.env is Env.DEV
        assert cfg.leverage == 20
        assert cfg.testnet is True

    def test_from_mapping_missing_raises(self):
        with pytest.raises(ValueError, match="Missing required env vars"):
            AppConfig.from_mapping({"ENV": "dev"})
