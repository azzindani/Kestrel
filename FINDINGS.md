# Kestrel — Go-Live Research Findings

**Date:** 2026-06-02
**Question:** Is Kestrel ready to trade real money ("go live / use inference")?
**Answer:** **No.** No validated tradeable edge exists at any tested timeframe. Do not fund an account or issue real API keys. The engineering is sound; the *strategy* lacks an edge.

---

## What was tested (real market data, not the synthetic mock)

All on real OHLCV (okx REST, BTC/USDT + ETH/USDT), with the §13/§17 fee+slippage model applied.

| # | Experiment | Tool | Result |
|---|---|---|---|
| 1 | Walk-forward backtest, current params, 120d | `scripts/backtest_real.py` | **Loses** — 14.3% OOS win rate, −$5.41, all §30 checks fail |
| 2 | 36-config param sweep, 90d | `scripts/param_sweep.py` | **0/36 pass** — apparent winners were overfit noise (negative in-sample) |
| 3 | Feature predictive-power scan, 5m | `scripts/edge_scan.py` | **No feature beats cost.** Mean 5m move 0.164% < 0.18% round-trip cost |
| 4 | Timeframe scan (1h/4h) | `scripts/edge_scan.py --tf` | 4h moves clear cost; `ema_spread` mean-reversion *looked* promising |
| 5 | 4h `ema_spread` walk-forward, real strategy, BTC+ETH, 365d | `scripts/research_4h_meanrev.py` | **Edge dissolved** — negative in-sample under taker cost in 8/8 configs; every OOS-positive paired with IS-negative (sign-flip noise, t≈0); no config positive in both train+test under taker *or* maker cost |

## Root cause

The dominant problem is **cost relative to move size**. At 5m, the average move is *smaller* than the ~0.18% round-trip transaction cost — a negative-sum game. Higher timeframes have larger moves, but the only signal that looked stable (`ema_spread` mean-reversion) has a real but tiny raw effect (~0.04%/trade gross) that is below even an optimistic maker cost (~0.06%). The hand-crafted patterns, the params, and the features all fail for the same underlying reason: there is no exploitable structure in these OHLCV-derived inputs large enough to overcome trading costs.

**Methodological lesson (recorded):** a feature whose scan IC / quintile-spread exceeds cost does **not** imply a tradeable strategy. The Q5−Q1 spread is a long-short portfolio statistic over overlapping windows; only a non-overlapping train/test strategy backtest reveals whether a realizable edge exists. It did not.

## Recommendation

1. **Run as a paper/research platform on live data** — set `EXCHANGE=gate` in `.env`, `docker compose up -d --force-recreate kestrel`. This exercises every infrastructure go-live criterion (Telegram, watchdog, backups, stop.sh, 14-day stability) and banks a real labeled dataset (`trade_context`, §21) — without risking money on a strategy that does not work.
2. **Do not** create an exchange account, request real API keys, fund capital, or provision a production VPS for live trading.

## What would change the verdict

A genuinely new information source or edge — order-book microstructure, alternative data, or properly cross-validated ML — *not* further tuning of the current OHLCV patterns/params, which is exhausted. Any such work must clear the same bar: positive, same-sign expectancy in **both** train and test under realistic cost, on **multiple** pairs.

## Reusable research harness (added during this investigation)

Run any via: `docker run --rm --entrypoint python -v /root/Kestrel:/app -w /app kestrel-kestrel:latest -u scripts/<name>.py [args]`

- `scripts/backtest_real.py` — real-data walk-forward backtest of the live strategy + per-pattern/direction/exit diagnostics.
- `scripts/param_sweep.py` — grid search over params.json, OOS-ranked.
- `scripts/edge_scan.py` — per-feature predictive-power (IC + quintile spread vs cost), `--tf`, `--walkforward`.
- `scripts/research_4h_meanrev.py` — non-overlapping mean-reversion strategy walk-forward, taker vs maker, BTC+ETH.
- `infra/verify_live.sql` — live health check (feed real? engine firing?) — `docker exec -i kestrel-postgres-1 psql -U kestrel -d kestrel < infra/verify_live.sql`.
