#!/usr/bin/env bash
# status.sh — health check: process alive + heartbeat fresh + DB reachable.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"
source "$ROOT/venv/bin/activate" 2>/dev/null || true

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }

echo "=== Kestrel Status ==="

# Process
PID_FILE="$ROOT/kestrel.pid"
if [[ -f "$PID_FILE" ]]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        ok "Watchdog running (pid=$PID)"
    else
        fail "PID file exists but process $PID not running"
    fi
else
    warn "Not running (no PID file)"
fi

# Heartbeat freshness
python -c "
import asyncio, os, time
from dotenv import load_dotenv
load_dotenv()
import asyncpg

async def check():
    cfg_vars = {k: os.environ[k] for k in ('DB_HOST','DB_PORT','DB_NAME','DB_USER','DB_PASSWORD','BOT_ID')}
    dsn = f\"postgresql://{cfg_vars['DB_USER']}:{cfg_vars['DB_PASSWORD']}@{cfg_vars['DB_HOST']}:{cfg_vars['DB_PORT']}/{cfg_vars['DB_NAME']}\"
    conn = await asyncpg.connect(dsn=dsn, command_timeout=5)
    row = await conn.fetchrow('SELECT ts, status FROM heartbeats WHERE bot_id = \$1', cfg_vars['BOT_ID'])
    await conn.close()
    if row is None:
        print('  \033[1;33m⚠\033[0m  No heartbeat recorded')
        return
    age = time.time() - row['ts'] / 1000
    if age < 90:
        print(f'  \033[0;32m✓\033[0m  Heartbeat fresh ({age:.0f}s ago) status={row[\"status\"]}')
    else:
        print(f'  \033[0;31m✗\033[0m  Heartbeat stale ({age:.0f}s ago)')

asyncio.run(check())
" 2>/dev/null || warn "DB unreachable — cannot check heartbeat"

# Open positions
python -c "
import asyncio, os
from dotenv import load_dotenv
load_dotenv()
import asyncpg

async def check():
    dsn = f\"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}\"
    conn = await asyncpg.connect(dsn=dsn, command_timeout=5)
    row = await conn.fetchrow('SELECT COUNT(*) AS cnt FROM trades WHERE bot_id=\$1 AND env=\$2 AND exit_ts IS NULL',
                              os.environ['BOT_ID'], os.environ['ENV'])
    await conn.close()
    cnt = row['cnt']
    print(f'  \033[0;32m✓\033[0m  Open positions: {cnt}')

asyncio.run(check())
" 2>/dev/null || warn "DB unreachable — cannot check positions"

echo ""
