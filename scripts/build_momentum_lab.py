#!/usr/bin/env python3
"""
Generate bots.json for the DIVERSITY lab — many distinct hypotheses, not one idea.

WHY THIS CHANGED (2026-06-19, loop iter 6 — "40 bots not effective, we didn't
learn more"): the prior 40-bot fleet was 2 entry patterns (mom_adx, triple_mom)
× 2 TF × 10 pairs — i.e. WIDE on instruments but NARROW on ideas. Every bot tested
a variant of confluence momentum, which the research loop already refuted across
recent + lockbox. Spraying one refuted idea across more pairs (or, worse, the old
120 = the SAME 3 momentum patterns × 4 TF, half of them sub-cost-floor 5m/15m)
teaches nothing new — the bot_registry confirms those configs are already SEEN.

The fix for "learn more" is hypothesis DIVERSITY, not bot count. This fleet runs
SIX distinct entry patterns spanning all four regime buckets (§22), at the two
least-bad timeframes, across the 10 crypto pairs:

    mom_adx            trend momentum (ADX-gated streak)        — VALIDATED control
    triple_mom         trend momentum + expanding ATR           — VALIDATED control
    impulse_retracement trend pullback continuation (TRENDING)  — barely tested live
    compression_breakout volatility breakout (VOLATILE)         — NEVER deployed
    anomaly_fade       counter-trend mean-revert (VOLATILE/RANGING) — NEVER deployed
    wick_rejection     mean-reversion at support (RANGING)      — NEVER deployed

The bot_registry showed wick_rejection / compression_breakout / anomaly_fade have
NEVER run as live bots — they are genuinely untested hypotheses, so each adds new
information. The two momentum patterns are kept as VALIDATED CONTROLS (registry =
SEEN, intentionally) so the new patterns are measured against a known baseline on
the same pairs, TFs, and risk profile.

6 patterns × 2 timeframes × 10 markets = 120 bots.

UNIFORM exit/risk profile across all six (the risk-shaped bracket from iter 5) so
the comparison isolates each pattern's directional signal, not its exit tuning:
tp 2.4 / sl 1.5 ATR (R/R 1.6), max_hold 6, trail arms +0.5R / 0.5R behind peak,
volume_ratio_min at floor 1.1, max_loss_pct 0.01. Pattern-shape params
(wick_ratio_min, body_ratio_min, compression_factor, ...) come from params.json.
This is a DEV/PAPER forward-test for LEARNING — NOT a live deployment and NOT an
edge claim; the project still has no proven edge.

bot_id: dev-{PAIR}-{tf}-{pattern}-01   e.g. dev-ETHUSDT-4h-wick_rejection-01
split_part(bot_id,'-',4) = the pattern, the dashboard leaderboard key.

Run:  python3 scripts/build_momentum_lab.py
"""

from __future__ import annotations

import json
import os

# The 10 pairs the lab runs across (all exist on BingX, the live paper feed).
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

# RISK-SHAPED exit + sizing profile (2026-06-16 revision), applied UNIFORMLY to all
# six patterns so the lab compares directional signal quality on equal footing. The
# prior "medium + trailing" bracket ran 31.7% win with 54% of trades hitting a FULL
# 1-ATR stop and ~3.3x-equity notional; these values cut exposure and reshape the
# win/loss profile (they do NOT create edge — the book is ~coin-flip):
#   sl 1.0 -> 1.5 ATR  : 1 ATR sits inside the noise band (premature stop-outs);
#                        1.5 ATR lets price breathe.
#   tp 2.0 -> 2.4 ATR  : keep planned R/R = 1.6 (>= risk Rule 3's 1.2).
#   trail 1R@1R -> 0.5R@0.5R : a trade that goes +0.5R then reverses scratches near
#                        BE instead of riding back to a full stop.
#   max_loss 0.02 -> 0.01 : halves per-trade notional from ~3.3x to ~1.1x equity.
#   max_hold 12 -> 6   : mean-reversion decays in 3-5 bars; shorter exits win.
# NOTE: the single BIGGEST lever (leverage 20x -> ~5x) is .env/§4 human-gated and is
# NOT applied here. adx_strong_min is only read by mom_adx/triple_mom; it is a no-op
# for the other four patterns (harmless, kept for a uniform profile).
_EXIT = {
    "tp_atr_multiplier": 2.4,
    "sl_atr_multiplier": 1.5,
    "max_hold_candles": 6,
    "trailing_enabled": True,
    "trail_activation_r": 0.5,
    "trail_distance_r": 0.5,
    "volume_ratio_min": 1.1,  # floor — keep the prod volume gate near pass-through
    "adx_strong_min": 25.0,  # validated strong-trend threshold (mom_adx/triple_mom only)
    "max_loss_pct_per_trade": 0.01,  # per-trade equity-risk cap
}

# Least-bad timeframes (5m/15m pruned iter 5 — moves sit below the cost floor).
_TIMEFRAMES = ["1h", "4h"]

# Six distinct entry patterns spanning all four regime buckets (§22). The two
# momentum patterns are VALIDATED CONTROLS (bot_registry = SEEN); the other four are
# under/never-tested hypotheses (registry = NEW) and are why this fleet learns more.
STRATEGIES = [
    {"name": "mom_adx", "patterns": ["mom_adx"]},  # control (trend momentum)
    {"name": "triple_mom", "patterns": ["triple_mom"]},  # control (momentum + vol)
    {"name": "impulse_retracement", "patterns": ["impulse_retracement"]},  # TRENDING pullback
    {"name": "compression_breakout", "patterns": ["compression_breakout"]},  # VOLATILE breakout
    {"name": "anomaly_fade", "patterns": ["anomaly_fade"]},  # VOLATILE/RANGING fade
    {"name": "wick_rejection", "patterns": ["wick_rejection"]},  # RANGING mean-revert
]


def main() -> None:
    bots = []
    for pair in MOMENTUM_PAIRS:
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

    out = os.path.join(os.path.dirname(__file__), "..", "bots.json")
    with open(out, "w") as f:
        json.dump(bots, f, indent=2)
    print(
        f"wrote {os.path.normpath(out)}: {len(bots)} bots = "
        f"{len(STRATEGIES)} patterns × {len(_TIMEFRAMES)} timeframes "
        f"× {len(MOMENTUM_PAIRS)} markets"
    )


if __name__ == "__main__":
    main()
