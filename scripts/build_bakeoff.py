#!/usr/bin/env python3
"""
Generate bots.json for the multi-strategy bake-off.

Each bot runs the SAME pair under a DIFFERENT strategy so the dashboard's
"Strategy Leaderboard" / "PnL by Bot" panels can compare them head-to-head.
Strategy = (enabled patterns) + (params.json overrides). Because the detector
forces trend-alignment on every pattern, these are all trend-following variants;
true mean-reversion needs detector changes (tracked separately).

bot_id format: dev-{PAIR}-5m-{strategy}-01  (keeps the §6 env-pair-tf prefix;
the dashboard extracts the strategy token via split_part(bot_id,'-',4)).

Params overrides must use params.json keys and stay within their declared ranges.
Run:  python3 scripts/build_bakeoff.py
"""

from __future__ import annotations

import json
import os

PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "PEPE/USDT", "HYPE/USDT"]

# strategy -> {patterns: [...] | None (all),  params: {override-key: value}}
STRATEGIES: dict[str, dict] = {
    "mom": {"patterns": ["trend_momentum"], "params": {}},
    "momwide": {"patterns": ["trend_momentum"], "params": {"tp_atr_multiplier": 2.4, "sl_atr_multiplier": 1.0}},
    "momtight": {"patterns": ["trend_momentum"], "params": {"tp_atr_multiplier": 1.0, "sl_atr_multiplier": 0.8}},
    "mompatient": {"patterns": ["trend_momentum"], "params": {"max_hold_candles": 8}},
    "momhiconf": {"patterns": ["trend_momentum"], "params": {"min_confidence": 0.7, "body_ratio_min": 0.55}},
    "momhivol": {"patterns": ["trend_momentum"], "params": {"volume_ratio_min": 1.8}},
    "multi": {"patterns": None, "params": {}},  # all registered patterns
    "classic": {"patterns": ["impulse_retracement", "momentum_continuation"], "params": {}},
}


def main() -> None:
    bots = []
    for pair in PAIRS:
        token = pair.replace("/", "")
        for strat, spec in STRATEGIES.items():
            entry = {
                "bot_id": f"dev-{token}-5m-{strat}-01",
                "pair": pair,
                "timeframe_entry": "5m",
                "timeframe_regime": "15m",
                "max_active_buckets": 1,
                "strategy": strat,
            }
            if spec["patterns"] is not None:
                entry["patterns"] = spec["patterns"]
            if spec["params"]:
                entry["params"] = spec["params"]
            bots.append(entry)

    out = os.path.join(os.path.dirname(__file__), "..", "bots.json")
    with open(out, "w") as f:
        json.dump(bots, f, indent=2)
    print(f"wrote {os.path.normpath(out)}: {len(bots)} bots = {len(STRATEGIES)} strategies × {len(PAIRS)} pairs")


if __name__ == "__main__":
    main()
