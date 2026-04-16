#!/usr/bin/env bash
# tune.sh — param change → 30d backtest → compare → ACCEPT | REVERT.
# Usage: ./scripts/tune.sh --param volume_ratio_min --value 1.5

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"
source "$ROOT/venv/bin/activate"

PARAM=""
NEW_VALUE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --param) PARAM="$2"; shift 2 ;;
        --value) NEW_VALUE="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

[[ -n "$PARAM" && -n "$NEW_VALUE" ]] || {
    echo "Usage: $0 --param <name> --value <value>"
    exit 1
}

python -c "
import json, sys, os, asyncio

ROOT = '$ROOT'
PARAM = '$PARAM'
NEW_VALUE = '$NEW_VALUE'

# Load params.json
with open(f'{ROOT}/params.json') as f:
    params_raw = json.load(f)

if PARAM not in params_raw:
    print(f'ERROR: Unknown param: {PARAM}')
    sys.exit(1)

spec = params_raw[PARAM]
ptype = spec['type']
lo, hi = spec['range']

# Type-cast
if ptype == 'int':
    val = int(NEW_VALUE)
elif ptype == 'float':
    val = float(NEW_VALUE)
else:
    val = NEW_VALUE

# Range check
if not (lo <= val <= hi):
    print(f'ERROR: {PARAM}={val} out of range [{lo}, {hi}]')
    sys.exit(1)

# Save backup
import shutil
backup = f'{ROOT}/params.json.bak'
shutil.copy(f'{ROOT}/params.json', backup)
print(f'Backup saved to {backup}')

old_val = spec['value']
params_raw[PARAM]['value'] = val
with open(f'{ROOT}/params.json', 'w') as f:
    json.dump(params_raw, f, indent=2)
print(f'Updated {PARAM}: {old_val} → {val}')

# Run 30-day backtest
print('Running 30-day backtest...')
from dotenv import load_dotenv
load_dotenv()
from src.config import AppConfig, load_params
import asyncpg

cfg = AppConfig.from_mapping(os.environ)
params = load_params(f'{ROOT}/params.json')

async def run_backtest():
    dsn = f\"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}\"
    conn = await asyncpg.connect(dsn=dsn)
    since_ts = int((__import__('time').time() - 30 * 86400) * 1000)
    rows = await conn.fetch(
        'SELECT * FROM candles WHERE bot_id=\$1 AND pair=\$2 AND timeframe=\$3 AND ts >= \$4 ORDER BY ts ASC',
        cfg.bot_id, cfg.pair, cfg.timeframe_entry, since_ts
    )
    await conn.close()
    return rows

rows = asyncio.run(run_backtest())

if len(rows) < 100:
    print('WARNING: Insufficient candle data for backtest (<100 candles). Reverting.')
    shutil.copy(backup, f'{ROOT}/params.json')
    sys.exit(1)

from src.config import Candle
def row_to_candle(r):
    return Candle(
        bot_id=r['bot_id'], ts=r['ts'], pair=r['pair'], timeframe=r['timeframe'],
        open=float(r['open']), high=float(r['high']), low=float(r['low']),
        close=float(r['close']), volume=float(r['volume']),
        ema9=float(r['ema9']) if r['ema9'] else None,
        ema21=float(r['ema21']) if r['ema21'] else None,
        rsi14=float(r['rsi14']) if r['rsi14'] else None,
        atr14=float(r['atr14']) if r['atr14'] else None,
        bb_upper=float(r['bb_upper']) if r['bb_upper'] else None,
        bb_lower=float(r['bb_lower']) if r['bb_lower'] else None,
        bb_width=float(r['bb_width']) if r['bb_width'] else None,
        adx=float(r['adx']) if r['adx'] else None,
        volume_ma20=float(r['volume_ma20']) if r['volume_ma20'] else None,
        volume_ratio=float(r['volume_ratio']) if r['volume_ratio'] else None,
        regime=r['regime'],
        body_size=float(r['body_size']) if r['body_size'] else None,
        total_range=float(r['total_range']) if r['total_range'] else None,
        body_ratio=float(r['body_ratio']) if r['body_ratio'] else None,
        upper_wick=float(r['upper_wick']) if r['upper_wick'] else None,
        lower_wick=float(r['lower_wick']) if r['lower_wick'] else None,
        direction=r['direction'],
    )

candles = [row_to_candle(r) for r in rows]

from src.backtest.runner import walk_forward
from src.backtest.metrics import compare_metrics

# Load old params for baseline
params_old_raw = json.load(open(backup))
params_old_raw[PARAM]['value'] = old_val
with open('/tmp/params_old.json', 'w') as f:
    json.dump(params_old_raw, f, indent=2)
old_params = load_params('/tmp/params_old.json')

baseline = walk_forward(candles, old_params, cfg)
candidate = walk_forward(candles, params, cfg)

print()
print('=== Walk-forward Results ===')
print(f'Baseline  in-sample:  {baseline[\"in_sample\"]}')
print(f'Baseline  out-sample: {baseline[\"out_sample\"]}')
print(f'Candidate in-sample:  {candidate[\"in_sample\"]}')
print(f'Candidate out-sample: {candidate[\"out_sample\"]}')

comparison = compare_metrics(baseline['out_sample'], candidate['out_sample'])
print()
print('Comparison (out-of-sample):')
for k, v in comparison.items():
    if k == 'verdict':
        continue
    color = '\033[0;32m' if v == 'improve' else ('\033[0;31m' if v == 'regress' else '')
    reset = '\033[0m' if color else ''
    print(f'  {k}: {color}{v}{reset}')

verdict = comparison['verdict']
if verdict == 'ACCEPT':
    print(f'\n\033[0;32mVERDICT: ACCEPT\033[0m — params.json updated.')
else:
    print(f'\n\033[0;31mVERDICT: REJECT\033[0m — reverting params.json.')
    shutil.copy(backup, f'{ROOT}/params.json')
    print(f'Reverted to {backup}')
    sys.exit(1)
"
