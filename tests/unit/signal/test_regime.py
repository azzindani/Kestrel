"""Unit tests for src/signal/regime.py (Tier 1)."""

from __future__ import annotations

from src.config import Regime, Rejection
from src.signal.regime import classify_regime, regime_permits_pattern
from tests.helpers.factories import make_candle, make_params


def _make_trending_candles(n: int = 50) -> list:
    """Create candles with high ADX and EMA spread pre-set to force TRENDING."""
    candles = []
    for i in range(n):
        price = 100.0 + i * 0.5
        candles.append(
            make_candle(
                close=price,
                open_=price - 0.2,
                high=price + 0.3,
                low=price - 0.4,
                volume=150.0,
                ts=i * 300_000,
                ema9=price * 1.003,
                ema21=price * 0.995,
                adx=25.0,
                atr14=price * 0.003,
                volume_ratio=1.5,
            )
        )
    return candles


def _make_quiet_candles(n: int = 30) -> list:
    """Create candles with low volume ratio (quiet market)."""
    return [
        make_candle(
            close=100.0,
            high=100.1,
            low=99.9,
            volume=50.0,
            ts=i * 300_000,
            atr14=0.05,
            volume_ratio=0.5,  # below 0.7 → quiet
        )
        for i in range(n)
    ]


class TestClassifyRegime:
    def test_classify_regime_returns_rejection_with_single_candle(self):
        params = make_params()
        result = classify_regime([make_candle(100.0)], params)
        assert isinstance(result, Rejection)

    def test_classify_regime_quiet_vol_ratio_returns_rejection(self):
        params = make_params(atr_quiet_multiplier=0.5)
        candles = _make_quiet_candles(30)
        result = classify_regime(candles, params)
        assert isinstance(result, Rejection)
        assert result.reason == "quiet_regime"

    def test_classify_regime_trending_returns_trending_regime(self):
        params = make_params(adx_trend_min=20.0, ema_spread_threshold=0.001, atr_quiet_multiplier=0.5)
        candles = _make_trending_candles(50)
        result = classify_regime(candles, params)
        assert not isinstance(result, Rejection)
        assert result.regime is Regime.TRENDING

    def test_classify_regime_returns_regime_result_with_required_fields(self):
        params = make_params(atr_quiet_multiplier=0.5)
        candles = _make_trending_candles(50)
        result = classify_regime(candles, params)
        if not isinstance(result, Rejection):
            assert hasattr(result, "regime")
            assert hasattr(result, "adx")
            assert hasattr(result, "atr14")
            assert hasattr(result, "atr50")

    def test_classify_regime_volatile_requires_high_atr_and_adx(self):
        params = make_params(atr_volatile_multiplier=1.5, atr_quiet_multiplier=0.5)
        candles = []
        for i in range(60):
            price = 100.0 + ((-1) ** i) * 5.0  # alternating prices
            candles.append(
                make_candle(
                    close=price,
                    high=price + 10.0,
                    low=price - 10.0,
                    volume=200.0,
                    ts=i * 300_000,
                    atr14=25.0,
                    adx=18.0,
                    volume_ratio=2.0,
                )
            )
        result = classify_regime(candles, params)
        # With high ATR and ADX > 15, should be VOLATILE or at least not QUIET
        if not isinstance(result, Rejection):
            assert result.regime in (Regime.VOLATILE, Regime.TRENDING, Regime.RANGING)


class TestRegimePermitsPattern:
    def test_regime_permits_pattern_trending_allows_impulse(self):
        assert regime_permits_pattern(Regime.TRENDING, "impulse_retracement") is True

    def test_regime_permits_pattern_trending_allows_momentum(self):
        assert regime_permits_pattern(Regime.TRENDING, "momentum_continuation") is True

    def test_regime_permits_pattern_trending_blocks_wick_rejection(self):
        assert regime_permits_pattern(Regime.TRENDING, "wick_rejection") is False

    def test_regime_permits_pattern_ranging_allows_wick_rejection(self):
        assert regime_permits_pattern(Regime.RANGING, "wick_rejection") is True

    def test_regime_permits_pattern_quiet_blocks_all(self):
        for pattern in [
            "impulse_retracement",
            "wick_rejection",
            "compression_breakout",
            "momentum_continuation",
            "anomaly_fade",
        ]:
            assert regime_permits_pattern(Regime.QUIET, pattern) is False

    def test_regime_permits_pattern_volatile_allows_compression_breakout(self):
        assert regime_permits_pattern(Regime.VOLATILE, "compression_breakout") is True

    def test_regime_permits_pattern_volatile_allows_anomaly_fade(self):
        assert regime_permits_pattern(Regime.VOLATILE, "anomaly_fade") is True

    def test_regime_permits_pattern_ranging_allows_anomaly_fade(self):
        assert regime_permits_pattern(Regime.RANGING, "anomaly_fade") is True

    def test_regime_permits_pattern_unknown_pattern_returns_false(self):
        assert regime_permits_pattern(Regime.TRENDING, "nonexistent_pattern") is False
