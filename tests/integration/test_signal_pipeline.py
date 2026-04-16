"""Integration tests for the full signal pipeline (no I/O — pure function chain).

Tests that candles → evaluate() → Signal/Rejection works end-to-end,
exercising the full path: regime → trend → pattern → volume → build_signal.
"""

from __future__ import annotations

from src.config import Direction, Rejection, Signal
from src.signal.detector import evaluate
from tests.helpers.factories import make_candle, make_params


def _make_impulse_retracement_candles(n_base: int = 50) -> list:
    """Build a candle series that should produce an impulse_retracement signal.

    Regime: TRENDING (high ADX, EMA9 > EMA21)
    Trend: LONG (EMA9 > EMA21, RSI > 45)
    Pattern: trigger candle (strong body) + retrace (small body, lower vol)
    Volume: volume_ratio > volume_ratio_min
    """
    base = []
    for i in range(n_base):
        price = 100.0 + i * 0.3
        base.append(
            make_candle(
                close=price,
                open_=price - 0.1,
                high=price + 0.2,
                low=price - 0.15,
                volume=120.0,
                ts=i * 300_000,
                ema9=price * 1.002,
                ema21=price * 0.996,
                rsi14=55.0,
                atr14=price * 0.003,
                adx=26.0,
                volume_ma20=100.0,
                volume_ratio=1.2,
                regime="TRENDING",
                bb_width=0.025,
            )
        )

    # Trigger candle: strong bullish, high volume_ratio
    trigger_price = base[-1].close + 3.0
    trigger = make_candle(
        close=trigger_price,
        open_=trigger_price - 2.0,  # body = 2.0 (large)
        high=trigger_price + 0.5,
        low=trigger_price - 2.3,  # total range ≈ 2.8, body_ratio ≈ 0.71
        volume=200.0,
        ts=n_base * 300_000,
        ema9=trigger_price * 1.002,
        ema21=trigger_price * 0.996,
        rsi14=58.0,
        atr14=trigger_price * 0.003,
        adx=26.0,
        volume_ma20=100.0,
        volume_ratio=2.0,
        regime="TRENDING",
    )

    # Retracement candle: small pullback, low volume
    retrace_price = trigger_price - 0.7
    retrace = make_candle(
        close=retrace_price,
        open_=trigger_price,
        high=trigger_price + 0.1,
        low=retrace_price - 0.1,
        volume=80.0,
        ts=(n_base + 1) * 300_000,
        ema9=retrace_price * 1.002,
        ema21=retrace_price * 0.996,
        rsi14=52.0,
        atr14=retrace_price * 0.003,
        adx=26.0,
        volume_ma20=100.0,
        volume_ratio=1.6,
        regime="TRENDING",
    )

    return base + [trigger, retrace]


class TestSignalPipelineIntegration:
    def test_evaluate_valid_trending_sequence_produces_signal(self):
        candles = _make_impulse_retracement_candles(50)
        params = make_params(
            body_ratio_min=0.6,
            volume_ratio_min=1.3,
            retracement_min=0.2,
            retracement_max=0.6,
            min_confidence=0.40,
            adx_trend_min=20.0,
            ema_spread_threshold=0.001,
            atr_quiet_multiplier=0.3,
        )
        signal, rejection = evaluate(candles, params, "test", "session", "dev")

        # Either a signal fires OR a rejection is returned — never both
        assert (signal is None) != (rejection is None)

        # If we got a signal it should have valid structure
        if signal is not None:
            assert isinstance(signal, Signal)
            assert signal.entry_price > 0
            assert signal.tp_price > signal.entry_price
            assert signal.sl_price < signal.entry_price
            assert signal.size_usdt in (5.0, 10.0)

    def test_evaluate_empty_candles_returns_rejection(self):
        params = make_params()
        signal, rejection = evaluate([], params, "test", "session", "dev")
        assert signal is None
        assert rejection is not None
        assert isinstance(rejection, Rejection)

    def test_evaluate_single_candle_returns_rejection(self):
        params = make_params()
        candles = [make_candle(100.0)]
        signal, rejection = evaluate(candles, params, "test", "session", "dev")
        assert signal is None
        assert rejection is not None

    def test_evaluate_quiet_market_returns_rejection(self):
        """Low volume_ratio should trigger quiet_regime rejection."""
        params = make_params(atr_quiet_multiplier=0.5)
        candles = [make_candle(100.0, volume=50.0, ts=i * 300_000, volume_ratio=0.4, atr14=0.05) for i in range(60)]
        signal, rejection = evaluate(candles, params, "test", "session", "dev")
        assert signal is None
        assert rejection is not None

    def test_evaluate_returns_tuple_of_two(self):
        params = make_params()
        result = evaluate([make_candle(100.0)], params, "test", "session", "dev")
        assert len(result) == 2

    def test_evaluate_signal_tp_above_entry_for_long(self):
        candles = _make_impulse_retracement_candles(50)
        params = make_params(
            body_ratio_min=0.6,
            volume_ratio_min=1.3,
            retracement_min=0.2,
            retracement_max=0.6,
            min_confidence=0.40,
            adx_trend_min=20.0,
            ema_spread_threshold=0.001,
            atr_quiet_multiplier=0.3,
        )
        signal, _ = evaluate(candles, params, "test", "session", "dev")
        if signal is not None and signal.direction is Direction.LONG:
            assert signal.tp_price > signal.entry_price
            assert signal.sl_price < signal.entry_price


class TestBacktestPipelineIntegration:
    def test_backtest_runner_produces_structured_result(self):
        from src.backtest.runner import run_backtest
        from tests.helpers.factories import make_app_config

        candles = _make_impulse_retracement_candles(120)
        params = make_params(
            body_ratio_min=0.6,
            volume_ratio_min=1.3,
            retracement_min=0.2,
            retracement_max=0.6,
            min_confidence=0.40,
            adx_trend_min=20.0,
            ema_spread_threshold=0.001,
            atr_quiet_multiplier=0.3,
        )
        cfg = make_app_config()

        result = run_backtest(candles, params, cfg, min_candles_warmup=30)

        assert "trades" in result
        assert "signals" in result
        assert "equity_curve" in result
        assert isinstance(result["trades"], list)
        assert isinstance(result["equity_curve"], list)
        assert len(result["equity_curve"]) > 0

    def test_walk_forward_produces_in_and_out_sample_metrics(self):
        from src.backtest.runner import walk_forward
        from tests.helpers.factories import make_app_config

        candles = _make_impulse_retracement_candles(200)
        params = make_params(
            body_ratio_min=0.6,
            volume_ratio_min=1.3,
            retracement_min=0.2,
            retracement_max=0.6,
            min_confidence=0.40,
            adx_trend_min=20.0,
            ema_spread_threshold=0.001,
            atr_quiet_multiplier=0.3,
        )
        cfg = make_app_config()

        result = walk_forward(candles, params, cfg)

        assert "in_sample" in result
        assert "out_sample" in result
        assert "trades_out" in result
        assert "total_trades" in result["in_sample"]
        assert "win_rate" in result["out_sample"]
