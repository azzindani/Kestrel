-- verify_live.sql — Kestrel live-inference health check.
-- Confirms the daemon is streaming a REAL feed and the signal engine is
-- evaluating every candle. Re-runnable any time after the feed switch.
--
-- Run:
--   docker exec -i kestrel-postgres-1 psql -U kestrel -d kestrel < infra/verify_live.sql
--
-- All ts columns are unix ms (BIGINT); "now" = (EXTRACT(EPOCH FROM NOW())*1000)::BIGINT.

\echo ''
\echo '── 1. FEED · latest candles (real BTC close ≈ 70k, age_sec < ~310 = live) ──'
SELECT pair, timeframe, close,
       round((EXTRACT(EPOCH FROM NOW())*1000 - ts)/1000) AS age_sec,
       to_timestamp(ts/1000) AT TIME ZONE 'UTC' AS candle_close_utc
FROM candles ORDER BY ts DESC LIMIT 4;

\echo ''
\echo '── 2. WARMUP · candles accumulated per pair (≈50+ before signals can fire) ──'
SELECT pair,
       count(*)                                            AS candles,
       bool_or(ema21 IS NOT NULL)                          AS ema_ready,
       bool_or(adx IS NOT NULL)                            AS adx_ready,
       (array_agg(regime ORDER BY ts DESC) FILTER (WHERE regime IS NOT NULL))[1] AS latest_regime
FROM candles GROUP BY pair ORDER BY pair;

\echo ''
\echo '── 3. PIPELINE · events last 30 min by category (proves loop is processing) ──'
SELECT category, count(*) AS n,
       to_timestamp(max(ts)/1000) AT TIME ZONE 'UTC' AS last_event_utc
FROM events
WHERE ts > (EXTRACT(EPOCH FROM NOW())*1000)::BIGINT - 1800000
GROUP BY category ORDER BY n DESC;

\echo ''
\echo '── 4. INFERENCE · signal-engine activity last 2h (every candle is evaluated) ──'
SELECT message AS engine_outcome, count(*) AS n
FROM events
WHERE category IN ('signal','risk')
  AND ts > (EXTRACT(EPOCH FROM NOW())*1000)::BIGINT - 7200000
GROUP BY message ORDER BY n DESC;

\echo ''
\echo '── 5. FIRED/REJECTED signals last 2h (rows reach the signals table) ──'
SELECT outcome, coalesce(reject_reason,'—') AS reason, pattern, count(*) AS n
FROM signals
WHERE ts > (EXTRACT(EPOCH FROM NOW())*1000)::BIGINT - 7200000
GROUP BY outcome, reject_reason, pattern ORDER BY n DESC;

\echo ''
\echo '── 6. TRADES · most recent 5 (paper, ENV=dev) ──'
SELECT id, pair, direction, pattern,
       to_timestamp(entry_ts/1000) AT TIME ZONE 'UTC' AS entry_utc,
       coalesce(close_reason,'OPEN') AS close_reason,
       round(pnl_net_usdt::numeric,4) AS pnl_net_usdt
FROM trades ORDER BY entry_ts DESC LIMIT 5;

\echo ''
\echo '── 7. LIVENESS · heartbeat (status=running, hb_age_sec < 60) ──'
SELECT bot_id, status, pid,
       round((EXTRACT(EPOCH FROM NOW())*1000 - ts)/1000) AS hb_age_sec
FROM heartbeats ORDER BY ts DESC;
\echo ''
