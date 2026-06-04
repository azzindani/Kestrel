#!/usr/bin/env python3
"""
Generate bots.json for the WAVE strategy lab.

Fleet = 3 strategies × 3 sizing profiles × N markets. Each bot is one
(strategy, sizing-profile, pair) cell, so the lab evaluates the three wave
entries AND the three equity-scaled sizing profiles across many markets at once.

bot_id: dev-{PAIR}-5m-{strategy}_{sizing}-01   e.g. dev-BTCUSDT-5m-ride_bal-01
The 4th '-' segment (split_part(bot_id,'-',4) = e.g. ride_bal) is the token the
dashboard "Strategy Leaderboard" groups on — strategy + sizing profile.

Strategies (entry pattern + TP/SL/hold profile):
    ride   wave_ride  — ride a trend wave after a pullback; WIDE SL / FAR TP / LONG hold
    scalp  vol_burst  — trend entry only while volatility expands; tight, short hold
    flip   wave_flip  — fade an exhausted run (counter-trend); moderate

Sizing profiles (equity-scaled position sizing, signal/sizing.py):
    agg  aggressive  — big fraction, de-risk late/little
    bal  balanced    — the params.json defaults
    con  conservative— small fraction, de-risk early/hard

Run:  python3 scripts/build_wave_lab.py
"""

from __future__ import annotations

import json
import os

# Synthetic mock-feed markets (EXCHANGE=mock generates a per-pair random walk, so
# any ticker works; these are real liquid crypto names for a realistic dashboard).
WAVE_PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT",
    "ADA/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT", "TRX/USDT", "MATIC/USDT",
    "LTC/USDT", "BCH/USDT", "ATOM/USDT", "UNI/USDT", "APT/USDT", "ARB/USDT",
    "OP/USDT", "NEAR/USDT", "INJ/USDT", "SUI/USDT", "TON/USDT", "FIL/USDT",
    "PEPE/USDT", "HYPE/USDT",
]

# Entry strategies: pattern + TP/SL/hold param profile.
VARIANTS = [
    {"name": "ride", "patterns": ["wave_ride"],
     "params": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.6, "max_hold_candles": 8}},
    {"name": "scalp", "patterns": ["vol_burst"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 3}},
    {"name": "flip", "patterns": ["wave_flip"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 4}},
]

# Equity-scaled sizing profiles (override the params.json sizing defaults).
SIZING_PROFILES = [
    {"suffix": "agg", "params": {  # aggressive: bet big, de-risk late
        "size_fraction_full": 1.0, "size_fraction_half": 0.7,
        "drawdown_derisk_threshold": 0.30, "drawdown_derisk_factor": 0.7,
        "consec_loss_cooloff": 5, "consec_loss_factor": 0.7}},
    {"suffix": "bal", "params": {  # balanced: params.json defaults
        "size_fraction_full": 1.0, "size_fraction_half": 0.5,
        "drawdown_derisk_threshold": 0.20, "drawdown_derisk_factor": 0.5,
        "consec_loss_cooloff": 3, "consec_loss_factor": 0.5}},
    {"suffix": "con", "params": {  # conservative: bet small, de-risk early/hard
        "size_fraction_full": 0.5, "size_fraction_half": 0.3,
        "drawdown_derisk_threshold": 0.10, "drawdown_derisk_factor": 0.3,
        "consec_loss_cooloff": 2, "consec_loss_factor": 0.3}},
]


def main() -> None:
    bots = []
    for pair in WAVE_PAIRS:
        token_pair = pair.replace("/", "")
        for v in VARIANTS:
            for sz in SIZING_PROFILES:
                token = f"{v['name']}_{sz['suffix']}"
                bots.append({
                    "bot_id": f"dev-{token_pair}-5m-{token}-01",
                    "pair": pair,
                    "timeframe_entry": "5m",
                    "timeframe_regime": "15m",
                    "max_active_buckets": 1,
                    "strategy": token,
                    "patterns": v["patterns"],
                    "params": {**v["params"], **sz["params"]},
                })

    out = os.path.join(os.path.dirname(__file__), "..", "bots.json")
    with open(out, "w") as f:
        json.dump(bots, f, indent=2)
    print(f"wrote {os.path.normpath(out)}: {len(bots)} bots = "
          f"{len(VARIANTS)} strategies × {len(SIZING_PROFILES)} sizing profiles × "
          f"{len(WAVE_PAIRS)} markets")


if __name__ == "__main__":
    main()
