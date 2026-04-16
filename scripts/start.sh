#!/usr/bin/env bash
# start.sh — start daemon + watchdog.
# Requires install.sh to have printed [GO] first.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

[[ -f "$ROOT/.env" ]] || { echo "ERROR: .env not found"; exit 1; }
source "$ROOT/.env"
source "$ROOT/venv/bin/activate"

# Check not already running
PID_FILE="$ROOT/kestrel.pid"
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Daemon already running (pid=$OLD_PID). Use restart.sh to restart."
        exit 1
    fi
    rm -f "$PID_FILE"
fi

DB_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
LOG_FILE="$ROOT/kestrel.log"

echo "Starting Kestrel daemon (BOT_ID=$BOT_ID, ENV=$ENV)..."

# Launch watchdog (which supervises the main daemon)
nohup python -m src.engine.watchdog \
    --db-url "$DB_URL" \
    --bot-id "$BOT_ID" \
    python -m src.engine.daemon \
    >> "$LOG_FILE" 2>&1 &

WATCHDOG_PID=$!
echo "$WATCHDOG_PID" > "$PID_FILE"
echo "Watchdog started (pid=$WATCHDOG_PID). Daemon is supervised."
echo "Logs: $LOG_FILE"
echo "Status: ./scripts/status.sh"
