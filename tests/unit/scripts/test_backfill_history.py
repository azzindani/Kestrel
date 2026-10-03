"""Tests for scripts/backfill_history.closed_only — the still-forming candle must not be backfilled.

Loaded by file path (the script is not a package module).
"""

from __future__ import annotations

import importlib.util
import pathlib

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "backfill_history.py"
_spec = importlib.util.spec_from_file_location("backfill_history", _SCRIPT)
bh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bh)

_H4 = 14_400_000


def _rows(*opens: int) -> list[list]:
    return [[ts, 1.0, 2.0, 0.5, 1.5, 10.0] for ts in opens]


def test_forming_candle_is_dropped():
    now = 5 * _H4 + 1_000  # one second into the candle that opened at 5 * 4h
    kept = bh.closed_only(_rows(3 * _H4, 4 * _H4, 5 * _H4), _H4, now)
    assert [r[0] for r in kept] == [3 * _H4, 4 * _H4]


def test_candle_is_kept_once_its_period_has_ended():
    now = 6 * _H4  # exactly the close of the candle that opened at 5 * 4h
    kept = bh.closed_only(_rows(4 * _H4, 5 * _H4), _H4, now)
    assert [r[0] for r in kept] == [4 * _H4, 5 * _H4]


def test_empty_input():
    assert bh.closed_only([], _H4, 10 * _H4) == []
