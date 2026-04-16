#!/usr/bin/env bash
# restart.sh — graceful stop then start.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Stopping..."
bash "$SCRIPT_DIR/stop.sh"
echo "Waiting 3s..."
sleep 3
echo "Starting..."
bash "$SCRIPT_DIR/start.sh"
