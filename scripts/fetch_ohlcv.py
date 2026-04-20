#!/usr/bin/env python3
"""
Fetch historical OHLCV from a public exchange (no API key required).
Writes candles to stdout as JSON lines consumed by backtest/QT cells.

Usage:
  python scripts/fetch_ohlcv.py [--pair BTC/USDT] [--tf 5m] [--days 90]
  python scripts/fetch_ohlcv.py --out /tmp/ohlcv.json
"""
import argparse, json, sys, time
import ccxt

EXCHANGES_ORDERED = ['bybit', 'okx', 'kucoin']


def _make_exchange(name: str) -> ccxt.Exchange:
    cls = getattr(ccxt, name)
    return cls({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})


def fetch(pair: str, timeframe: str, days: int) -> list[list]:
    since = int((time.time() - days * 86400) * 1000)
    last_error = None
    for name in EXCHANGES_ORDERED:
        ex = _make_exchange(name)
        try:
            ohlcvs: list[list] = []
            s = since
            print(f'Fetching {days}d {pair} {timeframe} via {name}...', file=sys.stderr)
            while True:
                batch = ex.fetch_ohlcv(pair, timeframe, since=s, limit=1000)
                if not batch:
                    break
                ohlcvs.extend(batch)
                s = batch[-1][0] + 1
                if len(batch) < 1000:
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
