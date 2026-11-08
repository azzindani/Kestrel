# Kestrel trailing-close A/B backtest — 20260604-055923 UTC

- **window:** 120d 5m · **leverage:** 20x · cost/trade ≈ 0.18%
- **pairs:** BTC/USDT, ETH/USDT, SOL/USDT, DOGE/USDT, PEPE/USDT, HYPE/USDT, XRP/USDT, BNB/USDT, ADA/USDT, AVAX/USDT
- **trailing beats fixed on:** 2 / 3 variants (OOS avg $/trade)

| variant | arm | n | win% | avg $ | net $ | expR | trail% | sl% | to% | IS→OOS |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| ride | fixed | 0 | 0.0 | 0.0000 | 0.000 | 0.00 | 0 | 0 | 0 | +0.0522 |
| ride | trail | 0 | 0.0 | 0.0000 | 0.000 | 0.00 | 0 | 0 | 0 | +0.0489 |
| scalp | fixed | 540 | 33.5 | -0.0388 | -20.927 | -0.32 | 0 | 48 | 24 | +0.0213 |
| scalp | trail | 549 | 39.2 | -0.0342 | -18.792 | -0.13 | 44 | 53 | 3 | +0.0127 |
| flip | fixed | 371 | 25.6 | -0.0380 | -14.089 | -0.39 | 0 | 40 | 43 | +0.0156 |
| flip | trail | 434 | 29.7 | -0.0355 | -15.407 | -0.34 | 27 | 51 | 23 | +0.0138 |

## Verdict

Trailing-close beat the fixed take-profit on **2/3** variants by out-of-sample average \$/trade. Trailing lets winners ride and exits on reversal; where it loses, the fixed TP banked more before the move retraced.
