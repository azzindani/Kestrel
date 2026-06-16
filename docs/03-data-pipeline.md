# 03 · Data Pipeline (Layer 2)

The data pipeline turns a raw exchange stream into the enriched `Candle` domain objects the
signal engine consumes. It lives in `src/data/` and is **Layer 2**: it assembles external
stream data into Layer 0/1 domain types. It contains the *shell* code that touches the
network; the *logic* (indicator math) lives in Layer 1 (`signal/indicators.py`) and is
called from here.

```
exchange  ─►  Feed (WS or REST poll)  ─►  CandleBuilder  ─►  Candle (OHLCV + indicators + geometry)
              one per (exchange[,mode])    one per bot         emitted on close → daemon queue
```

## 1. The feed — two implementations, one contract

Both feed implementations satisfy a small protocol (`data/providers/__init__.py`):

```python
class FeedProtocol(Protocol):
    last_reconnect_ts: Optional[int]   # unix ms of last reconnect (drives risk Rule 6)
    async def run() -> None
    def stop() -> None
```

The feed is selected by `FEED_MODE` in the environment: `"ws"` (default) or `"poll"`.

### 1a. `MarketFeed` — WebSocket (`data/feed.py`)

The default, real-time feed built on **ccxt.pro**.

- **One feed instance per exchange serves all bots on that exchange.** Bots register their
  interest via `feed.subscribe(pair, timeframe, builder, ...)`; subscriptions accumulate on a
  single ccxt.pro client. `feed.run()` is **idempotent** — the first caller drives
  `_stream_all()`, subsequent callers simply await the stop event. This is why standing up
  120 bots does not open 120 sockets.
- **Dispatch:** `_dispatch_ws()` fans one `(pair, timeframe)` stream out to *every* bot
  subscribed to it; each bot receives its own copy of the candle list (no cross-bot
  mutation). A historical bug — *"one poller per pair must feed ALL bots on it"* — was a
  case where half the fleet was starved because only one bot got the data; it is fixed.
- **Reconnection (exponential backoff):** `_MAX_RETRIES = 5`, `_BACKOFF_BASE = 2` → delays
  of 2 s → 4 s → 8 s → 16 s → 32 s. A successful reconnect resets the counter; five
  consecutive failures fire a **CRITICAL** Telegram alert, then the feed waits ~60 s and
  resets.
- **Stale-data guard:** on a reconnect (`retry_count > 0`), the feed stamps
  `_last_reconnect_ts = now_ms` and broadcasts it to every subscribed bot. The risk manager
  refuses to place orders within 60 s of a reconnect (Rule 6 — stale data). The daemon also
  must not place orders within 60 s of a WS reconnection (`CLAUDE.md` §16).

### 1b. `PollingFeed` — REST (`data/providers/polling.py`)

The reliable feed for **high timeframes**, selected with `FEED_MODE=poll`.

- **Why it exists:** a 4h candle closes once every four hours. ccxt.pro's websocket
  candle-rollover can be sparse or missed entirely on thin pairs, so a high-TF lab can sit
  idle waiting for a close event that never arrives. Polling `fetch_ohlcv()` detects newly
  *closed* candles deterministically.
- **Constants:** `_POLL_INTERVAL_S = 60` (poll once a minute), `_POLL_LIMIT = 5` (fetch the
  last five candles each poll). A `_TF_MS` table maps each timeframe to its period in ms.
- **Closed-candle detection** is a pure function, `new_closed_rows(rows, period_ms, now_ms,
  last_emitted)`: a row is *closed* when `ts + period_ms <= now_ms`. On the very first poll
  (`last_emitted is None`) it emits only the most recent closed row (seed without replaying
  history); thereafter it emits every closed row with `ts > last_emitted`.
- **No stale-data hazard:** because polling only ever returns *complete* closed candles via
  REST, there is no partial-candle risk and no reconnect cooldown. It reports
  `last_reconnect_ts = None` (risk Rule 6 treats `None` as "never reconnected" → never
  blocks on staleness).
- **Shared model:** one REST client per exchange, one poller per unique `(pair, timeframe)`,
  shared across all bots on that pair. The poll key is namespaced (`"gate:poll"` vs `"gate"`)
  so polling and WS feeds for the same exchange don't collide.

### 1c. Volume normalisation gotcha

Live Gate.io **websocket** candles report **quote** volume; the **REST** endpoint reports
**base** volume. The polling feed and the backfill script multiply base × close to convert
to quote volume so the `volume_ratio` indicator is on the same scale regardless of source.
Mixing the two without conversion would corrupt every volume filter.

## 2. Provider registry & venue support

`data/providers/__init__.py` registers feeds the same way patterns are registered:

- **ccxt-based crypto venues (shared feed)** — pre-registered through a ccxt.pro factory:
  `bybit, binance, okx, kucoin, kraken, gate, mexc, bitget, bingx, huobi`. One `MarketFeed`
  per exchange.
- **Non-ccxt providers (mostly per-bot)** — auto-imported modules that self-register via a
  decorator: `alpaca` (crypto/stock bars, WS), `oanda` (forex, REST poll), `ibkr`
  (multi-asset), `exness` (forex CFD via the MetaApi bridge — marked *shared*, one MetaApi
  connection fans out to all bots on the account).
- **`mock`** — a synthetic provider for tests.

This is what makes the broker decision "plug-and-play": pointing `EXCHANGE` at a different
ccxt venue requires no new code.

## 3. The candle builder (`data/candle_builder.py`)

One `CandleBuilder` exists per bot. It assembles ticks/closed bars into the enriched `Candle`
the signal engine needs.

- **Buffer size:** `_BUFFER_SIZE = 120`. The builder retains the most recent **120 candles** —
  the minimum history for reliable indicator computation. **This 120-candle window is a
  load-bearing constant**: it is the exact history the live detector sees, so any deployable
  pattern's lookback must be ≤ 120 (the research harness enforces the same limit — it is why
  Connors' SMA(200) silently never fires; see [Backtesting & Research](09-backtesting-research.md)).
- **Emission on close** (`process_ohlcv(ohlcv, is_closed=True)`):
  1. deduplicate by timestamp,
  2. compute candle **geometry** (body size, total range, body ratio, wicks, direction),
  3. build a preliminary `Candle`, append it to the buffer,
  4. compute **all indicators** over the buffer (`compute_all_indicators(...)`),
  5. rebuild the `Candle` enriched with indicators, replace the buffer entry,
  6. call the emitter callback → the daemon's candle queue.
- **Bootstrap:** `bootstrap(historical)` pre-fills the buffer from DB history at startup so
  the daemon is "warm" immediately and isn't blind for its first 120 candles. The
  `backfill_history.py` script populates that history (see [Deployment](10-deployment.md)).

Indicators are computed **once, at close, and stored** on the candle (and persisted to the
`candles` table). They are never recomputed downstream — a single source of truth per candle.

## 4. The indicator suite (`signal/indicators.py`, Layer 1)

All indicators are pure functions. They use Wilder's smoothing where appropriate (matching
standard TA definitions), and degrade gracefully when given too little history.

| Function | Signature | Notes |
|---|---|---|
| `compute_ema` | `(prices, period) -> float` | `k = 2/(period+1)`; SMA-seeded then EMA-smoothed. |
| `compute_rsi` | `(closes, period=14) -> float` | Wilder smoothing (α = 1/period). Returns `50.0` if too short; `100.0` if avg-loss is 0. |
| `compute_bb` | `(closes, period=20, std_dev=2.0) -> (upper, lower, width)` | `width = (upper − lower)/mean`. |
| `compute_atr` | `(candles, period=14) -> float` | True Range = max(\|H−L\|, \|H−prevC\|, \|L−prevC\|), Wilder-smoothed. |
| `compute_adx` | `(candles, period=14) -> float` | ±DM → ±DI → DX = 100·\|+DI − −DI\|/(+DI + −DI), Wilder-smoothed. Returns `0.0` if too short. |
| `compute_volume_ma` | `(volumes, period=20) -> float` | Simple moving average. |
| `compute_volume_stddev` | `(volumes, period=20) -> float` | Population stddev (÷ N). |
| `compute_all_indicators` | `(candles, ema_fast=9, ema_slow=21) -> dict` | Computes all ten in one pass → `{ema9, ema21, rsi14, atr14, bb_upper, bb_lower, bb_width, adx, volume_ma20, volume_ratio}`. |

These ten indicators (plus the precomputed candle geometry) are the entire feature set the
signal engine and patterns may use. The research conclusion that *"edge = timeframe + fees +
sizing, not new indicators"* (see [Backtesting & Research](09-backtesting-research.md)) is
why this list has stayed small and stable.
