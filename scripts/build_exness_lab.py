#!/usr/bin/env python3
"""
Generate bots.json for the EXNESS real-underlying hyperparameter lab.

Migrates the fleet off crypto/gate onto Exness MT5 instruments that have a real
tangible underlying (commodities/metals/energy + BTC/ETH) — see
docs/EXNESS_INSTRUMENTS.md for the selection rationale. Same wave algorithm; the
new dimension is RISK HARDENING: every bot carries a max_loss_pct_per_trade cap
(signal/sizing.py cap_size_for_risk) so a single stop-out can no longer cost ~20%
of the bucket at high leverage.

Fleet = SYMBOLS × (3 entries × 4 exit modes) × {risk 0.02}.
The 4 exit modes A/B the two questions the strategy review raised — at ~40% win rate
the lever to profitability is reward:risk, not win rate (0.4·R > 0.6 ⇒ need R > 1.5):
    fixed-ATR  · trail-ATR  · fixed-PCT  · trail-PCT
where PCT = fixed-percent reward:risk TP/SL (tp_pct/sl_pct), clamped inside the
liquidation distance, and trail = ratcheting trailing-close. Every bot keeps the
max_loss_pct_per_trade=0.02 risk cap (mindful risk management).

One account serves all of it: the shared feed runs one poller per symbol (not per
bot). In ENV=dev fills are simulated, so the whole lab is paper on real data. In
ENV=prod, run ONE bot per symbol (a real account can't hold independent same-symbol
positions per bot).

bot_id: dev-{SYMBOL}-5m-{variant}-01
    4th segment ({variant}: ride/ride_t/ride_p/ride_pt/scalp/...) is the dashboard
    grouping token: bare=fixed-ATR, _t=trail-ATR, _p=fixed-PCT, _pt=trail-PCT.

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
    "USOIL",  # WTI crude   — energy, cleanest real-underlying + swap-free
    "UKOIL",  # Brent crude — energy
    "XNGUSD",  # natural gas — energy
    "XAUUSD",  # gold        — metal (sarf caveat), swap-free, high-leverage tier
    "XAGUSD",  # silver      — metal (sarf caveat), swap-free
    "XPTUSD",  # platinum    — industrial metal
    "XCUUSD",  # copper      — industrial metal
    "BTCUSD",  # bitcoin     — keeps the strategy's crypto tuning
    "ETHUSD",  # ethereum
]

# Three entry patterns; each crossed with four exit modes (12 variants/symbol).
# PCT reward:risk values are sized for the configured leverage so the stop sits
# inside the liquidation distance (detector also clamps as a backstop). R:R = 2:1.
_ENTRIES = [
    {
        "name": "ride",
        "patterns": ["wave_ride"],
        "atr": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.6},
        "pct": {"tp_pct": 0.05, "sl_pct": 0.025},  # 5% / 2.5% — the user's example
        "hold_fixed": 8,
        "hold_trail": 24,
        "trail": (1.0, 1.0),
    },
    {
        "name": "scalp",
        "patterns": ["vol_burst"],
        "atr": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0},
        "pct": {"tp_pct": 0.02, "sl_pct": 0.01},  # tight scalp R:R
        "hold_fixed": 3,
        "hold_trail": 8,
        "trail": (0.8, 0.5),
    },
    {
        "name": "flip",
        "patterns": ["wave_flip"],
        "atr": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0},
        "pct": {"tp_pct": 0.03, "sl_pct": 0.015},
        "hold_fixed": 4,
        "hold_trail": 8,
        "trail": (1.0, 0.8),
    },
]

# Single mindful risk cap across the whole fleet (was a 0.05/0.02 sweep; the freed
# dimension now A/B-tests the exit mode the review asked for).
_RISK_CAP = 0.02


def _variants_for(e: dict) -> list[dict]:
    """Expand one entry into its four exit-mode variants (fixed/trail × ATR/PCT)."""
    act, dist = e["trail"]
    return [
        {"suffix": "", "params": {**e["atr"], "max_hold_candles": e["hold_fixed"]}},
        {
            "suffix": "_t",
            "params": {
                **e["atr"],
                "max_hold_candles": e["hold_trail"],
                "trailing_enabled": True,
                "trail_activation_r": act,
                "trail_distance_r": dist,
            },
        },
        {"suffix": "_p", "params": {"tp_sl_pct_enabled": True, **e["pct"], "max_hold_candles": e["hold_fixed"]}},
        {
            "suffix": "_pt",
            "params": {
                "tp_sl_pct_enabled": True,
                **e["pct"],
                "max_hold_candles": e["hold_trail"],
                "trailing_enabled": True,
                "trail_activation_r": act,
                "trail_distance_r": dist,
            },
        },
    ]


def main() -> None:
    bots = []
    for symbol in SYMBOLS:
        for e in _ENTRIES:
            for v in _variants_for(e):
                name = f"{e['name']}{v['suffix']}"
                params = dict(v["params"])
                params["max_loss_pct_per_trade"] = _RISK_CAP
                bots.append(
                    {
                        "bot_id": f"dev-{symbol}-5m-{name}-01",
                        "pair": symbol,
                        "timeframe_entry": "5m",
                        "timeframe_regime": "15m",
                        "max_active_buckets": 1,
                        "strategy": name,
                        "patterns": e["patterns"],
                        "params": params,
                    }
                )

    out = os.path.join(os.path.dirname(__file__), "..", "bots.json")
    with open(out, "w") as f:
        json.dump(bots, f, indent=2)
    print(
        f"wrote {os.path.normpath(out)}: {len(bots)} bots = "
        f"{len(_ENTRIES)} entries × 4 exit modes (fixed/trail × ATR/PCT) × "
        f"{len(SYMBOLS)} symbols @ risk cap {_RISK_CAP}  "
        f"({len(SYMBOLS)} pollers via shared feed)"
    )


if __name__ == "__main__":
    main()
