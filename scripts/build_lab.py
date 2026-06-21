#!/usr/bin/env python3
"""
Generate bots.json for the massive parallel hyperparameter lab.

Every bot is one (pair × TP × SL × hold) cell of a grid, all running the
trend_momentum entry (the one pattern that actually fires on real 5m data).
This is parallel HP tuning: the dashboard "Strategy Leaderboard" groups by the
param token in bot_id, so you can read which cell is least-bad — with the usual
caveat that a winner only counts if it survives out-of-sample.

bot_id: dev-{PAIR}-5m-t{TP*10}s{SL*10}h{HOLD}-01   e.g. dev-BTCUSDT-5m-t16s10h8-01
Param token (split_part(bot_id,'-',4)) = t16s10h8  → TP 1.6 / SL 1.0 / hold 8.

NOTE: live HP search is SLOW (days to accumulate samples). For fast, rigorous
tuning use a backtest grid sweep instead (scripts/param_sweep.py).
Run:  python3 scripts/build_lab.py
"""

from __future__ import annotations

import json
import os

# 10 pairs — mix of large-cap and high-volatility (where ATR clears cost more often)
PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "DOGE/USDT",
    "PEPE/USDT",
    "HYPE/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "ADA/USDT",
    "AVAX/USDT",
]

# Grid over the params most able to flip profitability given the fee/edge math.
TP_GRID = [1.0, 1.6, 2.4, 3.0]  # take-profit ATR multiple
SL_GRID = [0.7, 1.0, 1.4]  # stop-loss ATR multiple
HOLD_GRID = [4, 8]  # max candles held


def _tok(tp: float, sl: float, hold: int) -> str:
    return f"t{int(round(tp * 10))}s{int(round(sl * 10))}h{hold}"


def main() -> None:
    bots = []
    for pair in PAIRS:
        token_pair = pair.replace("/", "")
        for tp in TP_GRID:
            for sl in SL_GRID:
                for hold in HOLD_GRID:
                    strat = _tok(tp, sl, hold)
                    bots.append(
                        {
                            "bot_id": f"dev-{token_pair}-5m-{strat}-01",
                            "pair": pair,
                            "timeframe_entry": "5m",
                            "timeframe_regime": "15m",
                            "max_active_buckets": 1,
                            "strategy": strat,
                            "patterns": ["trend_momentum"],
                            "params": {
                                "tp_atr_multiplier": tp,
                                "sl_atr_multiplier": sl,
                                "max_hold_candles": hold,
                            },
                        }
                    )

    out = os.path.join(os.path.dirname(__file__), "..", "bots.json")
    with open(out, "w") as f:
        json.dump(bots, f, indent=2)
    cells = len(TP_GRID) * len(SL_GRID) * len(HOLD_GRID)
    print(
        f"wrote {os.path.normpath(out)}: {len(bots)} bots = {cells} grid cells × {len(PAIRS)} pairs "
        f"(TP×SL×hold = {len(TP_GRID)}×{len(SL_GRID)}×{len(HOLD_GRID)})"
    )


if __name__ == "__main__":
    main()
