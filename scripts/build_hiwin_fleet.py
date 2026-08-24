#!/usr/bin/env python3
"""Build the high-win-rate 5m fleet across the dev / lab / staging tiers.

Owner directive 2026-08-24: "i think we can afford more bots to 'dev', implement
high winrate to 'stage' and 'lab' ... the 'dev' environment will always expanding
because it for data collection ... the labs is for the sorted bots, the stage for
the ready bot before prod." Nothing goes to prod — no configuration has earned it.

WHY THESE ARMS. A 480-backtest sweep (scripts/algo_search.py, 5m, maker fees +
perp funding, 10 pairs) ran both bracket families. Under the conventional R/R>=1.2
brackets every cell landed at 38-44% win. Under the hiwin33 INVERTED bracket
(tp 0.5 / sl 1.5 ATR) six independent algorithms clustered at 68-72%:

    bb_break 71.8% (n=2544) · sma_cross 69.0% · macd_rsi 68.9%
    macd_cross 68.6% · mom_adx 68.1% · cci_mom 67.8%

That the same win rate appears across six unrelated entries says it is a property
of the GEOMETRY, not of any one signal — which is what makes it worth building a
tier around. It also answers the owner's "the winrate is still very bad": the live
fleet sits at ~34% because it runs the R/R>=1.2 brackets.

HONEST STATUS (§6, stated once). High win rate is NOT edge. At R/R 0.21 the
break-even win rate is ~82.6%, so 71.8% still loses ~$0.005/trade, and these were
+EV on 0/10 pairs by dollars. This fleet exists to forward-test the geometry that
actually reaches the owner's 70% target and to collect the data `dev` is for —
NOT because it is profitable. Nothing here is prod-eligible.

ALSO on-mandate: §13 requires minutes-candle hunting (1m-5m, hours explicitly not
a live cohort), and the fleet had drifted to 100% 1h. Every arm here is 5m.

STRICTLY ADDITIVE. Existing entries are never modified or removed and duplicate
bot_ids are skipped, so per the reset policy this deploy resets NOTHING and every
running forward-test keeps its history. New bot_ids still MUST be backfilled
(candles are per-bot_id) or the new bots start blind.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# hiwin33 bracket (docs/13-points-framework.md S1). Deliberately inverted:
# tp/sl = 0.5/1.5 = 0.33, which risk Rule 3 permits since the v2.6 floor is 0.25.
# Inverted geometry is the ONLY shape that can reach a 70% win rate — R/R >= 1.2
# caps win rate near 45-50% arithmetically.
_HIWIN33: dict[str, Any] = {
    "tp_atr_multiplier": 0.5,
    "sl_atr_multiplier": 1.5,
    "max_hold_candles": 6,
    "trailing_enabled": False,
    "volume_ratio_min": 1.1,
    "max_loss_pct_per_trade": 0.01,
}

# The conventional bracket (R/R 1.4). Used by the vwma_cross preset: that entry's
# measured edge lives HERE, not under the inverted geometry — the same signal ran
# -0.00 bps recent / -2.29 bps lockbox on hiwin33 versus +0.86 / +0.90 on tight.
# Pairing an entry with the wrong bracket throws the signal away.
_TIGHT: dict[str, Any] = {
    "tp_atr_multiplier": 1.4,
    "sl_atr_multiplier": 1.0,
    "max_hold_candles": 4,
    "trailing_enabled": False,
    "volume_ratio_min": 1.1,
    "max_loss_pct_per_trade": 0.01,
}

# Live-registered patterns ordered by measured 5m/hiwin33 win rate (descending).
_ARMS: list[str] = ["bb_break", "sma_cross", "macd_rsi", "macd_cross", "mom_adx", "cci_mom"]

# The most liquid pairs — used for the curated lab/staging tiers where breadth
# matters less than fill quality.
_CORE_PAIRS: list[str] = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "BNB/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "LTC/USDT",
]

_TF = "5m"
_LABEL_PREFIX = "hw33"


def make_bot(
    env: str,
    pair: str,
    arm: str,
    prefix: str = _LABEL_PREFIX,
    bracket: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Pure: one bot config for (env, pair, arm) on a 5m bracket.

    `prefix` is the strategy-label prefix (empty string = the bare arm name, the
    baseline-cohort convention); `bracket` defaults to hiwin33.

    bot_id is `{env}-{PAIR}-{tf}-{strategy}-01`; the dashboards key their
    per-strategy panels on split_part(bot_id,'-',4), so the strategy label must
    contain no dashes.
    """
    strategy = f"{prefix}_{arm}" if prefix else arm
    symbol = pair.replace("/", "")
    return {
        "bot_id": f"{env}-{symbol}-{_TF}-{strategy}-01",
        "pair": pair,
        "timeframe_entry": _TF,
        "timeframe_regime": _TF,
        "max_active_buckets": 1,
        "strategy": strategy,
        "patterns": [arm],
        "params": dict(bracket if bracket is not None else _HIWIN33),
    }


def build_tier(
    env: str,
    pairs: list[str],
    arms: list[str],
    prefix: str = _LABEL_PREFIX,
    bracket: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Pure: the full arm x pair grid for one tier."""
    return [make_bot(env, pair, arm, prefix, bracket) for arm in arms for pair in pairs]


def merge_additive(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Pure: append only bots whose bot_id is not already present.

    Returns (merged, added_count). Existing entries are returned untouched and in
    order — this is what makes the deploy reset-free.
    """
    seen = {b["bot_id"] for b in existing}
    added = [b for b in new if b["bot_id"] not in seen]
    return existing + added, len(added)


def _load(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        data = json.load(fh)
    return data["bots"] if isinstance(data, dict) else data


def _save(path: str, bots: list[dict[str, Any]], original: Any) -> None:
    """I/O shell: write back preserving the file's original top-level shape."""
    payload: Any = bots
    if isinstance(original, dict):
        payload = dict(original)
        payload["bots"] = bots
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def apply_tier(
    path: str,
    env: str,
    pairs: list[str],
    arms: list[str],
    dry_run: bool,
    prefix: str = _LABEL_PREFIX,
    bracket: Optional[dict[str, Any]] = None,
) -> tuple[int, int]:
    """I/O shell: merge one tier's grid into its bots file. Returns (added, total)."""
    original: Any = []
    if os.path.exists(path):
        with open(path) as fh:
            original = json.load(fh)
    existing = _load(path)
    merged, added = merge_additive(existing, build_tier(env, pairs, arms, prefix, bracket))
    if not dry_run:
        _save(path, merged, original)
    return added, len(merged)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report counts without writing")
    ap.add_argument(
        "--dev-pairs",
        default="all",
        help="'all' for the full pair universe already in bots.json, or a comma list",
    )
    ap.add_argument(
        "--preset",
        default="hiwin33",
        choices=["hiwin33", "vwma_cross"],
        help="hiwin33: the 6-arm high-win grid across dev/lab/staging (default). "
        "vwma_cross: the volume-weighted entry on the TIGHT bracket, DEV ONLY — its "
        "43-46%% win rate bars it from lab/staging under the owner's high-win tier rule.",
    )
    args = ap.parse_args()

    dev_path = os.path.join(_ROOT, "bots.json")
    if args.dev_pairs == "all":
        dev_pairs = sorted({b["pair"] for b in _load(dev_path)}) or _CORE_PAIRS
    else:
        dev_pairs = [p.strip() for p in args.dev_pairs.split(",") if p.strip()]

    if args.preset == "vwma_cross":
        # DEV ONLY, deliberately. vwma_cross wins 43-46% of its trades, so the
        # owner's tier rule (staging/lab = high-win DESIGN only) excludes it from
        # both curated tiers however stable its gross signal is. It also takes the
        # TIGHT bracket, not hiwin33 — see _TIGHT.
        plan = [(dev_path, "dev", dev_pairs, ["vwma_cross"], "", _TIGHT)]
    else:
        plan = [
            # dev: the full grid — the always-expanding data-collection tier.
            (dev_path, "dev", dev_pairs, _ARMS, _LABEL_PREFIX, _HIWIN33),
            # lab: the sorted tier — every high-win arm, but only the liquid pairs.
            (os.path.join(_ROOT, "bots.lab.json"), "lab", _CORE_PAIRS, _ARMS, _LABEL_PREFIX, _HIWIN33),
            # staging: pre-prod candidates — the top three arms by measured win rate.
            (
                os.path.join(_ROOT, "bots.staging.json"),
                "staging",
                _CORE_PAIRS[:8],
                _ARMS[:3],
                _LABEL_PREFIX,
                _HIWIN33,
            ),
        ]

    report: dict[str, dict[str, int]] = {}
    for path, env, pairs, arms, prefix, bracket in plan:
        added, total = apply_tier(path, env, pairs, arms, args.dry_run, prefix, bracket)
        report[env] = {"added": added, "tier_total": total, "pairs": len(pairs), "arms": len(arms)}

    # This is a build tool, not a daemon: its result goes to stdout for the operator
    # (the §3 no-print rule governs the trading daemon's logging path, which is the
    # events table). Kept to a single machine-readable line.
    print(json.dumps({"preset": args.preset, "dry_run": args.dry_run, "tiers": report}, indent=2))


if __name__ == "__main__":
    main()
