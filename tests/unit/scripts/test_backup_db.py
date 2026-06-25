"""Pure-helper tests for scripts/backup_db.py (_to_prune / _dump_cmd — no fs, no docker).

Loaded by file path so it needs no package wiring. The pg_dump execution + disk guard
are I/O and covered by the in-repo dry-run, not here. What matters to lock down is the
rotation math (never prune the wrong files) and that the lean dump really excludes candles.
"""

from __future__ import annotations

import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).parents[3] / "scripts" / "backup_db.py"
_spec = importlib.util.spec_from_file_location("backup_db", _PATH)
backup_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup_db)


def _files(*names):
    return [pathlib.Path("backups") / n for n in names]


class TestToPrune:
    def test_empty_prunes_nothing(self):
        assert backup_db._to_prune([], 14) == []

    def test_under_keep_prunes_nothing(self):
        files = _files("kestrel-lean-20260101T000000Z.dump", "kestrel-lean-20260102T000000Z.dump")
        assert backup_db._to_prune(files, 14) == []

    def test_at_keep_prunes_nothing(self):
        files = _files("a", "b", "c")
        assert backup_db._to_prune(files, 3) == []

    def test_over_keep_drops_oldest_by_name(self):
        # timestamped names sort chronologically; oldest two go when keep=2 of 4.
        files = _files(
            "kestrel-lean-20260101T000000Z.dump",
            "kestrel-lean-20260102T000000Z.dump",
            "kestrel-lean-20260103T000000Z.dump",
            "kestrel-lean-20260104T000000Z.dump",
        )
        pruned = backup_db._to_prune(files, 2)
        assert [p.name for p in pruned] == [
            "kestrel-lean-20260101T000000Z.dump",
            "kestrel-lean-20260102T000000Z.dump",
        ]

    def test_keep_zero_or_negative_prunes_nothing(self):
        # a 0/neg keep is a misconfig; refuse to wipe everything rather than obey it.
        files = _files("a", "b")
        assert backup_db._to_prune(files, 0) == []
        assert backup_db._to_prune(files, -1) == []

    def test_unsorted_input_still_drops_chronologically_oldest(self):
        files = _files(
            "kestrel-lean-20260103T000000Z.dump",
            "kestrel-lean-20260101T000000Z.dump",
            "kestrel-lean-20260102T000000Z.dump",
        )
        pruned = backup_db._to_prune(files, 1)
        assert [p.name for p in pruned] == [
            "kestrel-lean-20260101T000000Z.dump",
            "kestrel-lean-20260102T000000Z.dump",
        ]


class TestDumpCmd:
    def test_lean_excludes_candles(self):
        assert "--exclude-table=candles" in backup_db._dump_cmd(with_candles=False)

    def test_full_includes_candles(self):
        assert "--exclude-table=candles" not in backup_db._dump_cmd(with_candles=True)

    def test_cmd_targets_kestrel_custom_format(self):
        cmd = backup_db._dump_cmd(with_candles=True)
        assert "pg_dump" in cmd and "-Fc" in cmd and "kestrel" in cmd
