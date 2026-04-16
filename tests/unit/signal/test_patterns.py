"""Unit tests for src/signal/patterns.py (Tier 1)."""

from __future__ import annotations

from src.config import Direction, PatternType
from src.signal.patterns import (
    detect_anomaly_fade,
    detect_compression_breakout,
    detect_impulse_retracement,
    detect_momentum_continuation,
    detect_wick_rejection,
    registry,
)
from tests.helpers.factories import make_candle, make_params

# ---------------------------------------------------------------------------
# Registry contract tests
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_registry_contains_all_five_patterns(self):
        expected = {
            "impulse_retracement",
            "wick_rejection",
            "compression_breakout",
            "momentum_continuation",
            "anomaly_fade",
        }
        assert expected == set(registry.keys())

    def test_registry_impulse_retracement_is_callable(self):
        assert callable(registry["impulse_retracement"])

    def test_registry_all_values_are_callable(self):
        for fn in registry.values():
            assert callable(fn)


# ---------------------------------------------------------------------------
# impulse_retracement
# ---------------------------------------------------------------------------


class TestImpulseRetracement:
    def _impulse_setup(
        self,
        trigger_close: float = 100.0,
        trigger_open: float = 93.5,
        retrace_close: float = 97.0,
        retrace_open: float = 100.0,
        trigger_vol: float = 160.0,
        retrace_vol: float = 80.0,
    ) -> list:
        """Create a valid impulse + retracement sequence."""
        params = make_params(body_ratio_min=0.6, volume_ratio_min=1.3, retracement_min=0.3, retracement_max=0.5)

        # 20 base candles for volume MA
        base = [make_candle(90.0, volume=100.0, ts=i * 300_000) for i in range(20)]

        # Trigger: strong bullish candle
        trigger = make_candle(
            close=trigger_close,
            open_=trigger_open,
            high=trigger_close + 0.5,
            low=trigger_open - 0.5,
            volume=trigger_vol,
            ts=20 * 300_000,
        )

        # Retrace: small bearish pullback (doesn't close below trigger open)
        retrace = make_candle(
            close=retrace_close,
            open_=retrace_open,
            high=retrace_open + 0.5,
            low=retrace_close - 0.2,
            volume=retrace_vol,
            ts=21 * 300_000,
        )

        return base + [trigger, retrace], params

    def test_detect_impulse_retracement_valid_long_fires(self):
        candles, params = self._impulse_setup()
        result = detect_impulse_retracement(candles, params)
        assert result is not None
        assert result.direction is Direction.LONG
        assert result.pattern is PatternType.IMPULSE_RETRACEMENT

    def test_detect_impulse_retracement_insufficient_candles_returns_none(self):
        params = make_params()
        assert detect_impulse_retracement([], params) is None
        assert detect_impulse_retracement([make_candle(100.0)], params) is None
        assert detect_impulse_retracement([make_candle(100.0), make_candle(100.0)], params) is None

    def test_detect_impulse_retracement_low_body_ratio_returns_none(self):
        """Trigger has a small body (body_ratio < min) — pattern should not fire."""
        params = make_params(body_ratio_min=0.6)
        base = [make_candle(90.0, volume=100.0) for _ in range(20)]
        # Trigger with low body: high-low range of 10, body of 1
        trigger = make_candle(close=100.0, open_=99.0, high=104.0, low=94.0, volume=200.0)
        retrace = make_candle(close=99.5, open_=100.0, high=100.5, low=99.3, volume=80.0)
        result = detect_impulse_retracement(base + [trigger, retrace], params)
        assert result is None

    def test_detect_impulse_retracement_high_retrace_vol_returns_none(self):
        """Retrace volume must be lower than trigger volume."""
        candles, params = self._impulse_setup(trigger_vol=80.0, retrace_vol=160.0)
        result = detect_impulse_retracement(candles, params)
        assert result is None

    def test_detect_impulse_retracement_retrace_too_large_returns_none(self):
        """Retracement > max fraction means too deep, pattern rejected."""
        params = make_params(body_ratio_min=0.6, volume_ratio_min=1.3, retracement_min=0.3, retracement_max=0.5)
        base = [make_candle(90.0, volume=100.0) for _ in range(20)]
        trigger = make_candle(close=100.0, open_=93.5, high=100.5, low=93.0, volume=160.0)
        # retrace body = 5.0 out of trigger body = 6.5 → fraction ~0.77, exceeds max=0.5
        retrace = make_candle(close=95.0, open_=100.0, high=100.5, low=94.8, volume=80.0)
        result = detect_impulse_retracement(base + [trigger, retrace], params)
        assert result is None

    def test_detect_impulse_retracement_confidence_in_valid_range(self):
        candles, params = self._impulse_setup()
        result = detect_impulse_retracement(candles, params)
        assert result is not None
        assert 0.0 < result.confidence <= 1.0


# ---------------------------------------------------------------------------
# wick_rejection
# ---------------------------------------------------------------------------


class TestWickRejection:
    def _rejection_setup(self) -> tuple:
        """Create a valid wick rejection setup at support."""
        params = make_params(wick_ratio_min=2.0)

        # Base candles near support level at 100
        base = [make_candle(close=100.5 + i * 0.1, volume=100.0, ts=i * 300_000) for i in range(15)]

        # Rejection candle: big lower wick at support, close in top 30%
        # high=105, low=100, open=102, close=104
        # lower_wick = min(102,104) - 100 = 2
        # body = |104-102| = 2
        # wick_ratio = 2/2 = 1.0 → TOO LOW, let's adjust
        # Use: high=107, low=100, open=101, close=106
        # lower_wick = min(101,106) - 100 = 1
        # body = |106-101| = 5 → wick_ratio = 1/5 = 0.2 → still too low
        # Need: lower_wick / body >= 2.0
        # Use: close=102, open=101, high=103, low=98
        # body = 1, lower_wick = min(101,102) - 98 = 3, wick_ratio = 3/1 = 3 ✓
        # close_position = (102-98)/(103-98) = 4/5 = 0.8 ✓ (>0.70)
        # support = min(lows of last 10 candles)

        rejection = make_candle(
            close=102.0,
            open_=101.0,
            high=103.0,
            low=98.0,
            volume=100.0,
            ts=15 * 300_000,
        )

        return base + [rejection], params

    def test_detect_wick_rejection_valid_setup_fires(self):
        candles, params = self._rejection_setup()
        result = detect_wick_rejection(candles, params)
        assert result is not None
        assert result.direction is Direction.LONG

    def test_detect_wick_rejection_insufficient_candles_returns_none(self):
        params = make_params()
        assert detect_wick_rejection([], params) is None
        assert detect_wick_rejection([make_candle(100.0)], params) is None

    def test_detect_wick_rejection_small_wick_returns_none(self):
        """Upper wick smaller than wick_ratio_min × body — no trigger."""
        params = make_params(wick_ratio_min=2.0)
        candles = [make_candle(100.0 + i * 0.1, volume=100.0) for i in range(16)]
        # Final candle: small wick-to-body ratio
        small_wick = make_candle(close=101.0, open_=100.5, high=101.5, low=100.0, volume=100.0)
        result = detect_wick_rejection(candles[:-1] + [small_wick], params)
        assert result is None

    def test_detect_wick_rejection_close_not_in_top_30_pct_returns_none(self):
        """Close in bottom of range → no rejection."""
        params = make_params(wick_ratio_min=2.0)
        base = [make_candle(100.0 + i * 0.1, volume=100.0) for i in range(15)]
        # close near bottom: range=20, close near low
        bottom = make_candle(close=91.0, open_=90.0, high=111.0, low=90.0, volume=100.0)
        result = detect_wick_rejection(base + [bottom], params)
        assert result is None


# ---------------------------------------------------------------------------
# compression_breakout
# ---------------------------------------------------------------------------


class TestCompressionBreakout:
    def _compression_setup(self, direction: Direction = Direction.LONG) -> tuple:
        """Build a valid compression then breakout."""
        params = make_params(compression_factor=0.5, volume_ratio_min=1.3)

        # Need 25+ candles with declining BB width and declining volume, then breakout
        # Use base candles with known BB values
        base = []
        bb_widths = [0.04, 0.038, 0.036, 0.034, 0.032]  # strictly declining
        for i in range(25):
            bw = bb_widths[max(0, i - 20)] if i >= 20 else 0.05
            bb_u = 105.0 + i * 0.01 + 2.0
            bb_l = 105.0 + i * 0.01 - 2.0
            base.append(
                make_candle(
                    close=105.0 + i * 0.01,
                    volume=100.0 - i * 0.5,  # declining volume
                    ts=i * 300_000,
                    bb_upper=bb_u,
                    bb_lower=bb_l,
                    bb_width=bw,
                )
            )

        # Replace last 4 pre-breakout candles: declining volume
        for j, vol in enumerate([80.0, 70.0, 60.0], start=22):
            c = base[j]
            base[j] = make_candle(
                close=c.close, volume=vol, ts=c.ts, bb_upper=c.bb_upper, bb_lower=c.bb_lower, bb_width=c.bb_width
            )

        # Breakout candle: close above BB upper with high volume
        last_c = base[-1]
        if direction is Direction.LONG:
            breakout = make_candle(
                close=last_c.bb_upper + 0.5,
                volume=200.0,
                ts=25 * 300_000,
                bb_upper=last_c.bb_upper,
                bb_lower=last_c.bb_lower,
                bb_width=0.030,
            )
        else:
            breakout = make_candle(
                close=last_c.bb_lower - 0.5,
                volume=200.0,
                ts=25 * 300_000,
                bb_upper=last_c.bb_upper,
                bb_lower=last_c.bb_lower,
                bb_width=0.030,
            )

        return base + [breakout], params

    def test_detect_compression_breakout_insufficient_candles_returns_none(self):
        params = make_params()
        assert detect_compression_breakout([make_candle(100.0)] * 10, params) is None

    def test_detect_compression_breakout_inside_bb_returns_none(self):
        """Price stays inside BB — no breakout."""
        params = make_params()
        candles = []
        for i in range(26):
            candles.append(make_candle(100.0, bb_upper=110.0, bb_lower=90.0, bb_width=0.04, volume=100.0))
        result = detect_compression_breakout(candles, params)
        assert result is None


# ---------------------------------------------------------------------------
# momentum_continuation
# ---------------------------------------------------------------------------


class TestMomentumContinuation:
    def _momentum_setup(self) -> tuple:
        """Build a valid 3-candle acceleration + 1 retracement."""
        params = make_params(momentum_acceleration_candles=3)

        # 3 setup candles with growing bodies and volumes
        setup = [
            make_candle(close=102.0, open_=100.0, high=103.0, low=99.5, volume=100.0, ts=0),  # body=2
            make_candle(close=105.0, open_=102.0, high=106.0, low=101.5, volume=120.0, ts=1),  # body=3
            make_candle(close=109.0, open_=105.0, high=110.0, low=104.5, volume=150.0, ts=2),  # body=4
        ]
        # Retracement: small bearish, lower volume
        retrace = make_candle(close=108.0, open_=109.0, high=109.5, low=107.8, volume=80.0, ts=3)

        # Need n+2 total, so add a leading candle
        lead = make_candle(100.0, volume=90.0, ts=-1)
        return [lead] + setup + [retrace], params

    def test_detect_momentum_continuation_valid_setup_fires(self):
        candles, params = self._momentum_setup()
        result = detect_momentum_continuation(candles, params)
        assert result is not None
        assert result.direction is Direction.LONG
        assert result.pattern is PatternType.MOMENTUM_CONTINUATION

    def test_detect_momentum_continuation_insufficient_candles_returns_none(self):
        params = make_params(momentum_acceleration_candles=3)
        assert detect_momentum_continuation([make_candle(100.0)] * 3, params) is None

    def test_detect_momentum_continuation_non_decreasing_bodies_returns_none(self):
        """Bodies not accelerating → no pattern."""
        params = make_params(momentum_acceleration_candles=3)
        lead = make_candle(100.0, volume=90.0, ts=-1)
        # Setup: decreasing bodies
        setup = [
            make_candle(close=109.0, open_=105.0, high=110.0, low=104.5, volume=150.0, ts=0),  # body=4
            make_candle(close=112.0, open_=109.0, high=113.0, low=108.5, volume=160.0, ts=1),  # body=3
            make_candle(close=114.0, open_=112.0, high=115.0, low=111.5, volume=170.0, ts=2),  # body=2
        ]
        retrace = make_candle(close=113.0, open_=114.0, high=114.5, low=112.8, volume=80.0, ts=3)
        result = detect_momentum_continuation([lead] + setup + [retrace], params)
        assert result is None

    def test_detect_momentum_continuation_retrace_same_direction_returns_none(self):
        """Retracement candle same direction as setup → no pattern."""
        candles, params = self._momentum_setup()
        # Replace retrace with continuation (same direction)
        cont = make_candle(close=112.0, open_=108.0, high=113.0, low=107.5, volume=60.0, ts=3)
        candles[-1] = cont
        result = detect_momentum_continuation(candles, params)
        assert result is None


# ---------------------------------------------------------------------------
# anomaly_fade
# ---------------------------------------------------------------------------


class TestAnomalyFade:
    def _anomaly_setup(self) -> tuple:
        """Create a valid spike + reversal for anomaly fade."""
        params = make_params(anomaly_volume_stddev=2.5, anomaly_price_atr=2.5)

        # 22 base candles with consistent volume
        base = [make_candle(100.0 + i * 0.01, volume=100.0, ts=i * 300_000) for i in range(20)]

        # Spike candle: huge volume + large move
        spike = make_candle(close=130.0, open_=100.0, high=132.0, low=99.0, volume=3000.0, ts=20 * 300_000)

        # Reversal: closes below spike close (fading the up-spike)
        reversal = make_candle(close=120.0, open_=130.0, high=131.0, low=119.0, volume=200.0, ts=21 * 300_000)

        return base + [spike, reversal], params

    def test_detect_anomaly_fade_valid_spike_and_reversal_fires(self):
        candles, params = self._anomaly_setup()
        result = detect_anomaly_fade(candles, params)
        assert result is not None
        assert result.direction is Direction.SHORT  # fading an up spike → short
        assert result.pattern is PatternType.ANOMALY_FADE

    def test_detect_anomaly_fade_insufficient_candles_returns_none(self):
        params = make_params()
        assert detect_anomaly_fade([make_candle(100.0)] * 10, params) is None

    def test_detect_anomaly_fade_low_spike_volume_returns_none(self):
        """Volume below threshold → no anomaly.

        With varying background volumes (stddev ≈ 14), the threshold is
        vol_ma + 2.5 * stddev ≈ 100 + 35 = 135. A spike of 110 is below it.
        """
        params = make_params(anomaly_volume_stddev=2.5, anomaly_price_atr=2.5)
        # Varying background so stddev is non-trivial
        vols = [
            80.0,
            120.0,
            90.0,
            110.0,
            85.0,
            115.0,
            95.0,
            105.0,
            80.0,
            120.0,
            90.0,
            110.0,
            85.0,
            115.0,
            95.0,
            105.0,
            80.0,
            120.0,
            90.0,
            110.0,
        ]
        base = [make_candle(100.0, volume=vols[i], ts=i * 300_000) for i in range(20)]
        # spike volume is only slightly above mean → won't exceed mean + 2.5*stddev
        small_spike = make_candle(close=130.0, open_=100.0, high=131.0, low=99.0, volume=110.0, ts=20 * 300_000)
        reversal = make_candle(close=120.0, open_=130.0, high=130.5, low=119.5, volume=200.0, ts=21 * 300_000)
        result = detect_anomaly_fade(base + [small_spike, reversal], params)
        assert result is None

    def test_detect_anomaly_fade_no_reversal_confirmation_returns_none(self):
        """Reversal candle continues in spike direction → no confirmation."""
        candles, params = self._anomaly_setup()
        # Replace reversal with continuation (closes above spike close)
        continuation = make_candle(close=135.0, open_=130.0, high=136.0, low=129.0, volume=200.0)
        candles[-1] = continuation
        result = detect_anomaly_fade(candles, params)
        assert result is None

    def test_detect_anomaly_fade_confidence_in_valid_range(self):
        candles, params = self._anomaly_setup()
        result = detect_anomaly_fade(candles, params)
        assert result is not None
        assert 0.0 < result.confidence <= 1.0
