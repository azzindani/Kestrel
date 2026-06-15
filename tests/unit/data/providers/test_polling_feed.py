"""Unit tests for the REST-polling feed's close-detection (src/data/providers/polling.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.data.providers.polling import PollingFeed, _tf_ms, new_closed_rows
from tests.helpers.factories import make_app_config

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


class TestMultiBotDispatch:
    """Regression: one poller per (pair, tf) must feed EVERY bot on that stream.

    The original bug fed only the first-subscribed bot, silently starving the
    other strategy on the same pair (it never saw a candle close → never traded).
    """

    def test_dispatch_feeds_all_bots_on_a_pair(self):
        cfg = make_app_config(exchange="gate")
        feed = PollingFeed(cfg)
        b1, b2, other = MagicMock(), MagicMock(), MagicMock()
        feed.subscribe("ETH/USDT", "4h", b1)
        feed.subscribe("ETH/USDT", "4h", b2)  # second strategy, SAME pair
        feed.subscribe("BTC/USDT", "4h", other)

        feed._dispatch_closed("ETH/USDT", "4h", [1_700_000_000_000, 100.0, 110.0, 95.0, 105.0, 50.0])

        b1.process_ohlcv.assert_called_once()
        b2.process_ohlcv.assert_called_once()  # would FAIL under the old per-sub bug
        other.process_ohlcv.assert_not_called()

    def test_dispatch_converts_volume_base_to_quote(self):
        cfg = make_app_config(exchange="gate")
        feed = PollingFeed(cfg)
        b = MagicMock()
        feed.subscribe("ETH/USDT", "4h", b)
        feed._dispatch_closed("ETH/USDT", "4h", [1_700_000_000_000, 100.0, 110.0, 95.0, 105.0, 50.0])
        sent = b.process_ohlcv.call_args.args[0]
        assert sent[0] == 1_700_000_000_000
        assert sent[5] == 50.0 * 105.0  # volume × close → quote scale
