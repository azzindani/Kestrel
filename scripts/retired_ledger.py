#!/usr/bin/env python3
"""Retired-strategy ledger guard.

`retired_strategies.json` is the permanent record of strategy cells that lost a
live forward test. A cell is (pattern, timeframe, bracket). This script keeps
the fleet from quietly redeploying one:

    scripts/retired_ledger.py list                # print the ledger
    scripts/retired_ledger.py check [bots.json]   # exit 1 if any bot is a retired cell

`check` matches on pattern + timeframe_entry, and when the ledger entry carries a
concrete bracket (tp/sl/max_hold) also on those three params, so a retired
pattern may still be re-tried under a materially different bracket. Entries
whose bracket is a prose note match on pattern + timeframe alone (the whole
pattern/timeframe was retired).

Run it wherever bot_registry.py check runs (before commit, before restart).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LEDGER = os.path.join(_ROOT, "retired_strategies.json")
_BOTS = os.path.join(_ROOT, "bots.json")
_BRACKET_KEYS = ("tp_atr_multiplier", "sl_atr_multiplier", "max_hold_candles")


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _bots(path: str) -> list[dict[str, Any]]:
    raw = _load(path)
    return raw if isinstance(raw, list) else list(raw.get("bots", []))


def _bracket_of(params: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(params.get(k) for k in _BRACKET_KEYS)


def _matches(bot: dict[str, Any], entry: dict[str, Any]) -> bool:
    patterns = set(bot.get("patterns") or [])
    if entry["pattern"] not in patterns:
        return False
    if bot.get("timeframe_entry") != entry["timeframe"]:
        return False
    bracket = entry.get("bracket") or {}
    if all(k in bracket for k in _BRACKET_KEYS):
        return _bracket_of(bot.get("params") or {}) == _bracket_of(bracket)
    return True


def check(path: str) -> int:
    ledger = _load(_LEDGER)
    hits: list[tuple[str, str]] = []
    for bot in _bots(path):
        for entry in ledger.get("retired", []):
            if _matches(bot, entry):
                hits.append((bot["bot_id"], entry["label"]))
    if hits:
        print(f"RETIRED cells present in {os.path.relpath(path, _ROOT)}: {len(hits)}")
        for bot_id, label in hits:
            print(f"  {bot_id}  <-  retired {label}")
        return 1
    print(f"OK: no retired cell in {os.path.relpath(path, _ROOT)}")
    return 0


def list_ledger() -> int:
    ledger = _load(_LEDGER)
    print("RETIRED CELLS")
    for e in ledger.get("retired", []):
        print(
            f"  {e['retired_on']}  {e['pattern']:<18} {e['timeframe']:<3}  bots={e['bots']:<3} "
            f"n={e['trades']:<5} win={e['win_pct']:>5}%  PF={e['profit_factor']:<5} net={e['net_usdt']}"
        )
        print(f"      {e['reason']}")
    print("REFUTED DESIGNS")
    for d in ledger.get("refuted_designs", []):
        print(f"  {d['date']}  {d['design']}")
        print(f"      rule: {d['rule']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="print the ledger")
    c = sub.add_parser("check", help="exit 1 if a bots file contains a retired cell")
    c.add_argument("path", nargs="?", default=_BOTS)
    args = ap.parse_args()
    if args.cmd == "list":
        return list_ledger()
    return check(args.path)


if __name__ == "__main__":
    sys.exit(main())
