#!/usr/bin/env python3
"""Entry-condition miner — learn candidate entry gates from our OWN closed trades.

The learning loop this project actually has evidence for. Iter 66b mined 1,311
live trades ad-hoc and found that direction-ALIGNED entry RSI >= 70 ran -46.4 bps
at 27% win — the archive knew something the backtests had not been asked. That
mining was done by hand; this is the systematic version, now over a much larger
archive.

WHAT IT DOES. Every closed trade is joined to its SIGNAL candle (the candle that
closed immediately before entry, i.e. the state the detector actually saw), and
each stored feature is bucketed. Per bucket it reports sample count, win rate and
average net basis points. A feature whose buckets separate outcomes — especially
monotonically — is a candidate entry gate.

WHAT IT IS NOT. This finds HYPOTHESES, not edges. Mining the same trades that
produced the archive is in-sample by construction, and with enough features
something always separates. Every candidate must then be validated the normal way:
implemented as an algo_search gate and required to hold in BOTH the recent year and
the untouched prior-year lockbox across >=3 pairs. That bar has refuted most leads
this project has generated, including three in a single session (adversarial gate,
funding tilt, VWMA/MFI), and it is the only reason the surviving ones mean anything.

Oscillators are reported in DIRECTION-ALIGNED form (value for longs, 100-value for
shorts) as well as raw, because a long at RSI 80 and a short at RSI 20 are the same
"chasing an extended move" condition and pooling them raw cancels the signal out.
That framing is what made the iter-66b lead visible.

Read-only: it issues SELECTs and writes nothing.

Run:
  docker run --rm --network kestrel_net --env-file .env -e DB_HOST=postgres \
    -e PYTHONPATH=/app -v /root/Kestrel:/app -w /app --entrypoint python3 \
    kestrel-kestrel:latest scripts/mine_entry_conditions.py --tf 5m --min-n 40
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from dotenv import load_dotenv

from src.config import AppConfig
from src.db import connection as db_conn

# Timeframe -> candle period in ms, used to step back to the signal candle.
_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}

# Oscillators bounded 0-100, where the direction-aligned view is the meaningful one.
_ALIGNED_FEATURES = {"rsi14"}


@dataclass(frozen=True)
class Bucket:
    """One bucket of trades sharing a feature range."""

    label: str
    n: int
    win_pct: float
    avg_bps: float


def _pct(values: Sequence[float], q: float) -> float:
    """Pure: the q-quantile (0-1) of values by nearest rank."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def quantile_edges(values: Sequence[float], n_buckets: int) -> list[float]:
    """Pure: interior cut points splitting values into n_buckets quantiles.

    Duplicates are collapsed, so a feature with few distinct values yields fewer
    buckets rather than several identical ones.
    """
    if len(values) < n_buckets:
        return []
    if max(values) == min(values):
        return []  # constant feature: no cut can split it, so report no buckets
    cuts = [_pct(values, i / n_buckets) for i in range(1, n_buckets)]
    out: list[float] = []
    for c in cuts:
        if not out or c > out[-1]:
            out.append(c)
    return out


def bucketize(rows: Sequence[tuple[float, bool, float]], edges: Sequence[float], min_n: int) -> list[Bucket]:
    """Pure: split (value, won, net_bps) rows by edges into reported buckets.

    Buckets thinner than min_n are dropped rather than reported — a 3-trade bucket
    at 100% win is noise, and reporting it is how mining turns into self-deception.
    """
    groups: dict[int, list[tuple[bool, float]]] = {}
    for value, won, bps in rows:
        idx = 0
        for i, edge in enumerate(edges, start=1):
            if value > edge:
                idx = i
        groups.setdefault(idx, []).append((won, bps))

    buckets: list[Bucket] = []
    for idx in sorted(groups):
        items = groups[idx]
        if len(items) < min_n:
            continue
        lo = "-inf" if idx == 0 else f"{edges[idx - 1]:.3g}"
        hi = "+inf" if idx >= len(edges) else f"{edges[idx]:.3g}"
        buckets.append(
            Bucket(
                label=f"({lo}, {hi}]",
                n=len(items),
                win_pct=100.0 * sum(1 for w, _ in items if w) / len(items),
                avg_bps=statistics.fmean(b for _, b in items),
            )
        )
    return buckets


def separation(buckets: Sequence[Bucket]) -> float:
    """Pure: spread in avg_bps between the best and worst bucket (0 if <2)."""
    if len(buckets) < 2:
        return 0.0
    return max(b.avg_bps for b in buckets) - min(b.avg_bps for b in buckets)


def is_monotone(buckets: Sequence[Bucket]) -> bool:
    """Pure: True if avg_bps moves consistently one way across buckets.

    Monotone separation is far more credible than a single odd bucket: it says the
    feature has a direction, not that one slice got lucky.
    """
    if len(buckets) < 3:
        return False
    vals = [b.avg_bps for b in buckets]
    up = all(b >= a for a, b in zip(vals, vals[1:]))
    down = all(b <= a for a, b in zip(vals, vals[1:]))
    return up or down


async def load_trades(tf: str, env: str, limit_pattern: Optional[str]) -> list[dict[str, Any]]:
    """I/O shell: closed trades joined to the candle that closed before entry."""
    period = _TF_MS[tf]
    sql = """
        SELECT t.pnl_net_usdt, t.notional_usdt, t.direction,
               split_part(t.bot_id,'-',4) AS strategy,
               c.rsi14, c.adx, c.atr14, c.bb_width, c.volume_ratio,
               c.ema9, c.ema21, c.close, c.regime,
               EXTRACT(hour FROM to_timestamp(t.entry_ts/1000)) AS utc_hour
        FROM trades t
        JOIN candles c
          ON c.bot_id = t.bot_id AND c.pair = t.pair AND c.timeframe = t.timeframe
         AND c.ts = (t.entry_ts / $1) * $1 - $1
        WHERE t.exit_ts IS NOT NULL
          AND t.timeframe = $2
          AND t.env = $3
          AND t.close_reason <> 'orphaned_crash_recovery'
    """
    args: list[Any] = [period, tf, env]
    if limit_pattern:
        sql += " AND split_part(t.bot_id,'-',4) = $4"
        args.append(limit_pattern)
    async with db_conn.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


def features_for(row: dict[str, Any]) -> dict[str, Optional[float]]:
    """Pure: the feature vector the detector could have seen at entry."""
    is_long = str(row["direction"]).lower().endswith("long")
    out: dict[str, Optional[float]] = {}
    for name in ("rsi14", "adx", "atr14", "bb_width", "volume_ratio"):
        v = row.get(name)
        out[name] = float(v) if v is not None else None
    # Direction-aligned oscillator: a long at RSI 80 and a short at RSI 20 are the
    # same condition; pooled raw they cancel.
    if out.get("rsi14") is not None:
        out["rsi14_aligned"] = out["rsi14"] if is_long else 100.0 - float(out["rsi14"])
    # Trend stretch as a fraction of price, aligned to the trade's direction.
    ema9, ema21, close = row.get("ema9"), row.get("ema21"), row.get("close")
    if ema9 is not None and ema21 is not None and close:
        spread = (float(ema9) - float(ema21)) / float(close) * 10_000.0
        out["ema_spread_bps_aligned"] = spread if is_long else -spread
    out["utc_hour"] = float(row["utc_hour"]) if row.get("utc_hour") is not None else None
    return out


def net_bps(row: dict[str, Any]) -> Optional[float]:
    """Pure: trade PnL in basis points of notional (comparable across pairs)."""
    notional = row.get("notional_usdt")
    pnl = row.get("pnl_net_usdt")
    if not notional or pnl is None or float(notional) == 0.0:
        return None
    return float(pnl) / float(notional) * 10_000.0


def render(feature: str, buckets: Sequence[Bucket], monotone: bool, sep: float) -> list[str]:
    flag = " [MONOTONE]" if monotone else ""
    lines = [f"\n{feature}  (separation {sep:.1f} bps){flag}"]
    for b in buckets:
        lines.append(f"    {b.label:>22s}  n={b.n:5d}  win {b.win_pct:5.1f}%  avg {b.avg_bps:+7.2f} bps")
    return lines


def render_categorical(name: str, groups: dict[str, list[tuple[bool, float]]], min_n: int) -> list[str]:
    rows = [(k, v) for k, v in groups.items() if len(v) >= min_n]
    if not rows:
        return []
    lines = [f"\n{name}"]
    for key, items in sorted(rows, key=lambda kv: statistics.fmean(b for _, b in kv[1])):
        win = 100.0 * sum(1 for w, _ in items if w) / len(items)
        lines.append(
            f"    {key:>22s}  n={len(items):5d}  win {win:5.1f}%  avg {statistics.fmean(b for _, b in items):+7.2f} bps"
        )
    return lines


async def run(args: argparse.Namespace) -> None:
    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    await db_conn.init_pool(cfg)
    try:
        rows = await load_trades(args.tf, args.env, args.strategy)
    finally:
        await db_conn.close_pool()

    samples: list[tuple[dict[str, Optional[float]], bool, float, str, str]] = []
    for r in rows:
        bps = net_bps(r)
        if bps is None:
            continue
        samples.append((features_for(r), float(r["pnl_net_usdt"]) > 0, bps, str(r["regime"]), str(r["strategy"])))

    out: list[str] = [
        f"=== ENTRY-CONDITION MINING — env={args.env} tf={args.tf} "
        f"strategy={args.strategy or 'ALL'} n={len(samples)} (min bucket {args.min_n}) ===",
        "IN-SAMPLE by construction. Candidates must clear the cross-era lockbox before deploy.",
    ]
    if len(samples) < args.min_n * 2:
        out.append(f"\nInsufficient trades ({len(samples)}) for {args.min_n}-trade buckets — nothing mined.")
        print("\n".join(out))
        return

    overall = statistics.fmean(s[2] for s in samples)
    overall_win = 100.0 * sum(1 for s in samples if s[1]) / len(samples)
    out.append(f"\nBASELINE: win {overall_win:.1f}%  avg {overall:+.2f} bps  n={len(samples)}")

    feature_names = sorted({k for s in samples for k, v in s[0].items() if v is not None})
    scored: list[tuple[float, bool, str, list[Bucket]]] = []
    for name in feature_names:
        triples = [(s[0][name], s[1], s[2]) for s in samples if s[0].get(name) is not None]
        values = [t[0] for t in triples]
        edges = quantile_edges(values, args.buckets)  # type: ignore[arg-type]
        buckets = bucketize(triples, edges, args.min_n)  # type: ignore[arg-type]
        if len(buckets) >= 2:
            scored.append((separation(buckets), is_monotone(buckets), name, buckets))

    out.append("\n--- numeric features, ranked by outcome separation ---")
    for sep, mono, name, buckets in sorted(scored, key=lambda x: (-x[1], -x[0])):
        out.extend(render(name, buckets, mono, sep))

    by_regime: dict[str, list[tuple[bool, float]]] = {}
    by_strategy: dict[str, list[tuple[bool, float]]] = {}
    for feats, won, bps, regime, strategy in samples:
        by_regime.setdefault(regime, []).append((won, bps))
        by_strategy.setdefault(strategy, []).append((won, bps))
    out.append("\n--- categorical ---")
    out.extend(render_categorical("regime", by_regime, args.min_n))
    out.extend(render_categorical("strategy", by_strategy, args.min_n))

    out.append(
        "\nNEXT STEP: take the strongest MONOTONE separation, implement it as an "
        "--adversarial-gate-style entry gate in algo_search.py, and require it to hold "
        "in BOTH eras across >=3 pairs. Mining proposes; the lockbox decides."
    )
    # Operator-facing analysis tool, not the daemon's logging path (§3 governs the
    # daemon, whose channel is the events table).
    print("\n".join(out))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tf", default="5m", choices=sorted(_TF_MS), help="entry timeframe to mine")
    ap.add_argument("--env", default="dev", help="environment to mine (default dev)")
    ap.add_argument("--strategy", default=None, help="restrict to one strategy label")
    ap.add_argument("--buckets", type=int, default=5, help="quantile buckets per numeric feature")
    ap.add_argument("--min-n", type=int, default=40, dest="min_n", help="minimum trades per reported bucket")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
