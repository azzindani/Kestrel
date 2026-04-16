"""Unit tests for src/risk/manager.py (Tier 1 — human-only module, tests cover all 6 rules)."""

from __future__ import annotations

import time

from hypothesis import given, settings
from hypothesis import strategies as st

from src.config import Direction, ValidationResult
from src.risk.manager import validate
from tests.helpers.factories import make_app_config, make_bucket_state, make_signal

# ---------------------------------------------------------------------------
# Contract: public API
# ---------------------------------------------------------------------------


class TestValidateContract:
    """Validate contract: always returns ValidationResult with passed + reason."""

    def test_validate_returns_validation_result(self):
        result = validate(make_signal(), make_bucket_state(), make_app_config())
        assert isinstance(result, ValidationResult)

    def test_validate_passed_signal_returns_passed_true_reason_none(self):
        result = validate(make_signal(), make_bucket_state(), make_app_config())
        assert result.passed is True
        assert result.reason is None


# ---------------------------------------------------------------------------
# Rule 1: bucket_limit
# ---------------------------------------------------------------------------


class TestBucketLimit:
    def test_validate_bucket_limit_at_max_returns_rejected(self):
        state = make_bucket_state(active_positions=1)
        result = validate(make_signal(), state, make_app_config(max_active_buckets=1))
        assert result.passed is False
        assert result.reason == "bucket_limit"

    def test_validate_bucket_limit_below_max_passes(self):
        state = make_bucket_state(active_positions=0)
        result = validate(make_signal(), state, make_app_config(max_active_buckets=1))
        assert result.passed is True

    def test_validate_bucket_limit_multiple_buckets_passes_when_space(self):
        state = make_bucket_state(active_positions=2)
        result = validate(make_signal(), state, make_app_config(max_active_buckets=5))
        assert result.passed is True


# ---------------------------------------------------------------------------
# Rule 2: liquidation_too_close
# ---------------------------------------------------------------------------


class TestLiquidationTooClose:
    def test_validate_liq_too_close_very_high_leverage_returns_rejected(self):
        # With leverage=1000, liquidation is 0.1% away — below 1.5% threshold
        result = validate(make_signal(), make_bucket_state(), make_app_config(leverage=1000))
        assert result.passed is False
        assert result.reason == "liquidation_too_close"

    def test_validate_liq_adequate_distance_passes(self):
        # leverage=20 → liq distance ≈ 5% - 0.5% = 4.5% > 1.5%
        result = validate(make_signal(), make_bucket_state(), make_app_config(leverage=20))
        assert result.passed is True


# ---------------------------------------------------------------------------
# Rule 3: rr_below_minimum
# ---------------------------------------------------------------------------


class TestRrBelowMinimum:
    def test_validate_rr_below_1_2_returns_rejected(self):
        # tp_dist=100, sl_dist=500 → R/R = 0.2 < 1.2
        sig = make_signal(entry=1000.0, tp_offset=100.0, sl_offset=500.0)
        result = validate(sig, make_bucket_state(), make_app_config())
        assert result.passed is False
        assert result.reason == "rr_below_minimum"

    def test_validate_rr_exactly_at_minimum_passes(self):
        # tp_dist=120, sl_dist=100 → R/R = 1.2 (just at boundary)
        sig = make_signal(entry=1000.0, tp_offset=120.0, sl_offset=100.0)
        result = validate(sig, make_bucket_state(), make_app_config())
        assert result.passed is True

    def test_validate_sl_distance_zero_returns_rejected(self):
        # Manually set sl = entry to create zero distance
        from src.config import Signal

        sig_zero_sl = Signal(
            bot_id="t",
            session_id="s",
            env="dev",
            ts=int(time.time() * 1000),
            pair="BTCUSDT",
            timeframe="5m",
            candle_ts=0,
            pattern="impulse_retracement",
            direction=Direction.LONG,
            confidence=0.75,
            regime="TRENDING",
            layer_regime=1,
            layer_trend=1,
            layer_momentum=1,
            layer_volume=1,
            layers_passed=4,
            entry_price=1000.0,
            tp_price=1200.0,
            sl_price=1000.0,
            size_usdt=10.0,
        )
        result = validate(sig_zero_sl, make_bucket_state(), make_app_config())
        assert result.passed is False
        assert result.reason in ("sl_distance_zero", "rr_below_minimum")


# ---------------------------------------------------------------------------
# Rule 4: fee_not_viable
# ---------------------------------------------------------------------------


class TestFeeNotViable:
    def test_validate_tiny_tp_pct_returns_rejected(self):
        # tp_pct = 1/10000*100 = 0.01%, fee*1.5 = 0.27% → fee_not_viable
        sig2 = make_signal(entry=10000.0, tp_offset=1.0, sl_offset=0.5)
        result = validate(sig2, make_bucket_state(), make_app_config())
        assert result.passed is False
        assert result.reason == "fee_not_viable"

    def test_validate_adequate_tp_passes_fee_check(self):
        # tp_pct = 336/83000 * 100 ≈ 0.40% > 0.27% → passes
        sig = make_signal(entry=83000.0, tp_offset=336.0, sl_offset=210.0)
        result = validate(sig, make_bucket_state(), make_app_config())
        assert result.passed is True


# ---------------------------------------------------------------------------
# Rule 5: daily_loss_limit
# ---------------------------------------------------------------------------


class TestDailyLossLimit:
    def test_validate_session_pnl_at_limit_returns_rejected(self):
        state = make_bucket_state(session_net_pnl=-5.01)
        result = validate(make_signal(), state, make_app_config())
        assert result.passed is False
        assert result.reason == "daily_loss_limit"

    def test_validate_session_pnl_exactly_at_limit_returns_rejected(self):
        state = make_bucket_state(session_net_pnl=-5.00)
        result = validate(make_signal(), state, make_app_config())
        assert result.passed is False
        assert result.reason == "daily_loss_limit"

    def test_validate_session_pnl_just_above_limit_passes(self):
        state = make_bucket_state(session_net_pnl=-4.99)
        result = validate(make_signal(), state, make_app_config())
        assert result.passed is True

    def test_validate_session_pnl_positive_passes(self):
        state = make_bucket_state(session_net_pnl=5.0)
        result = validate(make_signal(), state, make_app_config())
        assert result.passed is True


# ---------------------------------------------------------------------------
# Rule 6: stale_data
# ---------------------------------------------------------------------------


class TestStaleData:
    def test_validate_recent_reconnect_30s_ago_returns_stale(self):
        now = int(time.time() * 1000)
        state = make_bucket_state(
            last_ws_reconnect_ts=now - 30_000,
            current_ts=now,
        )
        result = validate(make_signal(), state, make_app_config())
        assert result.passed is False
        assert result.reason == "stale_data"

    def test_validate_reconnect_61s_ago_passes(self):
        now = int(time.time() * 1000)
        state = make_bucket_state(
            last_ws_reconnect_ts=now - 61_000,
            current_ts=now,
        )
        result = validate(make_signal(), state, make_app_config())
        assert result.passed is True

    def test_validate_never_reconnected_passes(self):
        result = validate(make_signal(), make_bucket_state(last_ws_reconnect_ts=None), make_app_config())
        assert result.passed is True

    def test_validate_reconnect_exactly_at_60s_passes(self):
        # elapsed == 60s: condition is elapsed < 60 → False → not stale
        now = int(time.time() * 1000)
        state = make_bucket_state(
            last_ws_reconnect_ts=now - 60_000,
            current_ts=now,
        )
        result = validate(make_signal(), state, make_app_config())
        assert result.passed is True


# ---------------------------------------------------------------------------
# Rule ordering: first failing rule returned
# ---------------------------------------------------------------------------


class TestRuleOrdering:
    def test_validate_bucket_limit_checked_before_other_rules(self):
        """bucket_limit is Rule 1 — should be returned even if other rules also fail."""
        state = make_bucket_state(
            active_positions=1,
            session_net_pnl=-10.0,  # also violates daily loss
        )
        result = validate(make_signal(), state, make_app_config())
        assert result.reason == "bucket_limit"

    @given(
        active=st.integers(min_value=0, max_value=10),
        pnl=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_validate_always_returns_valid_result_struct(self, active: int, pnl: float):
        state = make_bucket_state(active_positions=active, session_net_pnl=pnl)
        result = validate(make_signal(), state, make_app_config(max_active_buckets=5))
        assert isinstance(result.passed, bool)
        if result.passed:
            assert result.reason is None
        else:
            assert isinstance(result.reason, str)
