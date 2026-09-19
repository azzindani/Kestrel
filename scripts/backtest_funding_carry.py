#!/usr/bin/env python3
"""Funding-rate carry backtest (iter 70, owner-authorized research 2026-09-19).

The one persistent edge class in crypto that does not need price prediction: when perp
funding is positive, longs pay shorts every settlement. A cash-and-carry position —
long SPOT + short PERP, same coin, same size — is price-neutral and collects that
funding. This script measures whether it clears realistic costs, per coin and pooled,
in two separate years, with rules that see only funding already settled.

SCOPE NOTE. This is outside the minutes-scalp design (§6) and needs a SPOT leg (§13 has
spot as data-only): research only, nothing here is wired into the daemon. The owner
decides after seeing the numbers.

DATA. Binance's public archive (data.binance.vision) — the futures REST API is
geo-blocked from this host, the archive is not. Monthly files: futures fundingRate,
futures 1h klines, spot 1h klines. Cached under reports/funding_archive/ (gitignored).
Binance funding is the venue-agnostic reference; --bingx-compare prints how BingX's own
recent funding compares, since BingX is the venue the stack trades.

MODEL (per coin, notional Q, perp leverage L):
  decision  at each funding settlement, after its rate is known: signal = mean of the
            last N settled rates. flat & signal >= entry -> enter at that hour's close;
            in & signal < exit -> leave at that hour's close. Funding accrues from the
            NEXT settlement (no lookahead).
  funding   short perp receives rate x Q x perp price at each settlement (pays if < 0).
  basis     P&L of the pair = (spot_exit - spot_entry) - (perp_exit - perp_entry) per unit.
  costs     each entry and exit: Q x (spot_fee + perp_fee + 2 x slippage).
  margin    a rally eats the short perp's margin (the spot gain is on the other account).
            When the perp high rallies half the liquidation distance from the last
            reference, rebalance = one extra round trip; if one hour jumps from below the
            trigger straight through the liquidation price, count a liquidation: one round
            trip + 1% of notional penalty.
  capital   Q x (1 + 1/L): spot paid in full + perp margin. Return on capital, idle
            capital earns nothing.

Run:
  python3 scripts/backtest_funding_carry.py                      # 34 scalp pairs, both years
  python3 scripts/backtest_funding_carry.py --pairs BTC/USDT,ETH/USDT --lev 3 --bingx-compare
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import os
import statistics as st
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_momentum_lab import SCALP_PAIRS  # noqa: E402 — research harness import path

_ARCHIVE = "https://data.binance.vision/data"
_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "funding_archive")
_HOUR = 3_600_000
_MMR = 0.005

# Two consecutive years, each the other's out-of-sample check.
YEARS = {
    "A 2024-09..2025-08": [(2024, m) for m in range(9, 13)] + [(2025, m) for m in range(1, 9)],
    "B 2025-09..2026-08": [(2025, m) for m in range(9, 13)] + [(2026, m) for m in range(1, 9)],
}

# (name, signal window N settlements, entry threshold, exit threshold) — rates as fractions/8h.
RULES = [
    ("always_on", 0, float("-inf"), float("-inf")),
    ("n3_in1bp_out0", 3, 0.0001, 0.0),
    ("n3_in2bp_out0.5bp", 3, 0.0002, 0.00005),
    ("n9_in1bp_out0", 9, 0.0001, 0.0),
    ("n9_in2bp_out1bp", 9, 0.0002, 0.0001),
]

FEES = {
    # spot fee, perp fee, slippage — per leg per side (fractions)
    "taker": (0.0010, 0.0005, 0.0003),
    "maker": (0.0010, 0.0002, 0.0),
}


def _fut_symbol(pair: str) -> list[str]:
    base = pair.split("/")[0]
    return [f"{base}USDT", f"1000{base}USDT"]


def _download(url: str, path: str) -> bytes | None:
    if os.path.exists(path):
        with open(path, "rb") as fh:
            blob = fh.read()
        return blob or None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                blob = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                blob = b""
                break
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 * (attempt + 1))
    else:
        return None  # transient failure: not cached, retried on the next run
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(blob)
    return blob or None


def _csv_rows(blob: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        text = zf.read(zf.namelist()[0]).decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    if rows and not rows[0][0].lstrip("-").isdigit():
        rows = rows[1:]  # header row (futures files carry one, spot files do not)
    return rows


def _ms(raw: str) -> int:
    v = int(raw)
    return v // 1000 if v > 10**14 else v  # spot archive switched to microseconds in 2025


@dataclass
class Series:
    funding: list[tuple[int, float]]  # (settlement ts, rate)
    spot: dict[int, float]  # hour close-time -> close
    perp: dict[int, tuple[float, float]]  # hour close-time -> (close, high)
    scale: float  # perp price / spot price unit ratio (1000 for 1000PEPE-style contracts)


def load_series(pair: str, months: list[tuple[int, int]]) -> Series | None:
    base = pair.split("/")[0]
    spot_sym = f"{base}USDT"
    for fut in _fut_symbol(pair):
        funding: list[tuple[int, float]] = []
        perp: dict[int, tuple[float, float]] = {}
        spot: dict[int, float] = {}
        for y, m in months:
            tag = f"{y}-{m:02d}"
            fb = _download(
                f"{_ARCHIVE}/futures/um/monthly/fundingRate/{fut}/{fut}-fundingRate-{tag}.zip",
                f"{_CACHE}/fut/{fut}-fundingRate-{tag}.zip",
            )
            if fb:
                for r in _csv_rows(fb):
                    funding.append((_ms(r[0]), float(r[2])))
            kb = _download(
                f"{_ARCHIVE}/futures/um/monthly/klines/{fut}/1h/{fut}-1h-{tag}.zip",
                f"{_CACHE}/fut/{fut}-1h-{tag}.zip",
            )
            if kb:
                for r in _csv_rows(kb):
                    perp[_ms(r[0]) + _HOUR] = (float(r[4]), float(r[2]))
            sb = _download(
                f"{_ARCHIVE}/spot/monthly/klines/{spot_sym}/1h/{spot_sym}-1h-{tag}.zip",
                f"{_CACHE}/spot/{spot_sym}-1h-{tag}.zip",
            )
            if sb:
                for r in _csv_rows(sb):
                    spot[_ms(r[0]) + _HOUR] = float(r[4])
        if funding and perp and spot:
            funding.sort()
            common = sorted(set(perp) & set(spot))
            if not common:
                continue
            ratio = st.median(perp[t][0] / spot[t] for t in common[:: max(1, len(common) // 200)])
            scale = 1000.0 if ratio > 100 else 1.0
            return Series(funding=funding, spot=spot, perp=perp, scale=scale)
    return None


@dataclass
class Result:
    funding: float = 0.0  # all as fractions of notional
    fees: float = 0.0
    basis: float = 0.0
    rebalances: int = 0
    liquidations: int = 0
    switches: int = 0
    settlements_in: int = 0
    settlements_all: int = 0

    @property
    def net(self) -> float:
        return self.funding - self.fees + self.basis


def _hour_at_or_before(prices: dict, ts: int) -> int | None:
    t = (ts // _HOUR) * _HOUR
    for back in range(0, 6):
        if t - back * _HOUR in prices:
            return t - back * _HOUR
    return None


def simulate(s: Series, rule: tuple, lev: float, fee: tuple[float, float, float]) -> Result:
    name, n_sig, entry_thr, exit_thr = rule
    spot_fee, perp_fee, slip = fee
    rt_side = spot_fee + perp_fee + 2.0 * slip  # one side (entry OR exit), both legs
    res = Result(settlements_all=len(s.funding))
    liq_dist = 1.0 / lev - _MMR
    trigger_dist = 0.5 / lev

    in_pos = False
    e_spot = e_perp = 0.0
    ref = 0.0
    hours = sorted(s.perp)
    hour_idx = {t: i for i, t in enumerate(hours)}
    last_checked_hour = None

    def mark(t: int) -> tuple[float, float] | None:
        hs, hp = _hour_at_or_before(s.spot, t), _hour_at_or_before(s.perp, t)
        if hs is None or hp is None:
            return None
        return s.spot[hs], s.perp[hp][0] / s.scale

    def margin_walk(until_ts: int) -> None:
        """Walk hourly perp highs since the last check: rebalance / liquidation events."""
        nonlocal ref, last_checked_hour, e_spot, e_perp
        start = hour_idx.get(last_checked_hour, -1) + 1 if last_checked_hour is not None else 0
        for i in range(start, len(hours)):
            t = hours[i]
            if t > until_ts:
                break
            last_checked_hour = t
            close, high = s.perp[t]
            close, high = close / s.scale, high / s.scale
            if high >= ref * (1.0 + trigger_dist):
                if high >= ref * (1.0 + liq_dist):
                    res.liquidations += 1
                    res.fees += 2.0 * rt_side + 0.01
                else:
                    res.rebalances += 1
                    res.fees += 2.0 * rt_side
                # Reset the hedge at this hour's close: realise basis so far, re-enter.
                m = mark(t)
                if m is not None:
                    sp, pp = m
                    res.basis += (sp - e_spot) / e_spot - (pp - e_perp) / e_spot
                    e_spot, e_perp = sp, pp
                ref = close

    history: list[float] = []
    for ts, rate in s.funding:
        if in_pos:
            margin_walk(ts)
            m = mark(ts)
            if m is not None:
                res.funding += rate * (m[1] / e_spot)  # short receives rate x notional at mark
            res.settlements_in += 1
        history.append(rate)
        sig = st.fmean(history[-n_sig:]) if n_sig else 0.0
        want_in = True if name == "always_on" else (sig >= entry_thr if not in_pos else sig >= exit_thr)
        if n_sig and len(history) < n_sig:
            want_in = False
        if want_in and not in_pos:
            m = mark(ts)
            if m is None:
                continue
            e_spot, e_perp = m
            ref = e_perp
            last_checked_hour = _hour_at_or_before(s.perp, ts)
            res.fees += rt_side
            res.switches += 1
            in_pos = True
        elif not want_in and in_pos:
            m = mark(ts)
            if m is None:
                continue
            sp, pp = m
            res.basis += (sp - e_spot) / e_spot - (pp - e_perp) / e_spot
            res.fees += rt_side
            in_pos = False
    if in_pos:
        m = mark(s.funding[-1][0])
        if m is not None:
            sp, pp = m
            res.basis += (sp - e_spot) / e_spot - (pp - e_perp) / e_spot
        res.fees += rt_side
    return res


def bingx_compare(pairs: list[str]) -> None:
    """Mean funding on BingX vs Binance over BingX's available recent history."""
    import ccxt

    bx = ccxt.bingx({"enableRateLimit": True, "timeout": 20000})
    print("\n=== BingX vs Binance funding (BingX's own recent history) ===", flush=True)
    for pair in pairs:
        try:
            rows = bx.fetch_funding_rate_history(f"{pair}:USDT", limit=1000)
        except Exception as exc:  # noqa: BLE001 — survey: report and continue
            print(f"  {pair:10s} bingx unavailable ({type(exc).__name__})", flush=True)
            continue
        if not rows:
            continue
        since = int(rows[0]["timestamp"])
        months = sorted(
            {(d.year, d.month) for d in (_dt.datetime.fromtimestamp(r["timestamp"] / 1000, _dt.UTC) for r in rows)}
        )
        bn = load_series(pair, months)
        bn_rates = [r for t, r in (bn.funding if bn else []) if t >= since]
        bx_rates = [float(r["fundingRate"]) for r in rows]
        days = (int(rows[-1]["timestamp"]) - since) / 86_400_000
        print(
            f"  {pair:10s} {days:5.0f}d  bingx mean {st.fmean(bx_rates) * 1e4:+.3f} bp/8h (n{len(bx_rates)})  "
            f"binance mean {st.fmean(bn_rates) * 1e4 if bn_rates else float('nan'):+.3f} bp/8h (n{len(bn_rates)})",
            flush=True,
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=None, help="comma list (BTC/USDT); default = the 34 SCALP_PAIRS")
    ap.add_argument("--lev", type=float, default=3.0, help="perp leg leverage (capital = Q x (1 + 1/L))")
    ap.add_argument("--fees", default="taker,maker")
    ap.add_argument("--bingx-compare", action="store_true", dest="bingx_compare")
    args = ap.parse_args()
    pairs = [p.strip() for p in args.pairs.split(",")] if args.pairs else list(SCALP_PAIRS)
    cap_mult = 1.0 + 1.0 / args.lev

    print(f"=== FUNDING CARRY (long spot + short perp) · perp {args.lev:g}x · capital = {cap_mult:.2f} x notional ===")
    for year, months in YEARS.items():
        series: dict[str, Series] = {}
        for pair in pairs:
            s = load_series(pair, months)
            if s is None or len(s.funding) < 300:
                print(f"  [{year}] {pair}: no/partial archive data — skipped", flush=True)
                continue
            series[pair] = s
        yrs = 365.0 / 365.0
        for fee_name in [f.strip() for f in args.fees.split(",")]:
            fee = FEES[fee_name]
            print(f"\n--- {year} · fees={fee_name} · {len(series)} coins ---", flush=True)
            print(
                f"  {'rule':20s} {'fund%':>7s} {'fees%':>6s} {'basis%':>7s} {'net%':>7s} {'APRcap':>7s} "
                f"{'time_in':>7s} {'sw/coin':>7s} {'rebal':>5s} {'liq':>4s} {'coins+':>7s}  worst / best coin",
                flush=True,
            )
            for rule in RULES:
                per = {p: simulate(s, rule, args.lev, fee) for p, s in series.items()}
                if not per:
                    continue
                nets = {p: r.net for p, r in per.items()}
                mean = lambda f: st.fmean(f(r) for r in per.values())  # noqa: E731
                worst = min(nets, key=nets.get)
                best = max(nets, key=nets.get)
                print(
                    f"  {rule[0]:20s} {mean(lambda r: r.funding) * 100:+7.2f} {mean(lambda r: r.fees) * 100:6.2f} "
                    f"{mean(lambda r: r.basis) * 100:+7.2f} {mean(lambda r: r.net) * 100:+7.2f} "
                    f"{mean(lambda r: r.net) / cap_mult / yrs * 100:+6.2f}% "
                    f"{mean(lambda r: r.settlements_in / max(r.settlements_all, 1)) * 100:6.0f}% "
                    f"{mean(lambda r: r.switches):7.1f} {sum(r.rebalances for r in per.values()):5d} "
                    f"{sum(r.liquidations for r in per.values()):4d} "
                    f"{sum(1 for v in nets.values() if v > 0):3d}/{len(nets):<3d}  "
                    f"{worst.split('/')[0]} {nets[worst] * 100:+.1f}% / {best.split('/')[0]} {nets[best] * 100:+.1f}%",
                    flush=True,
                )
    if args.bingx_compare:
        bingx_compare(pairs[:10])


if __name__ == "__main__":
    main()
