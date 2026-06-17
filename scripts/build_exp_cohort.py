#!/usr/bin/env python3
"""
Merge an EXPERIMENTAL COHORT into bots.json without touching the baseline fleet.

The autonomous research loop (RESEARCH_LOOP.md) uses this to deploy its current
best candidate to a small slice of bots each iteration so the experiment is
visible live in Grafana — while the 120 baseline bots keep running unchanged.

How isolation works (no per-bot env / no schema change):
    - Every cohort bot carries a strategy label that starts with "exp_"
      (e.g. "exp_qual_mom_adx"). bot_id = dev-{PAIR}-{TF}-{strategy}-01, so
      split_part(bot_id,'-',4) = the exp_* label — the dashboard leaderboard /
      per-strategy / per-bot panels key on exactly that segment, so the cohort
      shows as its own rows ("watch them rise and fall").
    - Cohort bots stay env=dev, so they use the SAFE simulation execution path
      like every other dev bot. They never touch live execution.
    - This script REPLACES the existing cohort: it strips any bots.json entry
      whose strategy starts with "exp_", then appends the new cohort. Baseline
      entries (strategy NOT starting with exp_) are preserved verbatim.

Spec file (exp_candidate.json, rewritten by the loop each iteration):
    {
      "iteration": 1,
      "note": "free text",
      "arms": [
        {
          "label": "exp_qual",                       # becomes the strategy prefix
          "timeframe": "5m",
          "pairs": ["BTC/USDT", "ETH/USDT"],
          "strategies": [{"name": "mom_adx", "patterns": ["mom_adx"]}],
          "params": { "tp_atr_multiplier": 2.4, ... }  # params.json overrides
        }
      ]
    }

Run:  python3 scripts/build_exp_cohort.py [--spec exp_candidate.json]
"""

from __future__ import annotations

import argparse
import json
import os

# Allowlist of params.json override keys the cohort may set. Kept in sync with
# the Params dataclass by hand; an unknown key here is rejected BEFORE the daemon
# loads it (load_bot_configs would otherwise raise at startup and crash the fleet).
_VALID_PARAM_KEYS = {
    "ema_fast", "ema_slow", "rsi_low", "rsi_high", "rsi_long_max", "rsi_short_min",
    "volume_ratio_min", "tp_atr_multiplier", "sl_atr_multiplier", "min_confidence",
    "adx_trend_min", "adx_strong_min", "bb_width_threshold", "max_hold_candles",
    "max_active_buckets", "body_ratio_min", "wick_ratio_min", "compression_factor",
    "atr_quiet_multiplier", "atr_volatile_multiplier", "ema_spread_threshold",
    "trailing_enabled", "trail_activation_r", "trail_distance_r",
    "tp_sl_pct_enabled", "tp_pct", "sl_pct",
    "size_fraction_full", "size_fraction_half", "size_min_usdt",
    "drawdown_derisk_threshold", "drawdown_derisk_factor",
    "consec_loss_cooloff", "consec_loss_factor", "max_loss_pct_per_trade",
}

_HERE = os.path.dirname(__file__)
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))


def _cohort_bots(spec: dict) -> list[dict]:
    bots: list[dict] = []
    for arm in spec.get("arms", []):
        label = arm["label"]
        if not label.startswith("exp_"):
            raise ValueError(f"arm label must start with 'exp_': {label!r}")
        tf = arm["timeframe"]
        params = dict(arm.get("params", {}))
        bad = sorted(k for k in params if k not in _VALID_PARAM_KEYS)
        if bad:
            raise ValueError(f"arm {label!r} has unknown params keys: {', '.join(bad)}")
        for pair in arm["pairs"]:
            token = pair.replace("/", "")
            for s in arm["strategies"]:
                strategy = f"{label}_{s['name']}"  # e.g. exp_qual_mom_adx
                bots.append(
                    {
                        "bot_id": f"dev-{token}-{tf}-{strategy}-01",
                        "pair": pair,
                        "timeframe_entry": tf,
                        "timeframe_regime": tf,
                        "max_active_buckets": 1,
                        "strategy": strategy,
                        "patterns": list(s["patterns"]),
                        "params": dict(params),
                    }
                )
    return bots


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge an experimental cohort into bots.json.")
    ap.add_argument("--spec", default=os.path.join(_ROOT, "exp_candidate.json"))
    ap.add_argument("--bots", default=os.path.join(_ROOT, "bots.json"))
    args = ap.parse_args()

    with open(args.spec) as fh:
        spec = json.load(fh)
    with open(args.bots) as fh:
        existing = json.load(fh)

    baseline = [b for b in existing if not str(b.get("strategy", "")).startswith("exp_")]
    dropped = len(existing) - len(baseline)
    cohort = _cohort_bots(spec)

    merged = baseline + cohort
    with open(args.bots, "w") as fh:
        json.dump(merged, fh, indent=2)

    labels = sorted({b["strategy"] for b in cohort})
    print(
        f"wrote {os.path.normpath(args.bots)}: {len(merged)} bots "
        f"({len(baseline)} baseline + {len(cohort)} cohort; dropped {dropped} prior cohort)"
    )
    print(f"  cohort iteration={spec.get('iteration')} arms={labels}")


if __name__ == "__main__":
    main()
