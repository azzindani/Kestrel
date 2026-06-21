#!/usr/bin/env python3
"""
Generate bots.json for the HYPER-SCALP FLEET (owner directive 2026-06-20, CLAUDE.md v2.1).

PURPOSE: Kestrel is a high-frequency scalping fleet — *hundreds* of bots scalping a
fast timeframe (5m) across many liquid markets, maximizing trade ACTIVITY. This is
the design (CLAUDE.md §6/§13), not a research deviation. Earlier loop iterations
shrank the fleet to slow 1h/4h "to lose less"; that is explicitly the wrong default
here (the owner can trade slow by hand). The job is to find net-of-fee edge WITHIN
the active-scalping design, not to reduce activity.

FLEET: 7 patterns × 5m × 34 liquid USDT pairs = 238 bots. WS feeds are SHARED per
(pair, timeframe), so 238 bots ride only 34 streams — many bots on few streams is
cheap. The activity comes mainly from `trend_momentum` (permissive trend-follower,
fires on ~9% of candles) + `mom_adx`/`triple_mom` (self-directing momentum); the
four shape patterns add breadth and occasionally fire at 5m.

TRANSPORT: FEED_MODE=ws (docker-compose.override.yml) — WebSocket is the proven
real-time transport for LOW timeframes (5m pushes every rollover reliably). poll is
for high TF only. MAKER_EXECUTION=true is REQUIRED: scalping only clears the fee
floor on the maker path (~0.04% round trip vs ~0.18% taker).

HONEST CAVEAT (kept): more activity is NOT more profit — there is no proven net-of-
fee edge yet, and faster trading bleeds fees faster. This is PAPER (ENV=dev) until
§18. The fleet exists to hunt edge at scale + speed, with eyes open.

bot_id: dev-{PAIR}-5m-{pattern}-01   e.g. dev-SOLUSDT-5m-trend_momentum-01
split_part(bot_id,'-',4) = the pattern, the dashboard leaderboard key.

Run:  python3 scripts/build_momentum_lab.py
"""

from __future__ import annotations

import json
import os

# 34 liquid USDT spot pairs, all VERIFIED present + active on gate (the paper feed)
# 2026-06-20 (leveraged tokens like *5L/*3S excluded; TON dropped — not on gate).
SCALP_PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "HYPE/USDT",
    "PEPE/USDT",
    "LINK/USDT",
    "DOT/USDT",
    "ATOM/USDT",
    "NEAR/USDT",
    "LTC/USDT",
    "BCH/USDT",
    "TRX/USDT",
    "SUI/USDT",
    "FIL/USDT",
    "OP/USDT",
    "ARB/USDT",
    "APT/USDT",
    "INJ/USDT",
    "UNI/USDT",
    "GALA/USDT",
    "CHZ/USDT",
    "ETC/USDT",
    "APE/USDT",
    "XLM/USDT",
    "AAVE/USDT",
    "FET/USDT",
    "SEI/USDT",
    "TIA/USDT",
    "WLD/USDT",
]
# Back-compat alias (older tooling / memory references MOMENTUM_PAIRS).
MOMENTUM_PAIRS = SCALP_PAIRS

# SCALP exit/sizing profile — fast in, fast out (5m). WIDENED STOP (iter 13,
# 2026-06-20): the first run's 0.9-ATR stop sat INSIDE 5m noise and was the
# dominant bleed — over 145 closed trades: 52 stop-outs at −$0.18 avg (−$9.47)
# vs timeouts slightly POSITIVE (+$2.13) and take-profit NEVER hit (winners come
# from the trail). So widen the stop to let trades breathe and convert premature
# stops into trail/timeout survivors (also lifts win rate toward the owner's 70%):
#   tp 1.9 / sl 1.3 ATR  : R/R ~1.46 (>= risk Rule 3's 1.2); tp is rarely touched
#                          (the trail books the winners), but kept R/R-valid.
#   max_hold 4 (20 min)  : still a scalp — single-variable change is the stop bracket.
#   trail 0.5R@0.5R      : arm at +0.5R, trail 0.5R behind peak (the profit mechanism).
#   adx_strong_min 20    : LOW end — more mom_adx/triple_mom fires at 5m (activity).
#   volume_ratio_min 1.1 : floor — keep the volume gate near pass-through.
#   max_loss_pct 0.01    : per-trade equity-risk cap (wider stop ⇒ smaller notional).
# MAKER_EXECUTION=true (override.yml) is what makes scalps clear risk Rule 4's fee
# gate. HONEST: this RESHAPES variance / cuts the stop-out bleed — it does NOT create
# edge (the book is ~coin-flip); the no-edge reality is unchanged.
_EXIT = {
    "tp_atr_multiplier": 1.9,
    "sl_atr_multiplier": 1.3,
    "max_hold_candles": 4,
    "trailing_enabled": True,
    "trail_activation_r": 0.5,
    "trail_distance_r": 0.5,
    "volume_ratio_min": 1.1,
    "adx_strong_min": 20.0,
    "max_loss_pct_per_trade": 0.01,
}

# Scalp timeframe. 5m = the fast "like before" entry; the proven low-TF WS transport.
_TIMEFRAMES = ["5m"]

# Seven entry patterns. The ACTIVITY drivers are trend_momentum (permissive, ~9% of
# candles) + mom_adx/triple_mom (self-directing momentum). The four shape patterns
# add breadth across regimes (they fire more at 5m than at 1h/4h). 7 × 5m × 34 = 238.
STRATEGIES = [
    {"name": "trend_momentum", "patterns": ["trend_momentum"]},  # primary scalp driver
    {"name": "mom_adx", "patterns": ["mom_adx"]},  # momentum scalp
    {"name": "triple_mom", "patterns": ["triple_mom"]},  # momentum + vol scalp
    {"name": "impulse_retracement", "patterns": ["impulse_retracement"]},
    {"name": "compression_breakout", "patterns": ["compression_breakout"]},
    {"name": "anomaly_fade", "patterns": ["anomaly_fade"]},
    {"name": "wick_rejection", "patterns": ["wick_rejection"]},
]


# --- 1h MACD research-comparison cohort (iter-18 2026-06-21) -----------------------
# macd_cross (trend-aligned MACD signal cross) is the project's FIRST signal +EV in BOTH
# the recent year AND the untouched prior-year LOCKBOX (1h, maker; RESEARCH_LOOP iter 18).
# §13 permits high-TF ONLY as a research-comparison arm, so this runs at 1h ALONGSIDE — not
# replacing — the 5m hyper-scalp fleet (the 238 above are untouched). Live FORWARD-TEST of a
# modest lead, NOT a validated edge. Exit = the validated harness "tight" bracket
# (tp 1.4 / sl 1.0 ATR / max_hold 4); pairs = the 6 backtested.
_MACD_COHORT_PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT", "ADA/USDT"]
_MACD_EXIT = {
    "tp_atr_multiplier": 1.4,
    "sl_atr_multiplier": 1.0,
    "max_hold_candles": 4,
    "trailing_enabled": False,
    "volume_ratio_min": 1.1,
    "max_loss_pct_per_trade": 0.01,
}


def main() -> None:
    bots = []
    for pair in SCALP_PAIRS:
        token_pair = pair.replace("/", "")
        for tf in _TIMEFRAMES:
            for s in STRATEGIES:
                bots.append(
                    {
                        "bot_id": f"dev-{token_pair}-{tf}-{s['name']}-01",
                        "pair": pair,
                        "timeframe_entry": tf,
                        "timeframe_regime": tf,
                        "max_active_buckets": 1,
                        "strategy": s["name"],
                        "patterns": s["patterns"],
                        "params": dict(_EXIT),
                    }
                )

    n_scalp = len(bots)
    for pair in _MACD_COHORT_PAIRS:
        token_pair = pair.replace("/", "")
        bots.append(
            {
                "bot_id": f"dev-{token_pair}-1h-macd_cross-01",
                "pair": pair,
                "timeframe_entry": "1h",
                "timeframe_regime": "1h",
                "max_active_buckets": 1,
                "strategy": "macd_cross",
                "patterns": ["macd_cross"],
                "params": dict(_MACD_EXIT),
            }
        )

    out = os.path.join(os.path.dirname(__file__), "..", "bots.json")
    with open(out, "w") as f:
        json.dump(bots, f, indent=2)
    print(
        f"wrote {os.path.normpath(out)}: {len(bots)} bots = "
        f"{n_scalp} scalp ({len(STRATEGIES)} patterns × {len(_TIMEFRAMES)} tf × "
        f"{len(SCALP_PAIRS)} markets) + {len(_MACD_COHORT_PAIRS)} 1h macd_cross cohort"
    )


if __name__ == "__main__":
    main()
