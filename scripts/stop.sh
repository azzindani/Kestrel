#!/usr/bin/env bash
# stop.sh — graceful shutdown.
# Sends SIGTERM → daemon cancels orders → closes positions → exits 0.
# Never hard-kills. Never exits with open positions.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

PID_FILE="$ROOT/kestrel.pid"

if [[ ! -f "$PID_FILE" ]]; then
    echo "Not running (no PID file)."
    exit 0
fi

PID=$(cat "$PID_FILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Process $PID not found (already stopped)."
    rm -f "$PID_FILE"
    exit 0
fi

echo "Sending SIGTERM to watchdog (pid=$PID)..."
kill -TERM "$PID"

# Wait up to 60 seconds for graceful shutdown
TIMEOUT=60
for i in $(seq 1 $TIMEOUT); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Daemon stopped cleanly."
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

echo "WARNING: Daemon did not stop within ${TIMEOUT}s. Check for open positions manually."
exit 1
