#!/usr/bin/env python3
"""restore_archive.py — restore a backup dump into the kestrel_archive database.

After a dev reset the live DB no longer holds the wiped trades/signals/events, so the
Data-Analysis Grafana board (uid kestrel-analysis, scripts/build_analysis_dashboard.py)
reads them from a SEPARATE `kestrel_archive` database via the provisioned KestrelArchive
datasource. This script (re)builds that archive from a chosen backup so you can analyse
ANY snapshot's full history — trades, signals, events, microstructure — in Grafana.

Restores the analysis-relevant tables only (skips candles + the bulky trade_context):
    trades · signals · events · pattern_memory · microstructure
then recreates the microstructure (pair, ts) index + ANALYZEs so the order-flow ⋈ trades
join panel stays fast.

Run on the host (orchestrates the postgres container, like backup_db.py):
    python3 scripts/restore_archive.py                 # newest lean dump
    python3 scripts/restore_archive.py backups/<file>.dump
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_ARCHIVE_DB = "kestrel_archive"
_TABLES = ("trades", "signals", "events", "pattern_memory", "microstructure")


def _dc(*args: str) -> list[str]:
    return ["docker", "compose", *args]


def _psql(db: str, sql: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        _dc("exec", "-T", "postgres", "psql", "-U", "kestrel", "-d", db, "-c", sql),
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Restore a backup dump into kestrel_archive for analysis.")
    ap.add_argument("dump", nargs="?", help="path to a .dump (default: newest backups/kestrel-lean-*.dump)")
    args = ap.parse_args()

    dump = args.dump
    if not dump:
        candidates = sorted(glob.glob(os.path.join(_ROOT, "backups", "kestrel-lean-*.dump")), reverse=True)
        if not candidates:
            print("no backups/kestrel-lean-*.dump found; pass a dump path explicitly", file=sys.stderr)
            return 1
        dump = candidates[0]
    if not os.path.isfile(dump):
        print(f"dump not found: {dump}", file=sys.stderr)
        return 1
    print(f"[archive] restoring from {os.path.relpath(dump, _ROOT)}")

    # 1. copy the dump into the postgres container
    subprocess.run(_dc("cp", dump, "postgres:/tmp/archive.dump"), cwd=_ROOT, check=True, capture_output=True, text=True)

    # 2. recreate the archive database
    _psql("postgres", f"DROP DATABASE IF EXISTS {_ARCHIVE_DB};")
    r = _psql("postgres", f"CREATE DATABASE {_ARCHIVE_DB} OWNER kestrel;")
    if r.returncode != 0:
        print(f"[archive] create DB failed: {r.stderr.strip()[:300]}", file=sys.stderr)
        return 1

    # 3. restore the analysis tables (pg_restore exits non-zero on the candles FK it can't
    #    rebuild — that is expected for a lean dump and harmless, so we don't gate on it)
    sel = []
    for t in _TABLES:
        sel += ["-t", t]
    subprocess.run(
        _dc(
            "exec",
            "-T",
            "postgres",
            "pg_restore",
            "-U",
            "kestrel",
            "-d",
            _ARCHIVE_DB,
            "--no-owner",
            "--no-acl",
            *sel,
            "/tmp/archive.dump",
        ),
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )

    # 4. selective restore drops indexes — recreate the ones the dashboard joins need
    _psql(_ARCHIVE_DB, "CREATE INDEX IF NOT EXISTS idx_micro_pair_ts ON microstructure (pair, ts DESC);")
    _psql(_ARCHIVE_DB, "CREATE INDEX IF NOT EXISTS idx_trades_archive ON trades (env, exit_ts);")
    _psql(_ARCHIVE_DB, "ANALYZE;")
    subprocess.run(
        _dc("exec", "-T", "postgres", "rm", "-f", "/tmp/archive.dump"), cwd=_ROOT, capture_output=True, text=True
    )

    counts = _psql(
        _ARCHIVE_DB,
        "SELECT 'trades '||count(*) FROM trades "
        "UNION ALL SELECT 'events '||count(*) FROM events "
        "UNION ALL SELECT 'microstructure '||count(*) FROM microstructure;",
    )
    print(
        "[archive] restored:\n"
        + "\n".join(
            "  " + ln.strip() for ln in counts.stdout.splitlines() if ln.strip() and "row" not in ln and "-" not in ln
        )
    )
    print(f"[archive] ready — Grafana datasource 'KestrelArchive' (uid kestrel-archive) → DB {_ARCHIVE_DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
