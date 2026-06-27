#!/usr/bin/env python3
"""lab.py — compose and run YOUR OWN hand-picked Kestrel fleet (the ENV=lab sandbox).

The research loop drives the dev fleet; the lab is yours. Browse the recorded universe
of bot configs (with how each performed), pick the ones you want, and run them as an
isolated paper fleet — without the loop touching them.

    python3 scripts/lab.py catalog                       # the menu: every (pattern,pair,tf)
                                                          #   that ran + its real performance
    python3 scripts/lab.py patterns                      # which patterns are deployable (fire)
    python3 scripts/lab.py add --pattern mom_adx --pair BTC/USDT --tf 5m
    python3 scripts/lab.py add dev-ETHUSDT-1h-macd_cross-01   # …or by an existing bot_id
    python3 scripts/lab.py list                           # your current lab fleet
    python3 scripts/lab.py remove lab-BTCUSDT-5m-mom_adx-01
    python3 scripts/lab.py deploy                         # backfill + bring the lab fleet up
    python3 scripts/lab.py down                           # stop the lab fleet

Lab is ALWAYS paper (ENV=lab → SimulationExecution, never a venue, never real money).
Rows are isolated by env='lab' + the `lab-` bot_id prefix; watch them in Grafana by
selecting "lab" in the Phase dropdown of the main board. Runs on the host (orchestrates
the postgres + kestrel containers, like backup_db.py / restore_archive.py).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_BOTS_LAB = os.path.join(_ROOT, "bots.lab.json")
_ARCHIVE_DB = "kestrel_archive"


def _dc(*args: str) -> list[str]:
    return ["docker", "compose", *args]


def _run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=_ROOT, capture_output=capture, text=True)


def _psql(db: str, sql: str) -> str:
    r = _run(_dc("exec", "-T", "postgres", "psql", "-U", "kestrel", "-d", db, "-c", sql))
    if r.returncode != 0:
        print(f"[lab] db query failed: {r.stderr.strip()[:300]}", file=sys.stderr)
    return r.stdout


def _load() -> list[dict]:
    if not os.path.isfile(_BOTS_LAB):
        return []
    with open(_BOTS_LAB) as fh:
        return json.load(fh)


def _save(bots: list[dict]) -> None:
    with open(_BOTS_LAB, "w") as fh:
        json.dump(bots, fh, indent=2)


def _deployable_patterns() -> dict[str, dict]:
    """Authoritative deployable-pattern map from the container registry: each registered
    pattern + whether it is regime-permitted (else it NEVER fires — the §9 gotcha)."""
    code = (
        "import json;"
        "from src.signal.patterns import registry, SELF_DIRECTING_PATTERNS;"
        "from src.signal.regime import regime_permits_pattern;"
        "from src.config import Regime;"
        "R=[Regime.TRENDING,Regime.VOLATILE,Regime.RANGING];"
        "print(json.dumps({n:{'self_directing':n in SELF_DIRECTING_PATTERNS,"
        "'regime_permitted':any(regime_permits_pattern(r,n) for r in R)} for n in registry}))"
    )
    r = _run(_dc("exec", "-T", "kestrel", "python3", "-c", code))
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(f"[lab] could not read pattern registry: {r.stderr.strip()[:200]}", file=sys.stderr)
        return {}


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_catalog(args: argparse.Namespace) -> int:
    """The menu: every (strategy,pair,tf) that ever traded + its recorded performance,
    so you can pick what to try. Reads the restored archive (scripts/restore_archive.py)."""
    where = ["env='dev'", "exit_ts IS NOT NULL", "pnl_net_usdt IS NOT NULL"]
    if args.pattern:
        where.append(f"split_part(bot_id,'-',4)='{args.pattern}'")
    if args.pair:
        where.append(f"pair='{args.pair}'")
    if args.tf:
        where.append(f"timeframe='{args.tf}'")
    order = {"net": "net_usdt DESC", "win": "win_pct DESC", "trades": "trades DESC"}[args.sort]
    sql = (
        "SELECT split_part(bot_id,'-',4) AS pattern, pair, timeframe AS tf, count(*) AS trades, "
        "ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS win_pct, "
        "ROUND(SUM(pnl_net_usdt)::numeric,2) AS net_usdt, "
        "ROUND(AVG(pnl_net_usdt)::numeric,4) AS avg_usdt "
        f"FROM trades WHERE {' AND '.join(where)} "
        f"GROUP BY 1,2,3 HAVING count(*) >= {args.min_trades} ORDER BY {order} LIMIT {args.top}"
    )
    out = _psql(_ARCHIVE_DB, sql)
    if not out.strip() or "0 rows" in out:
        print("[lab] no catalog rows — is the archive restored? run: python3 scripts/restore_archive.py")
        return 0
    print(f"\n=== CATALOG — recorded (pattern,pair,tf) cells, sorted by {args.sort} (top {args.top}) ===")
    print(out)
    print("Pick one and add it, e.g.:  python3 scripts/lab.py add --pattern <pattern> --pair <pair> --tf <tf>")
    print("(Honest: none are net-positive — the project has no proven edge. The lab is for YOU to try ideas.)")
    return 0


def cmd_patterns(args: argparse.Namespace) -> int:
    pats = _deployable_patterns()
    if not pats:
        return 1
    print("\n=== DEPLOYABLE PATTERNS (regime_permitted=False ⇒ it will NEVER fire) ===")
    for name in sorted(pats):
        p = pats[name]
        flag = "" if p["regime_permitted"] else "   ⚠ NOT regime-permitted (won't fire)"
        sd = "self-directing" if p["self_directing"] else "trend-following"
        print(f"  {name:24s} {sd}{flag}")
    return 0


def _bot_entry(pattern: str, pair: str, tf: str, params: dict | None) -> dict:
    token = pair.replace("/", "")
    entry = {
        "bot_id": f"lab-{token}-{tf}-{pattern}-01",
        "pair": pair,
        "timeframe_entry": tf,
        "timeframe_regime": tf,
        "max_active_buckets": 1,
        "strategy": pattern,
        "patterns": [pattern],
    }
    if params:
        entry["params"] = params
    return entry


def cmd_add(args: argparse.Namespace) -> int:
    # Resolve (pattern,pair,tf) either from an explicit bot_id or the flags.
    if args.ref:
        parts = args.ref.split("-")
        # {env}-{PAIRTOKEN}-{tf}-{pattern...}-{inst}
        if len(parts) < 5:
            print(f"[lab] cannot parse bot_id '{args.ref}' (expected env-PAIR-tf-pattern-inst)", file=sys.stderr)
            return 1
        token, tf, pattern = parts[1], parts[2], "-".join(parts[3:-1])
        pair = args.pair or (token[:-4] + "/USDT" if token.endswith("USDT") else token)
    else:
        if not (args.pattern and args.pair and args.tf):
            print("[lab] need either a bot_id, or all of --pattern --pair --tf", file=sys.stderr)
            return 1
        pattern, pair, tf = args.pattern, args.pair, args.tf

    pats = _deployable_patterns()
    if pats and pattern not in pats:
        print(f"[lab] '{pattern}' is not a registered pattern. See: python3 scripts/lab.py patterns", file=sys.stderr)
        return 1
    if pats and not pats[pattern]["regime_permitted"]:
        print(
            f"[lab] ⚠ '{pattern}' is registered but NOT regime-permitted → it will never fire. Aborting.",
            file=sys.stderr,
        )
        return 1

    params = json.loads(args.params) if args.params else None
    entry = _bot_entry(pattern, pair, tf, params)
    bots = _load()
    if any(b["bot_id"] == entry["bot_id"] for b in bots):
        print(f"[lab] {entry['bot_id']} already in the lab fleet — nothing to do")
        return 0
    bots.append(entry)
    _save(bots)
    print(f"[lab] added {entry['bot_id']}  ({len(bots)} bots in the lab fleet). Run: python3 scripts/lab.py deploy")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    bots = _load()
    keep = [b for b in bots if b["bot_id"] != args.bot_id and args.bot_id not in b["bot_id"]]
    if len(keep) == len(bots):
        print(f"[lab] no lab bot matched '{args.bot_id}'")
        return 0
    _save(keep)
    print(f"[lab] removed {len(bots) - len(keep)} bot(s); {len(keep)} remain. Run: python3 scripts/lab.py deploy")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    bots = _load()
    if not bots:
        print("[lab] lab fleet is empty. Browse: python3 scripts/lab.py catalog, then add.")
        return 0
    print(f"\n=== LAB FLEET — {len(bots)} bot(s) (bots.lab.json) ===")
    for b in bots:
        ov = f"  params={b['params']}" if b.get("params") else ""
        print(f"  {b['bot_id']:42s} {b['pair']:12s} {b['timeframe_entry']:4s} {b['strategy']}{ov}")
    # live performance if any lab trades exist
    live = _psql(
        "kestrel",
        "SELECT count(*) trades, ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) win_pct, "
        "ROUND(SUM(pnl_net_usdt)::numeric,2) net FROM trades WHERE env='lab' AND exit_ts IS NOT NULL",
    )
    print("\nlive lab results:\n" + live)
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    bots = _load()
    if not bots:
        print("[lab] lab fleet is empty — add bots first (python3 scripts/lab.py add ...)", file=sys.stderr)
        return 1
    print(f"[lab] deploying {len(bots)} lab bot(s)...")
    # 1. the new lab bot_ids need candle history (else they start dark)
    _run(_dc("cp", _BOTS_LAB, "kestrel:/app/bots.lab.json"))
    bf = _run(
        _dc(
            "exec",
            "-T",
            "kestrel",
            "python3",
            "scripts/backfill_history.py",
            "--bots",
            "bots.lab.json",
            "--source",
            "gate",
        )
    )
    print("[lab] backfill:\n" + "\n".join("  " + ln for ln in bf.stdout.strip().splitlines()[-len(bots) :]))
    # 2. bring up (or reload) the isolated lab compose project
    up = _run(_dc("-p", "kestrel-lab", "-f", "docker-compose.lab.yml", "up", "-d"))
    print(
        "[lab] " + (up.stdout + up.stderr).strip().splitlines()[-1] if (up.stdout + up.stderr).strip() else "[lab] up"
    )
    print("[lab] deployed. Watch it in Grafana → main board → Phase dropdown → 'lab'.")
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    r = _run(_dc("-p", "kestrel-lab", "-f", "docker-compose.lab.yml", "down"))
    print("[lab] " + (r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout + r.stderr).strip() else "[lab] down")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Compose and run your own hand-picked Kestrel lab fleet.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("catalog", help="browse recorded (pattern,pair,tf) cells + performance")
    c.add_argument("--pattern")
    c.add_argument("--pair")
    c.add_argument("--tf")
    c.add_argument("--sort", choices=["net", "win", "trades"], default="net")
    c.add_argument("--min-trades", type=int, default=5)
    c.add_argument("--top", type=int, default=40)
    c.set_defaults(func=cmd_catalog)

    sub.add_parser("patterns", help="list deployable patterns").set_defaults(func=cmd_patterns)

    a = sub.add_parser("add", help="add a bot to the lab fleet")
    a.add_argument("ref", nargs="?", help="an existing bot_id to clone into the lab (e.g. dev-BTCUSDT-5m-mom_adx-01)")
    a.add_argument("--pattern")
    a.add_argument("--pair")
    a.add_argument("--tf")
    a.add_argument("--params", help="JSON params override, e.g. '{\"tp_atr_multiplier\":2.4}'")
    a.set_defaults(func=cmd_add)

    r = sub.add_parser("remove", help="remove a bot (exact bot_id or substring)")
    r.add_argument("bot_id")
    r.set_defaults(func=cmd_remove)

    sub.add_parser("list", help="show the current lab fleet + live results").set_defaults(func=cmd_list)
    sub.add_parser("deploy", help="backfill + bring the lab fleet up").set_defaults(func=cmd_deploy)
    sub.add_parser("down", help="stop the lab fleet").set_defaults(func=cmd_down)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
