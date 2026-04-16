#!/usr/bin/env bash
# cleanup.sh — retention cleanup + VACUUM ANALYZE.
# Run at 03:00 UTC daily (also called by daemon's cleanup_task).
# Retention policy (CLAUDE.md §19):
#   candles not in trade_context: 90d
#   signals:                      60d
#   events:                       30d
#   trades + trade_context:       indefinite

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"
source "$ROOT/venv/bin/activate" 2>/dev/null || true

python -c "
import asyncio, os, time
from dotenv import load_dotenv
load_dotenv()
import asyncpg

async def run():
    dsn = f\"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}\"
    conn = await asyncpg.connect(dsn=dsn)

    now = time.time()
    ts_90d = int((now - 90 * 86400) * 1000)
    ts_60d = int((now - 60 * 86400) * 1000)
    ts_30d = int((now - 30 * 86400) * 1000)

    r1 = await conn.execute('DELETE FROM candles WHERE ts < \$1 AND id NOT IN (SELECT candle_id FROM trade_context)', ts_90d)
    r2 = await conn.execute('DELETE FROM signals WHERE ts < \$1', ts_60d)
    r3 = await conn.execute('DELETE FROM events WHERE ts < \$1', ts_30d)
    await conn.execute('VACUUM ANALYZE candles')
    await conn.execute('VACUUM ANALYZE signals')
    await conn.execute('VACUUM ANALYZE events')
    await conn.close()

    print(f'Cleanup complete: {r1} {r2} {r3}')

asyncio.run(run())
"
