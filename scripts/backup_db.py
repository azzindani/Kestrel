#!/usr/bin/env python3
"""
Snapshot the Kestrel research DB to a rotated, compressed pg_dump on the host.

Why this exists (owner 2026-06-25 "we need the data badly → store the database"):
the live DB already persists in the `pgdata` named volume across restarts, but there
was NO backup — one disk failure / accidental `docker compose down -v` / a stray full
reset and the live-recorded research data is gone. The forward-test + trade_context
windows + microstructure tape are the dataset future data-analytic SELECTION will run
on, so they must be durable AND portable, not just live.

Two dump kinds (disk is tight — keep this lean):
  - LEAN (default): everything EXCEPT `candles` → ~29 MB compressed. The irreplaceable
    live tables (trades, trade_context, signals, events, microstructure, pattern_memory).
    Cheap enough to take every loop firing and keep many rotations.
  - FULL (--full): includes `candles` → ~290 MB compressed. Complete + restorable.
    candles are the 800 MB bulk and are RE-FETCHABLE via backfill_history.py from gate,
    so full snapshots are taken rarely (portable copy), lean ones often (safety net).

Restore (into a scratch DB for analysis):
    docker compose exec -T postgres pg_restore -U kestrel -d <scratch> --no-owner < FILE
  A LEAN dump's trade_context FKs candles(id); restore candles first (full dump or a
  backfill) if you need the candle joins. For pure trade/microstructure analytics the
  lean dump stands alone.

Disk guard: refuses to dump if free space < --min-free-gb (default 2) so a 94%-full
host never gets pushed over by a backup. Rotation keeps --keep newest of each kind.

Run (from the repo root on the host — needs docker):
    python3 scripts/backup_db.py                 # lean dump, rotate, keep 14
    python3 scripts/backup_db.py --full --keep 3  # full snapshot, keep 3
    python3 scripts/backup_db.py --dry-run        # show what it would do
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone


def _to_prune(existing: list[pathlib.Path], keep: int) -> list[pathlib.Path]:
    """Return the files to delete: all but the `keep` newest (by name = timestamp).

    Filenames embed a sortable UTC stamp, so lexical sort == chronological. Pure
    (no fs calls) so it is unit-testable; the caller does the unlinking.
    """
    if keep <= 0:
        return []
    ordered = sorted(existing, key=lambda p: p.name)
    return ordered[:-keep] if len(existing) > keep else []


def _dump_cmd(with_candles: bool) -> list[str]:
    cmd = ["docker", "compose", "exec", "-T", "postgres", "pg_dump", "-U", "kestrel", "-d", "kestrel", "-Fc"]
    if not with_candles:
        cmd += ["--exclude-table=candles"]  # data + schema; ~10x smaller, re-fetchable
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotated compressed pg_dump backup of the Kestrel DB.")
    ap.add_argument(
        "--full", action="store_true", help="include candles (complete snapshot, ~290 MB); default excludes them"
    )
    ap.add_argument(
        "--keep", type=int, default=14, help="rotations to keep for this kind (default 14 lean; pass e.g. 3 for full)"
    )
    ap.add_argument("--out", type=str, default="backups", help="host directory for dumps (default ./backups)")
    ap.add_argument("--min-free-gb", type=float, default=2.0, help="abort if host free disk below this (default 2 GB)")
    ap.add_argument("--dry-run", action="store_true", help="show plan, take no dump and delete nothing")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    free_gb = shutil.disk_usage(out).free / 1024**3
    if free_gb < args.min_free_gb:
        print(
            f"[backup] ABORT: only {free_gb:.1f} GB free (< {args.min_free_gb} GB floor). Free disk first.",
            file=sys.stderr,
        )
        return 1

    kind = "full" if args.full else "lean"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = out / f"kestrel-{kind}-{stamp}.dump"

    existing = sorted(out.glob(f"kestrel-{kind}-*.dump"))
    prune = _to_prune(existing + [target], args.keep)  # account for the one we're about to add

    if args.dry_run:
        print(f"[backup] dry-run: would write {target} ({kind}), free={free_gb:.1f} GB")
        for p in prune:
            print(f"[backup] dry-run: would prune {p.name}")
        return 0

    cmd = _dump_cmd(args.full)
    with open(target, "wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        target.unlink(missing_ok=True)  # don't leave a truncated dump in rotation
        print(f"[backup] pg_dump failed ({proc.returncode}): {proc.stderr.decode()[:400]}", file=sys.stderr)
        return 1

    size_mb = target.stat().st_size / 1024**2
    print(f"[backup] wrote {target} — {size_mb:.0f} MB ({kind})")
    for p in prune:
        p.unlink(missing_ok=True)
        print(f"[backup] pruned {p.name}")
    kept = sorted(out.glob(f"kestrel-{kind}-*.dump"))
    print(f"[backup] {kind} rotations kept: {len(kept)} (keep={args.keep})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
