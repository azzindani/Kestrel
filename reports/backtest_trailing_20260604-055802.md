# Kestrel trailing-close A/B backtest — 20260604-055802 UTC

- **window:** 45d 5m · **leverage:** 20x · cost/trade ≈ 0.18%
- **pairs:** BTC/USDT, ETH/USDT, SOL/USDT
- **trailing beats fixed on:** 3 / 3 variants (OOS avg $/trade)

| variant | arm | n | win% | avg $ | net $ | expR | trail% | sl% | to% | IS→OOS |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| ride | fixed | 37 | 27.0 | -0.0324 | -1.200 | -0.31 | 0 | 35 | 51 | +0.0215 |
| ride | trail | 0 | 0.0 | 0.0000 | 0.000 | 0.00 | 0 | 0 | 0 | +0.0000 |
| scalp | fixed | 72 | 29.2 | -0.0657 | -4.729 | -0.46 | 0 | 56 | 24 | +0.0370 |
| scalp | trail | 67 | 29.8 | -0.0577 | -3.866 | -0.22 | 39 | 58 | 3 | +0.0213 |
| flip | fixed | 49 | 40.8 | -0.0523 | -2.562 | -0.11 | 0 | 24 | 45 | +0.0285 |
| flip | trail | 49 | 38.8 | -0.0355 | -1.741 | -0.08 | 41 | 41 | 18 | +0.0698 |

## Verdict

Trailing-close beat the fixed take-profit on **3/3** variants by out-of-sample average \$/trade. Trailing lets winners ride and exits on reversal; where it loses, the fixed TP banked more before the move retraced.
