#!/usr/bin/env bash
# update.sh — git pull → install.sh → GO: restart · NO-GO: abort.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

echo "Pulling latest code..."
git pull origin "$(git rev-parse --abbrev-ref HEAD)"

echo "Running install checks..."
if bash "$SCRIPT_DIR/install.sh"; then
    echo "Install passed. Restarting daemon..."
    bash "$SCRIPT_DIR/restart.sh"
else
    echo "Install check FAILED — staying on current version. No restart."
    exit 1
fi
