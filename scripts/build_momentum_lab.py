#!/usr/bin/env python3
"""
Generate bots.json for the CONFLUENCE-MOMENTUM lab (the validated 4h strategy).

Background: an exhaustive handwritten algorithm search (scripts/algo_search.py)
found that single-rule entries have no edge at any timeframe under the ~0.18%
round-trip cost, but multi-condition AND ("confluence") momentum at 4h is the
broadest positive result the project has produced. On a 365-day, 10-pair
walk-forward (60/40), `mom_adx` is net-positive on 10/10 pairs and clears the
§30 out-of-sample bar on ETH; `triple_mom` (the strictest variant) clears §30 on
ETH + DOGE. As a portfolio it still misses §30 on aggregate win-rate (~55%) and
on R/R for some pairs — so it does NOT meet §18 go-live criteria. This lab is a
DEV/PAPER forward-test of that candidate, not a live deployment.

Fleet = 2 entry strategies × N markets, each a (strategy, pair) cell:
    mom_adx     3-candle price streak inside a strong trend (ADX > adx_strong_min)
    triple_mom  + expanding ATR (strictest; streak + strong ADX + rising volatility)

Both are SELF_DIRECTING_PATTERNS: they take the streak direction without the
RSI/EMA trend gate (exactly how they were validated). They still pass volume
confirm, min-confidence, the QUIET-regime block, and all six risk rules.

Exit profile = the validated "tight" bracket (tp 1.4 ATR / sl 1.0 ATR, planned
R/R = 1.4 ≥ risk Rule 3's 1.2; max hold 4 candles). volume_ratio_min is set to
its floor (1.1) so the production volume gate stays close to the pass-through the
backtest used. max_loss_pct_per_trade keeps the per-trade risk cap on.

bot_id: dev-{PAIR}-4h-{strategy}-01   e.g. dev-ETHUSDT-4h-mom_adx-01
split_part(bot_id,'-',4) = mom_adx | triple_mom is the dashboard leaderboard key.

Run:  python3 scripts/build_momentum_lab.py
"""

from __future__ import annotations

import json
import os

# The 10 pairs the strategy was validated across (all exist on BingX, the live
# paper feed). BTC/ETH/SOL/BNB carry most of the edge; the rest are net-positive.
MOMENTUM_PAIRS = [
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

# "medium" exit + risk profile shared by both strategies. The 2026-06-14 exit
# sweep (scripts/algo_search.py --exits tight,medium,runner,wide,trail, 4h/365d/10
# pairs) found medium (tp 2.0 / sl 1.0 / hold 6) is the best confluence-momentum
# variant by expectancy: triple_mom/medium +$35.9 OOS, R/R 1.68 (vs tight +$27.8,
# R/R 1.28). Still sub-§30 (46.6% win < 55% — momentum is inherently low-win,
# high-R/R), so paper-only; this forward-tests the strongest variant found.
_EXIT = {
    "tp_atr_multiplier": 2.0,
    "sl_atr_multiplier": 1.0,
    "max_hold_candles": 6,
    "volume_ratio_min": 1.1,  # floor — keep the prod volume gate near pass-through
    "adx_strong_min": 25.0,  # the validated strong-trend entry threshold
    "max_loss_pct_per_trade": 0.02,
}

# Lab timeframe. The confluence-momentum edge was VALIDATED at 4h (see above) —
# 5m has NO validated edge (5m moves are smaller than the ~0.18% round-trip cost,
# the exact reason the search pushed to 4h). This is set to 5m by request for
# ACTIVITY/observation (many trades to watch the machine work), NOT for profit:
# expect negative expectancy here. Flip back to "4h" for the validated config.
_TIMEFRAME = "5m"

STRATEGIES = [
    {"name": "mom_adx", "patterns": ["mom_adx"]},
    {"name": "triple_mom", "patterns": ["triple_mom"]},
]


def main() -> None:
    bots = []
    for pair in MOMENTUM_PAIRS:
        token_pair = pair.replace("/", "")
        for s in STRATEGIES:
            bots.append(
                {
                    "bot_id": f"dev-{token_pair}-{_TIMEFRAME}-{s['name']}-01",
                    "pair": pair,
                    "timeframe_entry": _TIMEFRAME,
                    "timeframe_regime": _TIMEFRAME,
                    "max_active_buckets": 1,
                    "strategy": s["name"],
                    "patterns": s["patterns"],
                    "params": dict(_EXIT),
                }
            )

    out = os.path.join(os.path.dirname(__file__), "..", "bots.json")
    with open(out, "w") as f:
        json.dump(bots, f, indent=2)
    print(
        f"wrote {os.path.normpath(out)}: {len(bots)} bots = "
        f"{len(STRATEGIES)} strategies × {len(MOMENTUM_PAIRS)} markets ({_TIMEFRAME})"
    )


if __name__ == "__main__":
    main()
