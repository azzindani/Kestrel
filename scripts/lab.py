#!/usr/bin/env python3
"""lab.py — compose and run YOUR OWN hand-picked Kestrel fleet(s) (the ENV=lab sandbox).

The research loop drives the dev fleet; the lab is yours. Browse the recorded universe
of bot configs (with how each performed), pick the ones you want, and run them as an
isolated paper fleet — without the loop touching them. Every command accepts
`--sandbox NAME` to keep several independent bot selections running side by side, each
in its own container (default sandbox is "lab" — the original single-container fleet,
unchanged for backward compatibility).

    python3 scripts/lab.py catalog                       # the menu: every (pattern,pair,tf)
                                                          #   that ran + its real performance
    python3 scripts/lab.py patterns                      # which patterns are deployable (fire)
    python3 scripts/lab.py sandboxes                     # list every named sandbox + its status
    python3 scripts/lab.py add --pattern mom_adx --pair BTC/USDT --tf 5m
    python3 scripts/lab.py add dev-ETHUSDT-1h-macd_cross-01   # …or by an existing bot_id
    python3 scripts/lab.py add --sandbox alpha --pattern cci_mom --pair ETH/USDT --tf 1h
    python3 scripts/lab.py list --sandbox alpha           # a specific sandbox's fleet
    python3 scripts/lab.py remove --sandbox alpha labalpha-ETHUSDT-1h-cci_mom-01
    python3 scripts/lab.py deploy --sandbox alpha         # backfill + bring that sandbox up
    python3 scripts/lab.py down --sandbox alpha           # stop just that one sandbox

Every sandbox is ALWAYS paper (ENV=lab → SimulationExecution, never a venue, never real
money) regardless of its name — the safety guarantee doesn't depend on which sandbox you
pick. Rows are isolated by env='lab' + a `lab{NAME}-` bot_id prefix (the default sandbox
keeps the plain `lab-` prefix). Watch any of them in Grafana by selecting "lab" in the
Phase dropdown of the main board, then reading the bot_id prefix. Runs on the host
(orchestrates the postgres + kestrel containers, like backup_db.py / restore_archive.py).

The default sandbox ("lab") is a profile-gated service in docker-compose.yml, brought up
via `docker compose --profile lab up -d lab` — unchanged. Named sandboxes (anything other
than "lab") are NOT declared in docker-compose.yml (compose services are static) — they're
created directly with `docker run` on the shared `kestrel_net` network, mirroring the lab
service's image/env/volumes/healthcheck exactly, so they behave identically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_ARCHIVE_DB = "kestrel_archive"
_NAME_RE = re.compile(r"^[a-z0-9]{1,20}$")


def _dc(*args: str) -> list[str]:
    return ["docker", "compose", *args]


def _run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=_ROOT, capture_output=capture, text=True)


def _psql(db: str, sql: str) -> str:
    r = _run(_dc("exec", "-T", "postgres", "psql", "-U", "kestrel", "-d", db, "-c", sql))
    if r.returncode != 0:
        print(f"[lab] db query failed: {r.stderr.strip()[:300]}", file=sys.stderr)
    return r.stdout


def _validate_sandbox(name: str) -> str:
    if name != "lab" and not _NAME_RE.match(name):
        print(
            f"[lab] invalid sandbox name '{name}' — lowercase letters/digits only, max 20 chars "
            "(no hyphens/underscores: it has to fit inside a bot_id segment)",
            file=sys.stderr,
        )
        sys.exit(1)
    return name


def _prefix(name: str) -> str:
    """bot_id prefix for this sandbox — keeps the existing `lab-` prefix for the default
    sandbox (backward compatible with every already-deployed lab bot / dashboard filter)."""
    return "lab" if name == "lab" else f"lab{name}"


def _container(name: str) -> str:
    return "kestrel-lab-1" if name == "lab" else f"kestrel-sandbox-{name}-1"


def _bots_file(name: str) -> str:
    return os.path.join(_ROOT, "bots.lab.json" if name == "lab" else f"bots.lab-{name}.json")


def _load(name: str) -> list[dict]:
    path = _bots_file(name)
    if not os.path.isfile(path):
        return []
    with open(path) as fh:
        return json.load(fh)


def _save(name: str, bots: list[dict]) -> None:
    with open(_bots_file(name), "w") as fh:
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


def _health_cmd(prefix: str) -> str:
    return (
        f'PGPASSWORD=$DB_PASSWORD psql -h postgres -U $DB_USER -d $DB_NAME -tAc "SELECT '
        "(EXTRACT(EPOCH FROM NOW())*1000)::BIGINT - MAX(ts) < 90000 FROM heartbeats WHERE "
        f"bot_id LIKE '{prefix}-%'\" 2>/dev/null | grep -q ^t || exit 1"
    )


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_catalog(args: argparse.Namespace) -> int:
    """The menu: every (strategy,pair,tf) that ever traded + its recorded performance,
    so you can pick what to try. Reads the restored archive (scripts/restore_archive.py).
    Global across all sandboxes — there's only one recorded universe to browse."""
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


def cmd_sandboxes(args: argparse.Namespace) -> int:
    """List every sandbox that has either a bots file on disk or a running container."""
    names: set[str] = {"lab"}
    for fn in os.listdir(_ROOT):
        if fn == "bots.lab.json":
            continue
        m = re.match(r"^bots\.lab-([a-z0-9]{1,20})\.json$", fn)
        if m:
            names.add(m.group(1))

    print("\n=== SANDBOXES ===")
    for name in sorted(names):
        bots = _load(name)
        container = _container(name)
        ps = _run(
            [
                "docker",
                "inspect",
                container,
                "--format",
                "{{.State.Status}} {{if .State.Health}}({{.State.Health.Status}}){{end}}",
            ]
        )
        status = ps.stdout.strip() if ps.returncode == 0 else "not deployed"
        r = _run(
            _dc(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "kestrel",
                "-d",
                "kestrel",
                "-t",
                "-A",
                "-F",
                ",",
                "-c",
                "SELECT count(*), COALESCE(ROUND(SUM(pnl_net_usdt)::numeric,2),0) FROM trades "
                f"WHERE env='lab' AND exit_ts IS NOT NULL AND bot_id LIKE '{_prefix(name)}-%'",
            )
        )
        trades, net = (r.stdout.strip().split(",") + ["?", "?"])[:2]
        print(f"  {name:12s} bots={len(bots):3d}  container={container:28s} {status:20s}  trades={trades}  net=${net}")
    print("\nDeploy a sandbox:  python3 scripts/lab.py deploy --sandbox <name>")
    return 0


def _bot_entry(prefix: str, pattern: str, pair: str, tf: str, params: dict | None) -> dict:
    token = pair.replace("/", "")
    entry = {
        "bot_id": f"{prefix}-{token}-{tf}-{pattern}-01",
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
    name = _validate_sandbox(args.sandbox)
    prefix = _prefix(name)
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
    entry = _bot_entry(prefix, pattern, pair, tf, params)
    bots = _load(name)
    if any(b["bot_id"] == entry["bot_id"] for b in bots):
        print(f"[lab] {entry['bot_id']} already in sandbox '{name}' — nothing to do")
        return 0
    bots.append(entry)
    _save(name, bots)
    print(
        f"[lab] added {entry['bot_id']} to sandbox '{name}' ({len(bots)} bots). "
        f"Run: python3 scripts/lab.py deploy --sandbox {name}"
    )
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    name = _validate_sandbox(args.sandbox)
    bots = _load(name)
    keep = [b for b in bots if b["bot_id"] != args.bot_id and args.bot_id not in b["bot_id"]]
    if len(keep) == len(bots):
        print(f"[lab] no bot in sandbox '{name}' matched '{args.bot_id}'")
        return 0
    _save(name, keep)
    print(f"[lab] removed {len(bots) - len(keep)} bot(s) from '{name}'; {len(keep)} remain.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    name = _validate_sandbox(args.sandbox)
    bots = _load(name)
    if not bots:
        print(f"[lab] sandbox '{name}' is empty. Browse: python3 scripts/lab.py catalog, then add.")
        return 0
    print(f"\n=== SANDBOX '{name}' — {len(bots)} bot(s) ({os.path.basename(_bots_file(name))}) ===")
    for b in bots:
        ov = f"  params={b['params']}" if b.get("params") else ""
        print(f"  {b['bot_id']:42s} {b['pair']:12s} {b['timeframe_entry']:4s} {b['strategy']}{ov}")
    live = _psql(
        "kestrel",
        "SELECT count(*) trades, ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) win_pct, "
        f"ROUND(SUM(pnl_net_usdt)::numeric,2) net FROM trades WHERE env='lab' AND exit_ts IS NOT NULL "
        f"AND bot_id LIKE '{_prefix(name)}-%'",
    )
    print("\nlive results:\n" + live)
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    name = _validate_sandbox(args.sandbox)
    bots = _load(name)
    if not bots:
        print(f"[lab] sandbox '{name}' is empty — add bots first (python3 scripts/lab.py add ...)", file=sys.stderr)
        return 1
    bots_file = _bots_file(name)
    print(f"[lab] deploying sandbox '{name}' ({len(bots)} bot(s))...")

    # 1. the new bot_ids need candle history (else they start dark). Always run the
    #    backfill inside the already-running dev `kestrel` container — candles are keyed
    #    by (bot_id, pair, tf), not by env, so this works regardless of which sandbox
    #    container will end up running these bots.
    _run(_dc("cp", bots_file, f"kestrel:/app/{os.path.basename(bots_file)}"))
    bf = _run(
        _dc(
            "exec",
            "-T",
            "kestrel",
            "python3",
            "scripts/backfill_history.py",
            "--bots",
            os.path.basename(bots_file),
            "--source",
            "gate",
        )
    )
    tail = bf.stdout.strip().splitlines()[-len(bots) :] if bf.stdout.strip() else []
    print("[lab] backfill:\n" + "\n".join("  " + ln for ln in tail))

    if name == "lab":
        # The default sandbox stays exactly as before — the `lab` profile of the shared
        # kestrel compose project.
        up = _run(_dc("--profile", "lab", "up", "-d", "lab"))
    else:
        container = _container(name)
        prefix = _prefix(name)
        _run(["docker", "rm", "-f", container])  # idempotent re-deploy
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--network",
            "kestrel_net",
            "--env-file",
            os.path.join(_ROOT, ".env.lab"),
            "-e",
            "DB_HOST=postgres",
            "-e",
            "DB_PORT=5432",
            "-v",
            f"{bots_file}:/app/bots.json:ro",
            "-v",
            f"{os.path.join(_ROOT, 'params.json')}:/app/params.json:ro",
            "--restart",
            "unless-stopped",
            "--memory",
            "512m",
            "--cpus",
            "1.0",
            "--health-cmd",
            _health_cmd(prefix),
            "--health-interval",
            "30s",
            "--health-timeout",
            "10s",
            "--health-retries",
            "3",
            "kestrel-kestrel:latest",
        ]
        up = _run(cmd)
    out = (up.stdout + up.stderr).strip()
    print("[lab] " + (out.splitlines()[-1] if out else "up"))
    if up.returncode != 0:
        print("[lab] deploy FAILED — see output above", file=sys.stderr)
        return 1
    print(
        f"[lab] deployed. Watch it in Grafana → main board → Phase dropdown → 'lab' (bot_id prefix '{_prefix(name)}-')."
    )
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    name = _validate_sandbox(args.sandbox)
    if name == "lab":
        r = _run(_dc("--profile", "lab", "rm", "-s", "-f", "lab"))
        out = (r.stdout + r.stderr).strip()
        print("[lab] " + (out.splitlines()[-1] if out else "down"))
        return 0
    container = _container(name)
    _run(["docker", "stop", container])
    r = _run(["docker", "rm", "-f", container])
    if r.returncode == 0:
        print(f"[lab] sandbox '{name}' ({container}) stopped and removed.")
    else:
        print(f"[lab] '{container}' wasn't running (nothing to stop).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Compose and run your own hand-picked Kestrel lab fleet(s).")
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
    sub.add_parser("sandboxes", help="list every named sandbox + its status").set_defaults(func=cmd_sandboxes)

    a = sub.add_parser("add", help="add a bot to a sandbox")
    a.add_argument("ref", nargs="?", help="an existing bot_id to clone (e.g. dev-BTCUSDT-5m-mom_adx-01)")
    a.add_argument("--sandbox", default="lab", help="named sandbox to add to (default: lab)")
    a.add_argument("--pattern")
    a.add_argument("--pair")
    a.add_argument("--tf")
    a.add_argument("--params", help="JSON params override, e.g. '{\"tp_atr_multiplier\":2.4}'")
    a.set_defaults(func=cmd_add)

    r = sub.add_parser("remove", help="remove a bot (exact bot_id or substring) from a sandbox")
    r.add_argument("bot_id")
    r.add_argument("--sandbox", default="lab", help="named sandbox to remove from (default: lab)")
    r.set_defaults(func=cmd_remove)

    lst = sub.add_parser("list", help="show a sandbox's fleet + live results")
    lst.add_argument("--sandbox", default="lab", help="named sandbox to show (default: lab)")
    lst.set_defaults(func=cmd_list)

    dep = sub.add_parser("deploy", help="backfill + bring a sandbox up")
    dep.add_argument("--sandbox", default="lab", help="named sandbox to deploy (default: lab)")
    dep.set_defaults(func=cmd_deploy)

    down = sub.add_parser("down", help="stop a sandbox")
    down.add_argument("--sandbox", default="lab", help="named sandbox to stop (default: lab)")
    down.set_defaults(func=cmd_down)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
