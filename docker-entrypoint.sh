#!/usr/bin/env bash
# docker-entrypoint.sh — container startup: wait for postgres, run daemon in foreground.
# Docker restart policy (unless-stopped) handles supervision instead of watchdog.sh.
set -euo pipefail

echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."
until pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -t 60 2>/dev/null; do
    echo "  not ready — retrying in 2s"
    sleep 2
done
echo "PostgreSQL ready."

exec python -m src.engine.daemon
