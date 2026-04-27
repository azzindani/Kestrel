#!/usr/bin/env bash
# logs.sh — tail events from the events table with optional filters.
# Usage:
#   ./scripts/logs.sh                       # follow all events
#   ./scripts/logs.sh --level CRITICAL      # filter by level
#   ./scripts/logs.sh --category signal     # filter by category
#   ./scripts/logs.sh --last 7d             # last 7 days
#   ./scripts/logs.sh --export --last 7d    # export as JSONL to stdout

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"
source "$ROOT/venv/bin/activate" 2>/dev/null || true

LEVEL=""
CATEGORY=""
LAST="1h"
FOLLOW=true
EXPORT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --level)    LEVEL="$2";    shift 2 ;;
        --category) CATEGORY="$2"; shift 2 ;;
        --last)     LAST="$2";     shift 2 ;;
        --export)   EXPORT=true; FOLLOW=false; shift ;;
        --no-follow) FOLLOW=false; shift ;;
        --follow)   FOLLOW=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

python -c "
import asyncio, os, json, time, sys
from dotenv import load_dotenv
load_dotenv()
import asyncpg

LEVEL = '$LEVEL'
CATEGORY = '$CATEGORY'
LAST = '$LAST'
FOLLOW = $( [[ $FOLLOW == true ]] && echo True || echo False )
EXPORT = $( [[ $EXPORT == true ]] && echo True || echo False )

def parse_duration(s):
    if s.endswith('d'):  return int(s[:-1]) * 86400
    if s.endswith('h'):  return int(s[:-1]) * 3600
    if s.endswith('m'):  return int(s[:-1]) * 60
    return 3600

async def run():
    dsn = f\"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}\"
    try:
        conn = await asyncpg.connect(dsn=dsn)
    except Exception as exc:
        print(f'[WARN]  DB unreachable — cannot stream events ({exc.__class__.__name__}: {exc})')
        return

    since_ts = int((time.time() - parse_duration(LAST)) * 1000)
    last_id = 0

    while True:
        where = ['ts >= \$1', 'id > \$2']
        params = [since_ts, last_id]
        i = 3
        if LEVEL:
            where.append(f'level = \${i}'); params.append(LEVEL); i += 1
        if CATEGORY:
            where.append(f'category = \${i}'); params.append(CATEGORY); i += 1

        rows = await conn.fetch(
            f\"SELECT id, ts, level, category, message, payload FROM events WHERE {' AND '.join(where)} ORDER BY ts ASC LIMIT 100\",
            *params
        )
        for row in rows:
            last_id = row['id']
            ts_str = time.strftime('%H:%M:%S', time.gmtime(row['ts'] / 1000))
            if EXPORT:
                print(json.dumps(dict(row)))
            else:
                payload_str = ''
                if row['payload']:
                    p = json.loads(row['payload'])
                    payload_str = ' ' + str(p)[:120]
                level_colors = {'INFO': '', 'WARN': '\033[1;33m', 'ERROR': '\033[0;31m', 'CRITICAL': '\033[0;31;1m'}
                color = level_colors.get(row['level'], '')
                reset = '\033[0m' if color else ''
                print(f\"{ts_str}  {color}[{row['level'][:4]}]{reset}  [{row['category'][:3].upper()}]  {row['message']}{payload_str}\")

        if not FOLLOW:
            break
        await asyncio.sleep(2)

asyncio.run(run())
"
