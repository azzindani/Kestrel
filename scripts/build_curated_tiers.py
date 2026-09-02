#!/usr/bin/env python3
"""Re-seat the lab and staging tiers from the iter-68 sim-parity ranking.

Owner directive 2026-09-02: "re arrange the lab and staging bots based on what
you learned from the data we had."

WHAT THE DATA SAID (reports/iter68/, algo_search --intrabar close --fees realistic
--funding 0.01, 5m, 45d recent + prior-year lockbox, 10 pairs):

  * Under sim-parity fills EVERY hiwin33 cell sits at 56-60% points-win in BOTH
    eras with gross capture within a few bps of zero. Nothing reaches the 65%
    staging bar; the 68-72% the tier was built on was the runner's tp_first fill
    artifact (docs/09 §4d). So this is a RANKING, not a promotion.
  * Cross-era points-win / gross bps, hiwin33 (recent | lockbox):
        sma_cross 50/100   60.0 +4.37 | 64.0 +0.97   gross-positive BOTH eras (only cell)
        triple_mom         57.9 -0.66 | 60.2 +0.78
        mom_adx            56.8 -0.89 | 60.0 +0.48
        macd_rsi           57.7 +2.95 | 59.6 -1.12
        macd_cross         58.3 +2.65 | 58.8 -1.52
        cci_mom            59.2 +1.70 | 56.9 -2.18
        bb_break           55.9 -1.06 | 58.5 -2.18   weakest — dropped from both tiers
        sma_cross 9/21     58.1 -2.37 | 57.4 -1.92   dropped (50/100 dominates it)
        sma_cross 10/30, 20/50, 20/100: lockbox 53-56% at -5..-6 bps — dropped
  * The 1h exp_* cells that filled staging (48 bots) are outside the minutes-only
    mandate (CLAUDE.md §13) and their 8-day live legs were not high-win
    (exp_hiwin_macd_rsi 44.7%, exp_hiwin_cci_mom 58.8% PF 0.98, n≈35). Removed.
  * Live 8-day hw33 fleet, win% by pair (all six entries pooled, ~300 trades/pair):
    CHZ 59.9, PEPE 58.5, TIA 58.2, AVAX 58.2, ADA 57.7, APT 57.7, BTC 57.8, AAVE 57.8,
    WLD 57.6, SUI 57.4 ... SEI 48.3, ATOM 49.6, FET 49.6, DOGE 49.8. Eight days is a
    weak ranking; it is used only to ORDER the lab's pair list, not to claim edge.

TIER DESIGN:
  staging (pre-prod, high-win DESIGN, liquid core): the six cross-era cells on the
    hiwin33 bracket, each on the sweep pairs where it was NOT gross-negative in
    both eras (pairs the lockbox could not fetch — ADA/AVAX/HYPE — count as
    unmeasured, kept only where the recent era was positive). 41 bots.
  lab (curated, broader): the same six cells x the twelve best live-win pairs,
    plus the owner's two hand-added lab bots kept verbatim. 74 bots.

HONEST STATUS (§6, once): none of this is edge. Every cell is net-negative in
dollars in both eras after realistic fees. The tiers now hold the cells that lose
LEAST and win MOST OFTEN under a fill model that matches the simulator, which is
the first time either tier has been seated on sim-accurate numbers.

Both files are REWRITTEN (not merged): the previous seating was built on the
refuted fill model. New bot_ids => backfill required (candles are per-bot_id);
both tiers run on gate (compose overrides .env.lab's EXCHANGE=mock).

    python3 scripts/build_curated_tiers.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_HIWIN33: dict[str, Any] = {
    "tp_atr_multiplier": 0.5,
    "sl_atr_multiplier": 1.5,
    "max_hold_candles": 6,
    "trailing_enabled": False,
    "volume_ratio_min": 1.1,
    "max_loss_pct_per_trade": 0.01,
}

# label -> (live pattern, extra per-bot params)
_CELLS: dict[str, tuple[str, dict[str, Any]]] = {
    "cur_sma50100": ("sma_cross", {"sma_cross_fast": 50, "sma_cross_slow": 100}),
    "cur_triple_mom": ("triple_mom", {}),
    "cur_mom_adx": ("mom_adx", {}),
    "cur_macd_rsi": ("macd_rsi", {}),
    "cur_macd_cross": ("macd_cross", {}),
    "cur_cci_mom": ("cci_mom", {}),
}

# staging: per cell, the sweep pairs not gross-negative in both eras (see docstring).
_STAGING_PAIRS: dict[str, list[str]] = {
    "cur_sma50100": ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "PEPE"],
    "cur_triple_mom": ["SOL", "PEPE", "ETH", "ADA"],
    "cur_mom_adx": ["SOL", "XRP", "PEPE", "ADA", "HYPE"],
    "cur_macd_rsi": ["ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "PEPE", "HYPE"],
    "cur_macd_cross": ["ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "PEPE", "HYPE"],
    "cur_cci_mom": ["BTC", "ETH", "XRP", "DOGE", "BNB", "ADA", "PEPE"],
}

# lab: the twelve best live-win pairs from the 8-day hw33 fleet, in that order.
_LAB_PAIRS: list[str] = ["CHZ", "PEPE", "TIA", "AVAX", "ADA", "APT", "BTC", "AAVE", "WLD", "SUI", "XLM", "OP"]

# Owner's hand-added lab bots (scripts/lab.py) — kept verbatim.
_LAB_KEEP_IDS = {"lab-BTCUSDT-5m-mom_adx-01", "lab-ETHUSDT-1h-macd_cross-01"}

_TF = "5m"


def _bot(env: str, sym: str, label: str) -> dict[str, Any]:
    pattern, extra = _CELLS[label]
    return {
        "bot_id": f"{env}-{sym}USDT-{_TF}-{label}-01",
        "pair": f"{sym}/USDT",
        "timeframe_entry": _TF,
        "timeframe_regime": _TF,
        "max_active_buckets": 1,
        "strategy": label,
        "patterns": [pattern],
        "params": {**_HIWIN33, **extra},
    }


def build_staging() -> list[dict[str, Any]]:
    return [_bot("staging", sym, label) for label, syms in _STAGING_PAIRS.items() for sym in syms]


def build_lab(existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = [b for b in existing if b["bot_id"] in _LAB_KEEP_IDS]
    return kept + [_bot("lab", sym, label) for label in _CELLS for sym in _LAB_PAIRS]


def _load(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return raw if isinstance(raw, list) else list(raw.get("bots", []))


def _save(path: str, bots: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bots, fh, indent=2)
        fh.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lab_path = os.path.join(_ROOT, "bots.lab.json")
    stg_path = os.path.join(_ROOT, "bots.staging.json")
    old_lab, old_stg = _load(lab_path), _load(stg_path)
    lab, stg = build_lab(old_lab), build_staging()
    new_ids = [b["bot_id"] for b in lab + stg if b["bot_id"] not in {x["bot_id"] for x in old_lab + old_stg}]

    print(f"staging: {len(old_stg)} -> {len(stg)}   lab: {len(old_lab)} -> {len(lab)}   new bot_ids: {len(new_ids)}")
    for label in _CELLS:
        print(f"  {label:16} staging={len(_STAGING_PAIRS[label]):2d}  lab={len(_LAB_PAIRS):2d}")
    if args.dry_run:
        return 0
    _save(lab_path, lab)
    _save(stg_path, stg)
    new_path = os.path.join(_ROOT, "reports", "iter68", "new_tier_bots.json")
    os.makedirs(os.path.dirname(new_path), exist_ok=True)
    _save(new_path, [b for b in lab + stg if b["bot_id"] in set(new_ids)])
    print(f"wrote {lab_path}, {stg_path}; backfill list -> {new_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
