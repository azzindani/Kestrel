#!/bin/sh
# Daily Postgres backup with 7-day rotation.
# Runs in a small alpine sidecar; loops with a sleep instead of cron so we
# don't drag in cron daemons. Configurable via env:
#   PG_HOST, PG_PORT, PG_USER, PG_DB, PGPASSWORD (from compose env_file)
#   BACKUP_DIR      where dumps land (default /backups, mounted to host volume)
#   BACKUP_KEEP     how many daily files to retain (default 7)
#   BACKUP_HOUR     UTC hour to dump (default 3 → 03:00 UTC per CLAUDE.md §15)

set -eu

PG_HOST="${PG_HOST:-postgres}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-kestrel}"
PG_DB="${PG_DB:-kestrel}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP="${BACKUP_KEEP:-7}"
BACKUP_HOUR="${BACKUP_HOUR:-3}"

mkdir -p "$BACKUP_DIR"

run_dump() {
    ts=$(date -u +%Y%m%d-%H%M%S)
    out="$BACKUP_DIR/kestrel-$ts.sql.gz"
    echo "[$(date -u +%FT%TZ)] pg_dump → $out"
    PGPASSWORD="$PGPASSWORD" pg_dump \
        -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
        --no-owner --no-acl \
        | gzip > "$out"
    echo "[$(date -u +%FT%TZ)] done ($(du -h "$out" | cut -f1))"

    # Rotate: keep only the BACKUP_KEEP newest files.
    ls -1t "$BACKUP_DIR"/kestrel-*.sql.gz 2>/dev/null \
        | tail -n +$((BACKUP_KEEP + 1)) \
        | xargs -r rm -v
}

# Run one dump on startup so the first backup isn't 24h away on a fresh deploy.
run_dump || echo "[warn] initial dump failed (postgres not ready?); will retry tomorrow"

while true; do
    # Sleep until next BACKUP_HOUR:00 UTC.
    now_h=$(date -u +%H)
    now_m=$(date -u +%M)
    now_s=$(date -u +%S)
    target=$BACKUP_HOUR
    if [ "$now_h" -lt "$target" ]; then
        wait_s=$(( (target - now_h) * 3600 - now_m * 60 - now_s ))
    else
        wait_s=$(( (24 - now_h + target) * 3600 - now_m * 60 - now_s ))
    fi
    echo "[$(date -u +%FT%TZ)] next dump in ${wait_s}s"
    sleep "$wait_s"
    run_dump || echo "[warn] dump failed; will retry tomorrow"
done
