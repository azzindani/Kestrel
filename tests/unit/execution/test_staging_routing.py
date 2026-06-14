"""Phase-2 STAGING execution routing (src/execution/providers + src/config Env).

Staging routes to the LIVE execution code path (it is not DEV) so it exercises
real order placement, but it must run on a demo/testnet venue (TESTNET=true →
ccxt set_sandbox_mode → BingX VST). The registry enforces that rail so staging
can never silently touch real capital.
"""

from __future__ import annotations

import pytest

from src.config import Env
from src.execution.providers import get_execution_provider
from tests.helpers.factories import make_app_config


class TestStagingEnv:
    def test_staging_is_a_distinct_env_value(self):
        assert Env("staging") is Env.STAGING
        assert Env.STAGING.value == "staging"
        # Three phases, three distinct values.
        assert len({Env.DEV, Env.STAGING, Env.PROD}) == 3


class TestStagingRouting:
    def test_staging_without_testnet_is_refused(self):
        """Safety rail: staging on a live venue (TESTNET=false) must not start."""
        cfg = make_app_config(env=Env.STAGING, testnet=False, exchange="bingx")
        with pytest.raises(ValueError, match="TESTNET=true"):
            get_execution_provider(cfg)

    def test_staging_demo_routes_to_live_execution(self):
        """Staging on a demo venue resolves the live provider (not simulation)."""
        pytest.importorskip("ccxt")
        from src.execution.live import LiveExecution

        cfg = make_app_config(env=Env.STAGING, testnet=True, exchange="bingx")
        provider = get_execution_provider(cfg)
        assert isinstance(provider, LiveExecution)

    def test_dev_still_routes_to_simulation(self):
        """Regression: adding STAGING must not change Phase-1 (dev) routing."""
        from src.execution.simulation import SimulationExecution

        cfg = make_app_config(env=Env.DEV)
        assert isinstance(get_execution_provider(cfg), SimulationExecution)
