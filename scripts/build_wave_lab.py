#!/usr/bin/env python3
"""
Generate bots.json for the WAVE strategy lab — fixed-exit vs trailing-close A/B.

Fleet = 3 entry strategies × 2 exit policies × N markets. Each bot is one
(strategy, exit-policy, pair) cell, so the lab evaluates the three wave entries
AND fixed-TP vs trailing-close side by side across many markets at once. Running
both flavours of every strategy also doubles the fleet's concurrent open-position
count — the in-architecture way to "scale by number of open positions" (each bot
holds one position per pair; more positions = more bots).

bot_id: dev-{PAIR}-5m-{strategy}-01   e.g. dev-BTCUSDT-5m-ride_t-01
The 4th '-' segment (split_part(bot_id,'-',4) = e.g. ride / ride_t) is the token
the dashboard "Strategy Leaderboard" groups on — strategy + exit policy.

Entry strategies (registered pattern + TP/SL/hold profile):
    ride   wave_ride  — ride a trend wave after a pullback; WIDE SL / FAR TP / LONG hold
    scalp  vol_burst  — trend entry only while volatility expands; tight, short hold
    flip   wave_flip  — fade an exhausted run (counter-trend); moderate

Exit policies:
    (fixed)  fixed take-profit + fixed stop  — baseline
    _t       trailing-close — drop the fixed TP, ratchet the stop toward price so
             winners ride and exit on reversal (signal/sizing.py compounding pairs
             with this to grow the bucket). NOTE: the fixed TP still gates ENTRY via
             risk Rule 3 (planned R/R = tp/sl ≥ 1.2), so trailing variants keep a
             valid tp_atr_multiplier even though it is unused at exit.

Run:  python3 scripts/build_wave_lab.py
"""

from __future__ import annotations

import json
import os

# Synthetic mock-feed markets (EXCHANGE=mock generates a per-pair random walk, so
# any ticker works; these are real liquid crypto names for a realistic dashboard).
WAVE_PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "PEPE/USDT",
    "HYPE/USDT", "XRP/USDT", "BNB/USDT", "ADA/USDT", "AVAX/USDT",
]

# Each variant: entry pattern + exit param profile. Trailing variants set
# trailing_enabled and widen max_hold_candles so a runner isn't cut short.
VARIANTS = [
    # --- fixed-exit baselines ---
    {"name": "ride", "patterns": ["wave_ride"],
     "params": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.6, "max_hold_candles": 8}},
    {"name": "scalp", "patterns": ["vol_burst"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 3}},
    {"name": "flip", "patterns": ["wave_flip"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 4}},
    # --- trailing-close variants (same entries, exit rides the trail) ---
    {"name": "ride_t", "patterns": ["wave_ride"],
     "params": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.6, "max_hold_candles": 24,
                "trailing_enabled": True, "trail_activation_r": 1.0, "trail_distance_r": 1.0}},
    {"name": "scalp_t", "patterns": ["vol_burst"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 8,
                "trailing_enabled": True, "trail_activation_r": 0.8, "trail_distance_r": 0.5}},
    {"name": "flip_t", "patterns": ["wave_flip"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 8,
                "trailing_enabled": True, "trail_activation_r": 1.0, "trail_distance_r": 0.8}},
]


def main() -> None:
    bots = []
    for pair in WAVE_PAIRS:
        token_pair = pair.replace("/", "")
        for v in VARIANTS:
            bots.append({
                "bot_id": f"dev-{token_pair}-5m-{v['name']}-01",
                "pair": pair,
                "timeframe_entry": "5m",
                "timeframe_regime": "15m",
                "max_active_buckets": 1,
                "strategy": v["name"],
                "patterns": v["patterns"],
                "params": v["params"],
            })

    out = os.path.join(os.path.dirname(__file__), "..", "bots.json")
    with open(out, "w") as f:
        json.dump(bots, f, indent=2)
    print(f"wrote {os.path.normpath(out)}: {len(bots)} bots = "
          f"{len(VARIANTS)} variants × {len(WAVE_PAIRS)} markets")


if __name__ == "__main__":
    main()
