#!/usr/bin/env python3
"""
Bot config registry — the "have we already run this exact bot?" ledger.

PROBLEM this solves: every fleet change overwrites bots.json, so the only record
of past bot configurations was git history (recoverable but not queryable) and the
prose REFUTED LEDGER in RESEARCH_LOOP.md (archetypes, not exact params). Nothing
stopped the research loop from re-creating a bot it had already deployed and
measured. The DB stores outcomes keyed by bot_id but no params at all.

WHAT THIS DOES: reconstructs a config-fingerprint archive from the FULL git history
of bots.json (every committed fleet snapshot) plus the current working tree, and
writes it SHARDED into the `bot_registry/` directory — one JSON file per instrument
(`bot_registry/BTCUSDT.json`, ...) plus a `bot_registry/_index.json` summary. The
old monolithic `bot_registry.json` (~1.3 MB) was too bulky to read or diff; per-
instrument shards are small, browsable, and let a check load only the relevant
pair. Each unique config gets a stable fingerprint over its BEHAVIORAL fields —
pair (normalised), entry/regime timeframe, strategy, patterns, params,
max_active_buckets — and explicitly NOT the bot_id label (two bots with the same
behaviour but different labels are the same bot). For each fingerprint it records
when it was first seen, last seen, and in how many fleet snapshots it appeared.

Layer: this is a research/ops tool (scripts/), stdlib only, no DB or network. It
does NOT touch any frozen file and adds NO database table (db/schema.py is frozen;
§4 schema changes are human-gated) — the registry is plain JSON files in the repo.

Commands:
    build           rebuild bot_registry/ (sharded by pair) from git history +
                    current bots.json; removes the legacy monolith if present
    check [PATH]    classify each config in PATH (default bots.json) as NEW or SEEN
                    against the registry; exit 1 if any are SEEN (usable as a guard
                    in the loop: "don't re-create a bot we already tested")

Run:
    python3 scripts/bot_registry.py build
    python3 scripts/bot_registry.py check                 # checks bots.json
    python3 scripts/bot_registry.py check exp_preview.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_BOTS = os.path.join(_ROOT, "bots.json")
_REGISTRY_DIR = os.path.join(_ROOT, "bot_registry")  # sharded: one file per pair
_LEGACY_MONOLITH = os.path.join(_ROOT, "bot_registry.json")  # removed on build
_INDEX = "_index.json"  # summary file inside _REGISTRY_DIR
_TRACKED = "bots.json"  # path as tracked by git, relative to repo root


def _shard_name(pair: str) -> str:
    """Filename for a pair's shard; empty/odd pairs bucket into _MISC."""
    safe = "".join(ch for ch in pair if ch.isalnum())
    return f"{safe}.json" if safe else "_MISC.json"


def _norm_pair(pair: str) -> str:
    """BTC/USDT and BTCUSDT are the same market — normalise to the bare token."""
    return pair.replace("/", "").upper()


def _canonical(entry: dict) -> dict:
    """Behavioural config only — the bot_id label is deliberately excluded."""
    params = entry.get("params", {}) or {}
    return {
        "pair": _norm_pair(str(entry.get("pair", ""))),
        "timeframe_entry": str(entry.get("timeframe_entry", "")),
        "timeframe_regime": str(entry.get("timeframe_regime", "")),
        "strategy": str(entry.get("strategy", "")),
        "patterns": sorted(str(p) for p in (entry.get("patterns", []) or [])),
        "max_active_buckets": int(entry.get("max_active_buckets", 1)),
        "params": {k: params[k] for k in sorted(params)},
    }


def _fingerprint(canon: dict) -> str:
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", _ROOT, *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _history_snapshots() -> list[tuple[str, str, str, list]]:
    """(commit, iso_date, subject, parsed_bots) for every commit that touched bots.json, oldest first."""
    log = _git("log", "--reverse", "--format=%H%x09%cI%x09%s", "--", _TRACKED)
    out: list[tuple[str, str, str, list]] = []
    for line in log.splitlines():
        if not line.strip():
            continue
        commit, date, subject = (line.split("\t", 2) + ["", ""])[:3]
        try:
            raw = _git("show", f"{commit}:{_TRACKED}")
            bots = json.loads(raw)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue  # file deleted or unparseable at this commit — skip
        if isinstance(bots, list):
            out.append((commit, date, subject, bots))
    return out


def _ingest(reg: dict, bots: list, commit: str, date: str, subject: str) -> None:
    for entry in bots:
        if not isinstance(entry, dict):
            continue
        canon = _canonical(entry)
        fp = _fingerprint(canon)
        rec = reg.get(fp)
        if rec is None:
            reg[fp] = {
                "fingerprint": fp,
                "config": canon,
                "first_seen_commit": commit,
                "first_seen_date": date,
                "first_seen_subject": subject,
                "last_seen_commit": commit,
                "last_seen_date": date,
                "snapshot_count": 1,
                "example_bot_id": entry.get("bot_id", ""),
            }
        else:
            rec["last_seen_commit"] = commit
            rec["last_seen_date"] = date
            rec["snapshot_count"] += 1


def build() -> int:
    reg: dict = {}
    snaps = _history_snapshots()
    for commit, date, subject, bots in snaps:
        _ingest(reg, bots, commit, date, subject)

    # Fold in the current working tree (may be uncommitted during a loop iteration).
    if os.path.exists(_BOTS):
        with open(_BOTS) as f:
            current = json.load(f)
        _ingest(reg, current, "WORKING_TREE", "", "(uncommitted working tree)")

    # Shard by instrument. Each fingerprint includes the pair, so a config lives in
    # exactly one shard — no cross-shard duplication.
    shards: dict[str, list] = {}
    for rec in reg.values():
        shards.setdefault(_shard_name(rec["config"]["pair"]), []).append(rec)

    # Rebuild the directory from scratch so dropped configs don't linger as stale shards.
    if os.path.isdir(_REGISTRY_DIR):
        for fn in os.listdir(_REGISTRY_DIR):
            if fn.endswith(".json"):
                os.remove(os.path.join(_REGISTRY_DIR, fn))
    else:
        os.makedirs(_REGISTRY_DIR)

    index: dict[str, int] = {}
    for shard, recs in sorted(shards.items()):
        recs.sort(key=lambda r: (r["first_seen_date"], r["fingerprint"]))
        pair = recs[0]["config"]["pair"] or "_MISC"
        with open(os.path.join(_REGISTRY_DIR, shard), "w") as f:
            json.dump({"version": 1, "pair": pair, "configs": recs}, f, indent=2)
        index[shard] = len(recs)

    with open(os.path.join(_REGISTRY_DIR, _INDEX), "w") as f:
        json.dump(
            {
                "version": 1,
                "source": "git history of bots.json + working tree",
                "total_configs": len(reg),
                "committed_snapshots": len(snaps),
                "shards": dict(sorted(index.items(), key=lambda kv: -kv[1])),
            },
            f,
            indent=2,
        )

    # Drop the legacy bulk file if it's still around.
    if os.path.exists(_LEGACY_MONOLITH):
        os.remove(_LEGACY_MONOLITH)

    print(
        f"wrote {os.path.relpath(_REGISTRY_DIR, _ROOT)}/: {len(reg)} unique configs "
        f"across {len(index)} instrument shards ({len(snaps)} committed fleet snapshots)"
    )
    return 0


def _load_registry() -> dict:
    if not os.path.isdir(_REGISTRY_DIR):
        print(f"no registry at {_REGISTRY_DIR}/; run: bot_registry.py build", file=sys.stderr)
        sys.exit(2)
    reg: dict = {}
    for fn in os.listdir(_REGISTRY_DIR):
        if fn == _INDEX or not fn.endswith(".json"):
            continue
        with open(os.path.join(_REGISTRY_DIR, fn)) as f:
            data = json.load(f)
        for r in data.get("configs", []):
            reg[r["fingerprint"]] = r
    return reg


def check(path: str) -> int:
    reg = _load_registry()
    with open(path) as f:
        bots = json.load(f)

    new, seen = [], []
    for entry in bots:
        if not isinstance(entry, dict):
            continue
        fp = _fingerprint(_canonical(entry))
        (seen if fp in reg else new).append((entry.get("bot_id", "?"), fp))

    print(f"checked {os.path.relpath(path, _ROOT)} against registry ({len(reg)} known configs)")
    print(f"  NEW : {len(new)}")
    print(f"  SEEN: {len(seen)}")
    for bot_id, fp in seen:
        first = reg[fp]
        print(f"    - {bot_id}  [{fp}] first seen {first['first_seen_date'][:10]} ({first['first_seen_subject'][:50]})")
    # Exit 1 when duplicates exist so the loop can use this as a guard.
    return 1 if seen else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="rebuild bot_registry.json from git history + current bots.json")
    c = sub.add_parser("check", help="classify a bots file as NEW vs SEEN against the registry")
    c.add_argument("path", nargs="?", default=_BOTS, help="bots file to check (default: bots.json)")
    args = ap.parse_args()

    if args.cmd == "build":
        return build()
    if args.cmd == "check":
        return check(args.path)
    return 2


if __name__ == "__main__":
    sys.exit(main())
