#!/usr/bin/env python3
"""
Generate bots.json for the CRYPTO exit-mode lab on the live `gate` feed.

Why this (not build_exness_lab.py): the broker decision is deferred and the only
working live feed on this VPS is `gate` (docker-compose.override.yml), which serves
crypto pairs — it cannot provide the Exness real-underlying symbols (USOIL/XAUUSD/…).
Deploying the Exness lab onto the gate feed would starve 7/9 symbols. So we forward-
test the *refined algorithm* on the crypto pairs that actually have data; the Exness
instrument set (build_exness_lab.py + docs/EXNESS_INSTRUMENTS.md) stays ready for the
moment a real-underlying feed (BingX/Exness) is connected.

Fleet = SYMBOLS × (3 entries × 4 exit modes). The 4 exit modes A/B the strategy-review
question — at ~40% win the lever to profit is reward:risk, not win rate (0.4·R > 0.6 ⇒
R > 1.5):
    fixed-ATR (bare) · trail-ATR (_t) · fixed-PCT (_p) · trail-PCT (_pt)
PCT = fixed-percent reward:risk TP/SL (tp_pct/sl_pct), clamped inside the liquidation
distance by signal/detector.py. Every bot keeps max_loss_pct_per_trade=0.02.

ENV=dev ⇒ SimulationExecution (paper) on real gate candles. Shared feed = one poller
per pair (not per bot). bot_id = dev-{SYM}-5m-{variant}-01; the 4th segment is the
dashboard grouping token.

Run:  python3 scripts/build_crypto_lab.py
"""

from __future__ import annotations

import json
import os

# Gate (ccxt) USDT pairs with full warm candle history on this VPS (verified in the
# candles table). High-liquidity majors + a couple of high-vol alts (PEPE/HYPE) that
# are likeliest to clear the fee-viability gate.
SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "DOGE/USDT",
    "PEPE/USDT",
    "HYPE/USDT",
]

# Three entry patterns; each crossed with four exit modes (12 variants/symbol).
# PCT reward:risk = 2:1, sized for 20x so the stop sits inside the ~4.5% liquidation
# distance (detector also clamps to 0.7/leverage as a backstop).
_ENTRIES = [
    {
        "name": "ride",
        "patterns": ["wave_ride"],
        "atr": {"tp_atr_multiplier": 3.0, "sl_atr_multiplier": 1.6},
        "pct": {"tp_pct": 0.05, "sl_pct": 0.025},
        "hold_fixed": 8,
        "hold_trail": 24,
        "trail": (1.0, 1.0),
    },
    {
        "name": "scalp",
        "patterns": ["vol_burst"],
        "atr": {"tp_atr_multiplier": 1.6, "sl_atr_multiplier": 1.0},
        "pct": {"tp_pct": 0.02, "sl_pct": 0.01},
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

# Single mindful risk cap across the fleet (fraction of bucket equity per stop-out).
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
        {
            "suffix": "_p",
            "params": {"tp_sl_pct_enabled": True, **e["pct"], "max_hold_candles": e["hold_fixed"]},
        },
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


def _bot_token(symbol: str) -> str:
    """dev-{TOKEN}-5m-... — strip the quote so bot_id stays single-token per segment."""
    return symbol.split("/")[0]


def main() -> None:
    bots = []
    for symbol in SYMBOLS:
        token = _bot_token(symbol)
        for e in _ENTRIES:
            for v in _variants_for(e):
                name = f"{e['name']}{v['suffix']}"
                params = dict(v["params"])
                params["max_loss_pct_per_trade"] = _RISK_CAP
                bots.append(
                    {
                        "bot_id": f"dev-{token}-5m-{name}-01",
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
        f"{len(SYMBOLS)} crypto pairs @ risk cap {_RISK_CAP}  "
        f"({len(SYMBOLS)} pollers via shared gate feed)"
    )


if __name__ == "__main__":
    main()
