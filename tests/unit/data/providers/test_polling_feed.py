"""Unit tests for the REST-polling feed's close-detection (src/data/providers/polling.py)."""

from __future__ import annotations

from src.data.providers.polling import _tf_ms, new_closed_rows

# 4h period in ms; build rows on 4h boundaries.
_P = 14_400_000


def _row(ts, close=100.0, vol=5.0):
    return [ts, close, close + 1, close - 1, close, vol]


class TestTimeframeMs:
    def test_known_timeframes(self):
        assert _tf_ms("5m") == 300_000
        assert _tf_ms("4h") == 14_400_000
        assert _tf_ms("1d") == 86_400_000

    def test_unknown_timeframe_raises(self):
        try:
            _tf_ms("7h")
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestNewClosedRows:
    def test_emits_only_closed_candles(self):
        # rows at 0, 4h, 8h; now is mid the 8h candle → 8h is still open.
        rows = [_row(0), _row(_P), _row(2 * _P)]
        now = 2 * _P + 1_000  # 8h candle (ts=2P) closes at 3P; still open
        out = new_closed_rows(rows, _P, now, last_emitted=None)
        # last_emitted None → seed with the most-recent CLOSED row (ts=4h), not the open 8h
        assert [r[0] for r in out] == [_P]

    def test_seed_returns_only_latest_closed_when_no_bootstrap(self):
        rows = [_row(0), _row(_P), _row(2 * _P)]
        now = 3 * _P  # all three are closed
        out = new_closed_rows(rows, _P, now, last_emitted=None)
        assert [r[0] for r in out] == [2 * _P]  # only the latest, no history replay

    def test_emits_all_new_closed_after_last_emitted(self):
        rows = [_row(0), _row(_P), _row(2 * _P), _row(3 * _P)]
        now = 4 * _P  # 0,1,2,3 all closed (3P closes at 4P)
        out = new_closed_rows(rows, _P, now, last_emitted=_P)
        assert [r[0] for r in out] == [2 * _P, 3 * _P]  # ascending, only > last_emitted

    def test_no_emit_when_nothing_new(self):
        rows = [_row(0), _row(_P), _row(2 * _P)]
        now = 2 * _P + 5  # only 0 and 1 closed; both <= last_emitted
        out = new_closed_rows(rows, _P, now, last_emitted=_P)
        assert out == []

    def test_in_progress_candle_never_emitted(self):
        # latest row is the current (open) candle; must be excluded
        rows = [_row(_P), _row(2 * _P)]
        now = 2 * _P + 60_000  # 2P candle still open (closes at 3P)
        out = new_closed_rows(rows, _P, now, last_emitted=_P)
        assert out == []  # 2P not closed, 1P already emitted

    def test_dedup_exact_boundary(self):
        # a candle is closed exactly when ts + period == now
        rows = [_row(2 * _P)]
        assert new_closed_rows(rows, _P, 3 * _P, last_emitted=_P) == [rows[0]]
        assert new_closed_rows(rows, _P, 3 * _P - 1, last_emitted=_P) == []
