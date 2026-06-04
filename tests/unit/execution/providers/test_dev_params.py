"""The DEV execution provider must hand each bot its OWN params.

Exit mechanics (max_hold_candles timeout, trailing-close) live in
SimulationExecution, so per-bot param overrides have to reach it — otherwise
trailing lab variants would silently run with trailing off.
"""

from __future__ import annotations

from src.execution.providers import get_execution_provider
from src.execution.simulation import SimulationExecution
from tests.helpers.factories import make_app_config, make_params


class TestDevProviderParams:
    def test_per_bot_trailing_params_reach_simulation(self):
        params = make_params(trailing_enabled=True, trail_activation_r=1.0, trail_distance_r=0.5, max_hold_candles=24)
        cfg = make_app_config(params=params)
        ex = get_execution_provider(cfg)
        assert isinstance(ex, SimulationExecution)
        assert ex._trailing_enabled is True
        assert ex._trail_distance_r == 0.5
        assert ex._max_hold_candles == 24

    def test_falls_back_to_params_json_when_no_override(self):
        # No per-bot params → loads params.json; trailing defaults off there.
        cfg = make_app_config(params=None)
        ex = get_execution_provider(cfg)
        assert isinstance(ex, SimulationExecution)
        assert ex._trailing_enabled is False
