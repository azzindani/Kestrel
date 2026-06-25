"""Pure-helper tests for scripts/reset_dev.py (_build_steps only — no DB).

The script is loaded by file path so it needs no package wiring on sys.path.
Only the side-effect-free delete-step builder is exercised; the actual DELETE
execution is I/O and covered by the in-container dry-run, not here.

The behaviour under test is the 2026-06-25 scoping rule: a SURGICAL reset
(--strategy) must wipe only the named cohorts' dev rows and must NEVER touch the
global pattern_memory, while a full reset (no scope) keeps the old whole-slate wipe.
"""

from __future__ import annotations

import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).parents[3] / "scripts" / "reset_dev.py"
_spec = importlib.util.spec_from_file_location("reset_dev", _PATH)
reset_dev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reset_dev)


def _tables(steps):
    return [t for t, _, _ in steps]


class TestBuildSteps:
    def test_full_wipe_includes_pattern_memory(self):
        steps = reset_dev._build_steps(None)
        assert _tables(steps) == ["trade_context", "events", "signals", "trades", "pattern_memory"]

    def test_full_wipe_steps_take_no_args(self):
        for _, _, args in reset_dev._build_steps(None):
            assert args == ()

    def test_scoped_wipe_excludes_pattern_memory(self):
        # The whole point of the scoped reset: global learned state survives so the
        # cohorts that did NOT change keep their pattern_memory.
        steps = reset_dev._build_steps(["cci_mom"])
        assert "pattern_memory" not in _tables(steps)
        assert _tables(steps) == ["trade_context", "events", "signals", "trades"]

    def test_scoped_wipe_passes_strategy_list_as_arg(self):
        strategies = ["macd_cross", "macd_rsi"]
        steps = reset_dev._build_steps(strategies)
        for _, sql, args in steps:
            assert args == (strategies,)
            assert "ANY($1::text[])" in sql

    def test_scoped_trade_context_routes_through_parent_trades(self):
        # trade_context has no bot_id/env column — it must scope via its parent trades.
        steps = reset_dev._build_steps(["cci_mom"])
        tctx_sql = next(sql for table, sql, _ in steps if table == "trade_context")
        assert "FROM trades WHERE env='dev'" in tctx_sql

    def test_scoped_and_full_both_keep_candles(self):
        # candles + microstructure are never in any delete step, ever.
        for scope in (None, ["cci_mom"]):
            tables = _tables(reset_dev._build_steps(scope))
            assert "candles" not in tables
            assert "microstructure" not in tables
