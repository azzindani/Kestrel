#!/usr/bin/env python3
"""
Real-data walk-forward backtest (CLAUDE.md §30).

Fetches real historical OHLCV from the live feed exchange (gate, fallback okx),
builds indicator-enriched candles through the PRODUCTION CandleBuilder (so the
indicator path is identical to live), then runs the real walk_forward()
(train 60% / test 40%) with the fee + slippage model applied by the runner.

Run (one-off container, repo mounted, no DB needed):
  docker run --rm --entrypoint python \
    -v /root/Kestrel:/app -w /app kestrel-kestrel:latest \
    scripts/backtest_real.py --days 120 --pair BTC/USDT
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, "/app")  # make `src` importable when run as a plain script

import ccxt  # provided by the image

from dotenv import load_dotenv

from src.config import AppConfig, load_params
from src.data.candle_builder import CandleBuilder
from src.backtest.metrics import compute_metrics
from src.backtest.runner import walk_forward

# okx first: proven to paginate full history from this host. gate rejects long
# lookbacks; kraken hard-caps at ~720 candles so it can't serve a long span.
FETCH_ORDER = ("okx", "gate", "kraken")
_TF_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
_RETRIES = 3


def _fetch_one(name: str, pair: str, timeframe: str, start: int, now_ms: int, tf_ms: int) -> list[list]:
    ex = getattr(ccxt, name)({"enableRateLimit": True})
    ex.load_markets()
    rows: list[list] = []
    since = start
    while since < now_ms:
        batch = ex.fetch_ohlcv(pair, timeframe, since=since, limit=1000)
        if not batch:
            break
        batch = [r for r in batch if r[0] >= since]
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + tf_ms
        if len(batch) < 2:
            break
    seen: dict[int, list] = {}
    for r in rows:
        seen[int(r[0])] = r
    return [seen[k] for k in sorted(seen)]


def fetch_ohlcv(pair: str, timeframe: str, days: int) -> tuple[str, list[list]]:
    """Paginate real OHLCV `days` back from now. Returns (exchange_used, rows).

    Rejects any source that returns far fewer candles than the requested span
    implies (e.g. kraken's 720-candle cap) so the backtest never silently runs
    on a tiny window. Retries transient network errors before moving on.
    """
    tf_ms = _TF_MS[timeframe]
    span_ms = days * 86_400_000
    now_ms = int(time.time() * 1000)
    start = now_ms - span_ms
    min_required = int((span_ms / tf_ms) * 0.6)  # tolerate gaps/holidays, reject tiny windows

    last_err: Exception | None = None
    for name in FETCH_ORDER:
        for attempt in range(1, _RETRIES + 1):
            try:
                ordered = _fetch_one(name, pair, timeframe, start, now_ms, tf_ms)
                if len(ordered) >= min_required:
                    return name, ordered
                last_err = RuntimeError(
                    f"{name}: only {len(ordered)} candles (< {min_required} required for {days}d) — capped source"
                )
                print(f"  {name}: {len(ordered)} candles < {min_required} required — skipping (capped/partial)")
                break  # source can't serve this span; don't retry, move on
            except Exception as exc:  # noqa: BLE001 — survey loop
                last_err = exc
                print(f"  {name}: attempt {attempt}/{_RETRIES} failed ({type(exc).__name__}: {str(exc)[:70]})")
                time.sleep(2 * attempt)
    raise RuntimeError(f"all exchanges failed to serve {days}d; last error: {last_err}")


def build_candles(cfg: AppConfig, params, rows: list[list]) -> list:
    """Drive raw rows through the production CandleBuilder."""
    builder = CandleBuilder(cfg.bot_id, cfg.pair, cfg.timeframe_entry, params)
    built: list = []
    builder.set_emitter(built.append)
    for r in rows:
        builder.process_ohlcv([int(r[0]), r[1], r[2], r[3], r[4], r[5]], is_closed=True)
    return built


def _fmt(m: dict) -> str:
    rr = (abs(m["avg_win_usdt"] / m["avg_loss_usdt"]) if m["avg_loss_usdt"] else float("inf"))
    return (
        f"    trades={m['total_trades']}  win_rate={m['win_rate']*100:.1f}%  "
        f"net=${m['total_pnl_usdt']:.4f}  avg=${m['avg_pnl_usdt']:.4f}/trade\n"
        f"    avg_win=${m['avg_win_usdt']:.4f}  avg_loss=${m['avg_loss_usdt']:.4f}  "
        f"R/R={rr:.2f}  profit_factor={m['profit_factor']}\n"
        f"    sharpe={m['sharpe_ratio']}  max_dd=${m['max_drawdown_usdt']:.4f} "
        f"({m['max_drawdown_pct']:.1f}%)  avg_hold={m['avg_hold_candles']}c\n"
        f"    close_reasons={m['close_reasons']}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--pair", default=None, help="override PAIR from .env")
    ap.add_argument("--tf", default=None, help="override TIMEFRAME_ENTRY from .env")
    args = ap.parse_args()

    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    params = load_params("params.json")
    pair = args.pair or cfg.pair
    tf = args.tf or cfg.timeframe_entry

    print(f"=== Kestrel real-data walk-forward backtest ===")
    print(f"pair={pair}  tf={tf}  days={args.days}  leverage={cfg.leverage}x  "
          f"bucket=${cfg.bucket_size_usdt}  params=params.json (current)")

    print(f"\nFetching ~{args.days}d of real {pair} {tf} OHLCV...")
    ex_used, rows = fetch_ohlcv(pair, tf, args.days)
    t0 = time.strftime("%Y-%m-%d %H:%M", time.gmtime(rows[0][0] / 1000))
    t1 = time.strftime("%Y-%m-%d %H:%M", time.gmtime(rows[-1][0] / 1000))
    span_days = (rows[-1][0] - rows[0][0]) / 86_400_000
    print(f"  source={ex_used}  candles={len(rows)}  period={t0} → {t1} UTC ({span_days:.1f}d)")

    candles = build_candles(cfg, params, rows)
    print(f"  built {len(candles)} indicator-enriched candles via production CandleBuilder")

    split = int(len(candles) * 0.60)
    print(f"\nWalk-forward split: {split} train (in-sample) / {len(candles)-split} test (out-of-sample)\n")
    wf = walk_forward(candles, params, cfg, train_frac=0.60)

    print("IN-SAMPLE (train 60%):")
    print(_fmt(wf["in_sample"]))
    print("\nOUT-OF-SAMPLE (test 40%) — THIS is what gates go-live:")
    print(_fmt(wf["out_sample"]))

    # ---- §30 / §13 verdict on out-of-sample ----
    oos = wf["out_sample"]
    rr = abs(oos["avg_win_usdt"] / oos["avg_loss_usdt"]) if oos["avg_loss_usdt"] else float("inf")
    checks = [
        ("win_rate > 55%", oos["win_rate"] > 0.55, f"{oos['win_rate']*100:.1f}%"),
        ("avg R/R >= 1.2", rr >= 1.2, f"{rr:.2f}"),
        ("net PnL > 0", oos["total_pnl_usdt"] > 0, f"${oos['total_pnl_usdt']:.4f}"),
        ("trades >= 30 (sample size)", oos["total_trades"] >= 30, str(oos["total_trades"])),
    ]
    # ---- Diagnostic: per-pattern / per-direction breakdown (full period) ----
    from src.backtest.runner import run_backtest

    full = run_backtest(candles, params, cfg)["trades"]

    def _grp(trades, keyfn):
        g: dict = {}
        for t in trades:
            g.setdefault(keyfn(t), []).append(t)
        return g

    def _line(label, ts):
        n = len(ts)
        w = sum(1 for t in ts if float(t["pnl_net_usdt"]) > 0)
        net = sum(float(t["pnl_net_usdt"]) for t in ts)
        return f"    {label:28s} n={n:4d}  win={w/n*100 if n else 0:5.1f}%  net=${net:8.3f}"

    print(f"\n=== DIAGNOSTIC: full-period breakdown ({len(full)} trades) ===")
    print("  by pattern:")
    for k, ts in sorted(_grp(full, lambda t: t["pattern"]).items(), key=lambda kv: -len(kv[1])):
        print(_line(k, ts))
    print("  by direction:")
    for k, ts in sorted(_grp(full, lambda t: t["direction"]).items()):
        print(_line(k, ts))
    print("  by close_reason:")
    for k, ts in sorted(_grp(full, lambda t: t["close_reason"]).items()):
        print(_line(k, ts))
    # Fee-drag context: cost per round trip in bucket terms
    notional = cfg.bucket_size_usdt * cfg.leverage
    fee_rt = notional * (0.0004 * 2)
    slip_rt = notional * (0.0005 * 2)
    cost = fee_rt + slip_rt
    print(f"\n  fee+slippage drag per trade ≈ ${cost:.3f} "
          f"({cost/cfg.bucket_size_usdt*100:.2f}% of ${cfg.bucket_size_usdt} bucket) "
          f"at {cfg.leverage}x — a winner must clear this before any profit.")

    print("\n=== GO-LIVE VERDICT (out-of-sample, CLAUDE.md §30) ===")
    all_pass = True
    for label, ok, val in checks:
        all_pass = all_pass and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:32s} = {val}")
    print(f"\n  >>> {'STRATEGY VALIDATED — eligible to proceed' if all_pass else 'NOT VALIDATED — re-tune params before any real money'} <<<")


if __name__ == "__main__":
    main()
