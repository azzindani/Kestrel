#!/usr/bin/env bash
# install.sh — full setup from zero.
# Prints [GO] on success, [NO-GO] + reason on failure.
# Called by update.sh and required before start.sh.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'

fail() { echo -e "${RED}[NO-GO]${NC} $1"; exit 1; }
pass() { echo -e "${GREEN}[GO]${NC} $1"; }

# ── 1. Python ≥ 3.11 ────────────────────────────────────────────────────────
echo "Checking Python version..."
PYTHON=$(command -v python3.11 || command -v python3 || fail "python3 not found")
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
MAJOR="${PY_VER%%.*}"; MINOR="${PY_VER##*.}"
[[ "$MAJOR" -ge 3 && "$MINOR" -ge 11 ]] || fail "Python ≥ 3.11 required (found $PY_VER)"
pass "Python $PY_VER"

# ── 2. Virtual environment ───────────────────────────────────────────────────
echo "Setting up venv..."
if [[ ! -d "$ROOT/venv" ]]; then
    "$PYTHON" -m venv "$ROOT/venv" || fail "Failed to create venv"
fi
source "$ROOT/venv/bin/activate"

# ── 3. Dependencies ──────────────────────────────────────────────────────────
echo "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "$ROOT/requirements.txt" || fail "pip install failed"
pass "Dependencies installed"

# ── 4. .env complete ─────────────────────────────────────────────────────────
echo "Checking .env..."
[[ -f "$ROOT/.env" ]] || fail ".env not found (copy from .env.example and fill values)"
source "$ROOT/.env"
REQUIRED_VARS=(
    ENV BOT_ID EXCHANGE API_KEY API_SECRET TESTNET
    DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD
    PAIR TIMEFRAME_ENTRY TIMEFRAME_REGIME
    LEVERAGE BUCKET_SIZE_USDT MAX_ACTIVE_BUCKETS
    TELEGRAM_TOKEN TELEGRAM_CHAT_ID LOG_LEVEL
)
for VAR in "${REQUIRED_VARS[@]}"; do
    [[ -n "${!VAR:-}" ]] || fail ".env missing: $VAR"
done
pass ".env complete"

# ── 5. DB reachable + schema ─────────────────────────────────────────────────
echo "Checking PostgreSQL..."
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -c "SELECT 1" > /dev/null 2>&1 || fail "Cannot connect to PostgreSQL at $DB_HOST:$DB_PORT/$DB_NAME"
pass "PostgreSQL reachable"

echo "Applying schema..."
python -c "
import asyncio, os
from dotenv import load_dotenv
load_dotenv()
from src.config import AppConfig
from src.db.connection import init_pool, close_pool
from src.db.schema import apply_schema

async def run():
    cfg = AppConfig.from_mapping(os.environ)
    await init_pool(cfg)
    await apply_schema()
    await close_pool()

asyncio.run(run())
" || fail "Schema migration failed"
pass "Schema applied"

# ── 6. Exchange auth valid ───────────────────────────────────────────────────
echo "Verifying exchange credentials..."
python -c "
import asyncio, os, ccxt.async_support as ccxt
from dotenv import load_dotenv
load_dotenv()
exchange_cls = getattr(ccxt, os.environ['EXCHANGE'])
ex = exchange_cls({'apiKey': os.environ['API_KEY'], 'secret': os.environ['API_SECRET']})
if os.environ.get('TESTNET', '').lower() in ('1', 'true', 'yes'):
    ex.set_sandbox_mode(True)
async def run():
    await ex.fetch_balance()
    await ex.close()
asyncio.run(run())
" || fail "Exchange credential verification failed"
pass "Exchange credentials valid"

# ── 7. Telegram reachable ────────────────────────────────────────────────────
echo "Checking Telegram..."
TELE_RESP=$(curl -s --max-time 5 \
    "https://api.telegram.org/bot${TELEGRAM_TOKEN}/getMe" 2>&1)
echo "$TELE_RESP" | grep -q '"ok":true' || fail "Telegram bot token invalid or unreachable"
pass "Telegram reachable"

# ── 8. params.json valid ─────────────────────────────────────────────────────
echo "Validating params.json..."
python -c "
from src.config import load_params
p = load_params('params.json')
assert p.max_active_buckets >= 1
assert p.min_confidence >= 0.4
print('params valid')
" || fail "params.json validation failed"
pass "params.json valid"

echo ""
echo -e "${GREEN}[GO] — all checks passed. Safe to start.${NC}"
