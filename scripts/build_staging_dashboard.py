#!/usr/bin/env python3
"""
Build the Phase-2 STAGING Grafana dashboard.

Owner directive (2026-06-22): the staging dashboard must have the SAME verbosity as
the Phase-1 labs dashboard — staging is where the best performers live, so it needs
every panel the labs view has, not a slimmed-down summary.

Rather than maintain a second (always-drifting) panel set, this generates the staging
dashboard as an exact CLONE of the Phase-1 dashboard (infra/grafana/dashboards/
kestrel.json — 170+ panels, all queries scoped to the `env` template variable),
then retargets the clone to Phase 2:
    - uid           -> kestrel-staging  (distinct provisioned dashboard)
    - title         -> Kestrel — Staging (Phase 2 · best performers)
    - env variable  -> default/current = 'staging' (and 'staging' selected)
    - tags          -> + 'staging'

So staging gets byte-for-byte the same verbosity as Phase 1, and any panel added to
build_dashboard.py automatically flows here on the next run. The phase-1 dashboard
keeps its own env dropdown (dev/staging/prod) too; this is just a dedicated,
always-staging view to bookmark and compare side-by-side. (The previous slim
live-ops panel set — order flow / fill realism / labs↔phase cross-check — is in git
history; re-add those when staging moves from the sim engine to a real demo venue.)

Run after build_dashboard.py (it reads that script's output):
    python3 scripts/build_dashboard.py && python3 scripts/build_staging_dashboard.py
"""

from __future__ import annotations

import copy
import json
import os

SRC = os.path.join(os.path.dirname(__file__), "..", "infra", "grafana", "dashboards", "kestrel.json")
OUT = os.path.join(os.path.dirname(__file__), "..", "infra", "grafana", "dashboards", "kestrel-staging.json")


def _retarget_env_var(dashboard: dict) -> None:
    """Set the `env` template variable's default + current to 'staging'."""
    for var in dashboard.get("templating", {}).get("list", []):
        if var.get("name") != "env":
            continue
        for opt in var.get("options", []):
            opt["selected"] = opt.get("value") == "staging"
        var["current"] = {"text": "staging", "value": "staging", "selected": True}


def build() -> dict:
    with open(SRC) as f:
        dashboard = json.load(f)
    dashboard = copy.deepcopy(dashboard)
    dashboard["uid"] = "kestrel-staging"
    dashboard["title"] = "Kestrel — Staging (Phase 2 · best performers)"
    dashboard["tags"] = sorted(set(dashboard.get("tags", [])) | {"kestrel", "staging"})
    dashboard["version"] = 1
    _retarget_env_var(dashboard)
    return dashboard


def main() -> None:
    dashboard = build()
    with open(OUT, "w") as f:
        json.dump(dashboard, f, indent=2)
    n_panels = len([p for p in dashboard.get("panels", []) if p.get("type") != "row"])
    print(f"wrote {os.path.normpath(OUT)}: {n_panels} panels (cloned from kestrel.json, env=staging)")


if __name__ == "__main__":
    main()
