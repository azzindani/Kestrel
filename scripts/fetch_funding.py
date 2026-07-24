"""Perp funding-rate history fetcher (iter 67 — owner idea #3, funding tilt).

Fetches historical 8h funding rates for USDT perpetuals from Binance's PUBLIC
REST endpoint (the only surveyed venue whose funding history reaches the full
2-year window the lockbox era needs — gate caps at 180d, okx ~3mo, bingx ~2mo;
probe 2026-07-24). REST from the dev host works; the geo-block in the VPS memo
applies to live trading websockets, and Binance here is a DATA source only.

Cached per pair under reports/funding/ (gitignored) so repeated sweeps in one
research session don't refetch. Cache is refreshed when its coverage no longer
spans the requested window.

Importable API:
    load_funding_map(pair, days, offset_days) -> list[(ts_ms, rate_frac)]
        sorted ascending; rate_frac is the raw exchange fraction
        (0.0001 == 0.01%/8h). Empty list => no data (caller degrades).

CLI (prefetch/inspect):
    python3 scripts/fetch_funding.py --pairs BTC/USDT,ETH/USDT --days 365 --offset-days 365
"""

import argparse
import json
import os
import time

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "reports", "funding")
_DAY_MS = 86_400_000
_PAGE_LIMIT = 200


def _cache_path(pair: str) -> str:
    return os.path.join(_CACHE_DIR, pair.replace("/", "_") + ".json")


def _fetch_range(pair: str, since_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Paginate Binance funding history for one pair over [since_ms, end_ms]."""
    import ccxt

    ex = ccxt.binance({"enableRateLimit": True, "timeout": 20000})
    symbol = f"{pair}:USDT"
    out: list[tuple[int, float]] = []
    cursor = since_ms
    while cursor < end_ms:
        rows = ex.fetch_funding_rate_history(symbol, since=cursor, limit=_PAGE_LIMIT)
        if not rows:
            break
        for r in rows:
            ts = int(r["timestamp"])
            if ts > end_ms:
                break
            out.append((ts, float(r["fundingRate"])))
        last_ts = int(rows[-1]["timestamp"])
        if last_ts <= cursor:
            break
        cursor = last_ts + 1
    return out


def load_funding_map(pair: str, days: int, offset_days: int = 0) -> list[tuple[int, float]]:
    """Funding events covering the requested window, ascending. [] if unavailable."""
    now = int(time.time() * 1000)
    end_ms = now - offset_days * _DAY_MS
    since_ms = end_ms - days * _DAY_MS

    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = _cache_path(pair)
    if os.path.exists(path):
        with open(path) as fh:
            cached = [(int(t), float(r)) for t, r in json.load(fh)]
        # A cache whose span covers the window (with one funding-interval slack
        # at each edge) is fresh enough for research sweeps.
        if cached and cached[0][0] <= since_ms + 8 * 3_600_000 and cached[-1][0] >= end_ms - 8 * 3_600_000:
            return [(t, r) for t, r in cached if since_ms <= t <= end_ms]

    # Binance lists sub-cent memecoins as 1000-multiplied perps (PEPE/USDT ->
    # 1000PEPE/USDT). Funding reflects the same asset's positioning, so the
    # multiplied contract's rates are valid here. Unknown symbols raise BadSymbol,
    # so each candidate gets its own attempt.
    base, _, quote = pair.partition("/")
    full: list[tuple[int, float]] = []
    for candidate in (pair, f"1000{base}/{quote}"):
        try:
            # Fetch the widest window any era needs (recent + lockbox = 2y)
            # once, so both era sweeps hit the same cache file.
            full = _fetch_range(candidate, now - 730 * _DAY_MS, now)
        except Exception:  # noqa: BLE001 — data source degrade, try next candidate
            full = []
        if full:
            break
    if not full:
        return []
    if full:
        with open(path, "w") as fh:
            json.dump(full, fh)
    return [(t, r) for t, r in full if since_ms <= t <= end_ms]


def rate_at(events: list[tuple[int, float]], ts: int) -> float | None:
    """Most recent funding rate at or before ts (binary search). None if before history."""
    lo, hi = 0, len(events)
    while lo < hi:
        mid = (lo + hi) // 2
        if events[mid][0] <= ts:
            lo = mid + 1
        else:
            hi = mid
    return events[lo - 1][1] if lo else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True, help="comma list, slash format (BTC/USDT)")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--offset-days", type=int, default=0, dest="offset_days")
    args = ap.parse_args()

    for pair in [p.strip() for p in args.pairs.split(",")]:
        ev = load_funding_map(pair, args.days, args.offset_days)
        if not ev:
            print(f"{pair:12s} NO DATA")
            continue
        rates_pct = [r * 100.0 for _, r in ev]
        pos = sum(1 for r in rates_pct if r > 0)
        mean = sum(rates_pct) / len(rates_pct)
        mx, mn = max(rates_pct), min(rates_pct)
        print(
            f"{pair:12s} n={len(ev):5d}  mean={mean:+.4f}%/8h  pos={100 * pos / len(ev):5.1f}%  "
            f"range=[{mn:+.4f}, {mx:+.4f}]"
        )


if __name__ == "__main__":
    main()
