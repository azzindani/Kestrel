#!/usr/bin/env python3
"""
Generate bots.json for the WAVE strategy lab — replaces the dead TP×SL×hold grid.

Three strategy variants (the "surf the wave, flip when wrong" family), each run on
every pair as its own bot. Far fewer bots than the 240-cell HP grid (3 × 10 = 30),
so the live bleed surface is small while the three approaches accumulate real data.

Each variant = one registered pattern + a TP/SL/hold param profile. The bot_id's
4th '-' segment is the strategy token the dashboard "Strategy Leaderboard" groups on
(split_part(bot_id,'-',4)), e.g. dev-BTCUSDT-5m-ride-01 → "ride".

    ride   wave_ride  — ride a trend wave after a pullback; WIDE SL / FAR TP / LONG hold
    scalp  vol_burst  — trend entry only while volatility expands; tight, short hold
    flip   wave_flip  — fade an exhausted run (counter-trend); moderate

Run:  python3 scripts/build_wave_lab.py
"""

from __future__ import annotations

import json
import os

import build_lab as lab  # reuse the PAIRS list — single source of truth

VARIANTS = [
    {"name": "ride", "patterns": ["wave_ride"],
     "params": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.6, "max_hold_candles": 8}},
    {"name": "scalp", "patterns": ["vol_burst"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 3}},
    {"name": "flip", "patterns": ["wave_flip"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 4}},
]


def main() -> None:
    bots = []
    for pair in lab.PAIRS:
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
          f"{len(VARIANTS)} variants × {len(lab.PAIRS)} pairs "
          f"({', '.join(v['name'] for v in VARIANTS)})")


if __name__ == "__main__":
    main()
