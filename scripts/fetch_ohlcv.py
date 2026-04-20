#!/usr/bin/env python3
"""
Fetch historical OHLCV from a public exchange (no API key required).
Tries exchanges in order until one succeeds.

Usage:
  python scripts/fetch_ohlcv.py [--pair BTC/USDT] [--tf 5m] [--days 90]
  python scripts/fetch_ohlcv.py --out /tmp/ohlcv.json
"""
import argparse, json, sys, time
import ccxt

# Tried in order; first success wins.
# kucoin and kraken have no geo-restrictions from GCP/Colab.
EXCHANGES_ORDERED = ['kucoin', 'kraken', 'okx', 'bybit']

# Timeframe string → milliseconds (for "are we at the present?" check)
_TF_MS = {
    '1m': 60_000, '3m': 180_000, '5m': 300_000, '15m': 900_000,
    '30m': 1_800_000, '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000,
}


def _make_exchange(name: str) -> ccxt.Exchange:
    cls = getattr(ccxt, name)
    return cls({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})


def fetch(pair: str, timeframe: str, days: int) -> list[list]:
    since_ms = int((time.time() - days * 86400) * 1000)
    tf_ms = _TF_MS.get(timeframe, 300_000)
    last_error = None

    for name in EXCHANGES_ORDERED:
        ex = _make_exchange(name)
        try:
            ohlcvs: list[list] = []
            s = since_ms
            print(f'Fetching {days}d {pair} {timeframe} via {name}...', file=sys.stderr)
            while True:
                batch = ex.fetch_ohlcv(pair, timeframe, since=s, limit=1000)
                if not batch:
                    break
                ohlcvs.extend(batch)
                last_ts = batch[-1][0]
                s = last_ts + 1
                # Stop when the last candle is within 2 candle-periods of now.
                # This works regardless of how many candles each exchange returns
                # per page (Binance=1000, OKX/KuCoin=300, Kraken=720, etc.).
                now_ms = int(time.time() * 1000)
                if last_ts >= now_ms - 2 * tf_ms:
                    break
                print(f'  {len(ohlcvs):,}...', end='\r', file=sys.stderr)
            print(f'OK  {len(ohlcvs):,} candles', file=sys.stderr)
            return ohlcvs
        except Exception as exc:
            print(f'  {name} unavailable: {exc}', file=sys.stderr)
            last_error = exc
            continue

    raise RuntimeError(f'All exchanges failed. Last error: {last_error}')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--pair',  default='BTC/USDT')
    ap.add_argument('--tf',    default='5m')
    ap.add_argument('--days',  type=int, default=90)
    ap.add_argument('--out',   default=None, help='write JSON to file instead of stdout')
    args = ap.parse_args()

    ohlcvs = fetch(args.pair, args.tf, args.days)
    data = json.dumps(ohlcvs)
    if args.out:
        with open(args.out, 'w') as fh:
            fh.write(data)
        print(f'Written to {args.out}', file=sys.stderr)
    else:
        print(data)


if __name__ == '__main__':
    main()
