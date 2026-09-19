#!/usr/bin/env python3
"""Append a forward-test cohort to bots.json WITHOUT touching any existing bot.

build_exp_cohort.py replaces every exp_* cohort; this is the additive counterpart the
owner's tier model calls for (dev = always-expanding tester). One cohort = one live
pattern x one bracket x a pair list, labelled so split_part(bot_id,'-',4) is the
cohort (the dashboards key on that segment).

It refuses to add a bot_id that already exists, runs both deploy guards
(retired_ledger.py check + bot_registry.py check) on the new bots only, and writes the
new bots to --new-out so ONLY they get backfilled — backfilling existing bot_ids
causes a one-off mass-entry burst on restart (RESEARCH_LOOP reset-policy notes).

Run:
  python3 scripts/add_dev_cohort.py --label vr_tight --pattern vr_adaptive \
      --bracket tight --new-out reports/iter71/new_bots.json [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_momentum_lab import SCALP_PAIRS  # noqa: E402 — research harness import path

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Same bracket presets as algo_search.EXITS, so a deployed cohort is exactly the cell
# that was backtested.
BRACKETS = {
    "hiwin33": {"tp_atr_multiplier": 0.5, "sl_atr_multiplier": 1.5, "max_hold_candles": 6},
    "tight": {"tp_atr_multiplier": 1.4, "sl_atr_multiplier": 1.0, "max_hold_candles": 4},
    "medium": {"tp_atr_multiplier": 2.0, "sl_atr_multiplier": 1.0, "max_hold_candles": 6},
}
# Fleet-wide per-bot defaults every current 5m dev cohort carries.
_BASE_PARAMS = {"trailing_enabled": False, "volume_ratio_min": 1.1, "max_loss_pct_per_trade": 0.01}


def parse_overrides(items: list[str], contract: dict) -> dict:
    """--param key=value pairs, type-cast and range-checked against params.json (§25)."""
    out: dict = {}
    for item in items:
        key, sep, raw = item.partition("=")
        if not sep or key not in contract:
            raise SystemExit(f"--param {item!r}: expected key=value with a key defined in params.json")
        spec = contract[key]
        kind = spec["type"]
        value = int(raw) if kind == "int" else float(raw) if kind == "float" else raw.lower() == "true"
        lo, hi = spec["range"]
        if kind in ("int", "float") and not (lo <= value <= hi):
            raise SystemExit(f"--param {key}={value} is outside its params.json range [{lo}, {hi}]")
        out[key] = value
    return out


def cohort(
    label: str, pattern: str, bracket: str, pairs: list[str], timeframe: str, overrides: dict | None = None
) -> list[dict]:
    params = {**BRACKETS[bracket], **_BASE_PARAMS, **(overrides or {})}
    return [
        {
            "bot_id": f"dev-{pair.replace('/', '')}-{timeframe}-{label}-01",
            "pair": pair,
            "timeframe_entry": timeframe,
            "timeframe_regime": timeframe,
            "max_active_buckets": 1,
            "strategy": label,
            "patterns": [pattern],
            "params": dict(params),
        }
        for pair in pairs
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="cohort label (bot_id segment 4); no '-' allowed")
    ap.add_argument("--pattern", required=True, help="registered live pattern name")
    ap.add_argument("--bracket", required=True, choices=sorted(BRACKETS))
    ap.add_argument("--timeframe", default="5m")
    ap.add_argument("--pairs", default=None, help="comma list (BTC/USDT); default = the 34 SCALP_PAIRS")
    ap.add_argument("--bots", default=os.path.join(_ROOT, "bots.json"))
    ap.add_argument("--new-out", required=True, dest="new_out", help="append the new bots here (for backfill)")
    ap.add_argument(
        "--param",
        action="append",
        default=[],
        help="per-bot params.json override key=value (repeatable; range-checked)",
    )
    ap.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = ap.parse_args()

    if "-" in args.label:
        raise SystemExit("label must not contain '-' (it is a bot_id segment)")
    from src.signal.patterns import registry  # imported late: needs the repo on sys.path

    if args.pattern not in registry:
        raise SystemExit(f"{args.pattern!r} is not a registered pattern")

    pairs = [p.strip() for p in args.pairs.split(",")] if args.pairs else list(SCALP_PAIRS)
    with open(args.bots, encoding="utf-8") as fh:
        fleet = json.load(fh)
    existing = {b["bot_id"] for b in fleet}
    with open(os.path.join(_ROOT, "params.json"), encoding="utf-8") as fh:
        overrides = parse_overrides(args.param, json.load(fh))
    new = cohort(args.label, args.pattern, args.bracket, pairs, args.timeframe, overrides)
    clash = [b["bot_id"] for b in new if b["bot_id"] in existing]
    if clash:
        raise SystemExit(f"{len(clash)} bot_id(s) already in {args.bots}, e.g. {clash[0]} — refusing")

    staged = os.path.join(os.path.dirname(os.path.abspath(args.new_out)), f".stage_{args.label}.json")
    os.makedirs(os.path.dirname(staged), exist_ok=True)
    with open(staged, "w", encoding="utf-8") as fh:
        json.dump(new, fh, indent=2)
    for guard in (["retired_ledger.py", "check", staged], ["bot_registry.py", "check", staged]):
        rc = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "scripts", guard[0]), *guard[1:]], cwd=_ROOT
        ).returncode
        if rc != 0:
            os.remove(staged)
            raise SystemExit(f"guard {guard[0]} refused the cohort (exit {rc})")
    os.remove(staged)

    print(
        f"{args.label}: {len(new)} bots ({args.pattern} / {args.bracket} / {args.timeframe}); fleet {len(fleet)} -> {len(fleet) + len(new)}"
    )
    if args.dry_run:
        return
    with open(args.bots, "w", encoding="utf-8") as fh:
        json.dump(fleet + new, fh, indent=2)
        fh.write("\n")
    prior = []
    if os.path.exists(args.new_out):
        with open(args.new_out, encoding="utf-8") as fh:
            prior = json.load(fh)
    with open(args.new_out, "w", encoding="utf-8") as fh:
        json.dump(prior + new, fh, indent=2)


if __name__ == "__main__":
    sys.path.insert(0, _ROOT)
    main()
