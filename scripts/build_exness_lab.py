#!/usr/bin/env python3
"""
Generate bots.json for the EXNESS real-underlying hyperparameter lab.

Migrates the fleet off crypto/gate onto Exness MT5 instruments that have a real
tangible underlying (commodities/metals/energy + BTC/ETH) — see
docs/EXNESS_INSTRUMENTS.md for the selection rationale. Same wave algorithm; the
new dimension is RISK HARDENING: every bot carries a max_loss_pct_per_trade cap
(signal/sizing.py cap_size_for_risk) so a single stop-out can no longer cost ~20%
of the bucket at high leverage.

Fleet = SYMBOLS × (3 entries × fixed/trail) × {risk 0.05, risk 0.02}.
One Exness account serves all of it: the shared ExnessFeed runs one MetaApi poller
per symbol (not per bot). In ENV=dev fills are simulated, so the whole lab is paper
on real Exness data. In ENV=prod, run ONE bot per symbol (real account can't hold
independent same-symbol positions per bot).

bot_id: dev-{SYMBOL}-5m-{variant}-{NN}
    4th segment ({variant}: ride/ride_t/scalp/...) is the dashboard grouping token.
    instance NN encodes the risk cap: 01 = 0.05, 02 = 0.02.

Run:  python3 scripts/build_exness_lab.py
"""

from __future__ import annotations

import json
import os

# Exness MT5 symbols with a real tangible underlying (docs/EXNESS_INSTRUMENTS.md).
# Energy + industrial metals carry no bai'-al-sarf caveat; gold/silver do; BTC/ETH
# are MUI-conditional commodities. Forex pairs and indices are deliberately excluded
# (fiat / basket — no real underlying).
SYMBOLS = [
    "USOIL",   # WTI crude   — energy, cleanest real-underlying + swap-free
    "UKOIL",   # Brent crude — energy
    "XNGUSD",  # natural gas — energy
    "XAUUSD",  # gold        — metal (sarf caveat), swap-free, high-leverage tier
    "XAGUSD",  # silver      — metal (sarf caveat), swap-free
    "XPTUSD",  # platinum    — industrial metal
    "XCUUSD",  # copper      — industrial metal
    "BTCUSD",  # bitcoin     — keeps the strategy's crypto tuning
    "ETHUSD",  # ethereum
]

# Entry/exit variants — identical structure to the wave lab, now risk-capped.
_BASE_VARIANTS = [
    {"name": "ride", "patterns": ["wave_ride"],
     "params": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.6, "max_hold_candles": 8}},
    {"name": "scalp", "patterns": ["vol_burst"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 3}},
    {"name": "flip", "patterns": ["wave_flip"],
     "params": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0, "max_hold_candles": 4}},
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

# Risk-cap sweep: instance suffix → max_loss_pct_per_trade (the new tuning knob).
RISK_LEVELS = {"01": 0.05, "02": 0.02}


def main() -> None:
    bots = []
    for symbol in SYMBOLS:
        for v in _BASE_VARIANTS:
            for inst, risk in RISK_LEVELS.items():
                params = dict(v["params"])
                params["max_loss_pct_per_trade"] = risk
                bots.append({
                    "bot_id": f"dev-{symbol}-5m-{v['name']}-{inst}",
                    "pair": symbol,
                    "timeframe_entry": "5m",
                    "timeframe_regime": "15m",
                    "max_active_buckets": 1,
                    "strategy": v["name"],
                    "patterns": v["patterns"],
                    "params": params,
                })

    out = os.path.join(os.path.dirname(__file__), "..", "bots.json")
    with open(out, "w") as f:
        json.dump(bots, f, indent=2)
    print(
        f"wrote {os.path.normpath(out)}: {len(bots)} bots = "
        f"{len(_BASE_VARIANTS)} variants × {len(RISK_LEVELS)} risk levels × "
        f"{len(SYMBOLS)} symbols  ({len(SYMBOLS)} MetaApi pollers via shared feed)"
    )


if __name__ == "__main__":
    main()
