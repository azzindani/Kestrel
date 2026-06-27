#!/usr/bin/env python3
"""build_analysis_dashboard.py — generate the Kestrel DATA-ANALYSIS Grafana board.

Distinct from build_dashboard.py (live phase monitor): this board is for ANALYSING the
ENTIRE recorded dataset, including data that no longer lives in the live DB after a reset.
It reads from TWO provisioned datasources:

  * KestrelArchive (uid 'kestrel-archive') — the kestrel_archive DB, into which a chosen
    backup dump is restored by scripts/restore_archive.py. Holds the FULL history:
    trades / signals / events / microstructure as of that backup.
  * KestrelDB (uid 'kestrel-db') — the live DB (candles + ongoing microstructure that the
    reset always keeps), for current-coverage panels.

Sections: dataset overview · trade analytics (full history) · signal funnel · order-flow /
microstructure (incl. the depth_imb5 alignment thesis joined to real trade outcomes).

Run:  python3 scripts/build_analysis_dashboard.py   →   infra/grafana/dashboards/kestrel-analysis.json
Then: docker compose restart grafana   (provisioning reloads the board + datasource)
"""

from __future__ import annotations

import json
import os

DS_LIVE = {"type": "postgres", "uid": "kestrel-db"}
DS_ARCH = {"type": "postgres", "uid": "kestrel-archive"}
PV = "11.3.0"
OUT = os.path.join(os.path.dirname(__file__), "..", "infra", "grafana", "dashboards", "kestrel-analysis.json")

TH_PNL = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 0}]}
TH_BLUE = {"mode": "absolute", "steps": [{"color": "blue", "value": None}]}
TH_WIN = {
    "mode": "absolute",
    "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 45}, {"color": "green", "value": 55}],
}
TH_PF = {
    "mode": "absolute",
    "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 1.3}],
}


# UTC trading-session bucket from an epoch-ms column (mirrors src/config.get_trading_session
# closely enough for analysis: ASIAN 0-8 · LONDON 8-13 · OVERLAP 13-16 · US 16-21 · LATE 21-24).
def _session_case(ts_col: str) -> str:
    h = f"floor((({ts_col}/1000) % 86400) / 3600.0)"
    return (
        f"CASE WHEN {h} < 8 THEN 'ASIAN' WHEN {h} < 13 THEN 'LONDON' "
        f"WHEN {h} < 16 THEN 'OVERLAP' WHEN {h} < 21 THEN 'US' ELSE 'LATE' END"
    )


# ── Layout engine (copied convention from build_dashboard.py) ──────────────────
_cur = {"x": 0, "y": 0, "rowh": 0}
_DS = DS_ARCH
panels: list = []
_pid = 0


def use(ds: dict) -> None:
    """Switch the datasource that subsequent panels query."""
    global _DS
    _DS = ds


def nid() -> int:
    global _pid
    _pid += 1
    return _pid


def section(title: str) -> None:
    if _cur["x"] > 0:
        _cur["y"] += _cur["rowh"]
    _cur["x"], _cur["rowh"] = 0, 0
    panels.append(
        {
            "collapsed": False,
            "gridPos": {"x": 0, "y": _cur["y"], "w": 24, "h": 1},
            "id": nid(),
            "panels": [],
            "title": title,
            "type": "row",
        }
    )
    _cur["y"] += 1


def place(w: int, h: int) -> dict:
    if _cur["x"] + w > 24:
        _cur["x"], _cur["y"] = 0, _cur["y"] + _cur["rowh"]
        _cur["rowh"] = 0
    g = {"x": _cur["x"], "y": _cur["y"], "w": w, "h": h}
    _cur["x"] += w
    _cur["rowh"] = max(_cur["rowh"], h)
    return g


def tgt(sql: str, fmt: str = "table") -> dict:
    return {"datasource": _DS, "format": fmt, "rawQuery": True, "rawSql": sql, "refId": "A"}


def fc(unit=None, thresholds=None, custom=None, mn=None, mx=None, decimals=None) -> dict:
    d = {"color": {"mode": "thresholds"}, "mappings": [], "thresholds": thresholds or TH_BLUE}
    if unit:
        d["unit"] = unit
    if custom is not None:
        d["custom"] = custom
    if mn is not None:
        d["min"] = mn
    if mx is not None:
        d["max"] = mx
    if decimals is not None:
        d["decimals"] = decimals
    return {"defaults": d, "overrides": []}


def stat(title, w, h, sql, unit=None, thresholds=None, color="value", decimals=None) -> None:
    panels.append(
        {
            "datasource": _DS,
            "fieldConfig": fc(unit, thresholds, decimals=decimals),
            "gridPos": place(w, h),
            "id": nid(),
            "options": {
                "colorMode": color,
                "graphMode": "none",
                "justifyMode": "auto",
                "orientation": "auto",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
            },
            "pluginVersion": PV,
            "targets": [tgt(sql)],
            "title": title,
            "type": "stat",
        }
    )


def ts(title, w, h, sql, unit="currencyUSD", color=None, fill=20, bars=False) -> None:
    custom = {
        "axisBorderShow": False,
        "axisCenteredZero": False,
        "axisColorMode": "text",
        "axisLabel": "",
        "axisPlacement": "auto",
        "barAlignment": 0,
        "drawStyle": "bars" if bars else "line",
        "fillOpacity": fill,
        "gradientMode": "opacity",
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
        "lineInterpolation": "linear",
        "lineWidth": 2,
        "pointSize": 5,
        "scaleDistribution": {"type": "linear"},
        "showPoints": "never",
        "spanNulls": True,
        "stacking": {"group": "A", "mode": "none"},
        "thresholdsStyle": {"mode": "off"},
    }
    f = fc(unit, {"mode": "absolute", "steps": [{"color": "green", "value": None}]}, custom)
    f["defaults"]["color"] = {"mode": "fixed", "fixedColor": color} if color else {"mode": "palette-classic"}
    panels.append(
        {
            "datasource": _DS,
            "fieldConfig": f,
            "gridPos": place(w, h),
            "id": nid(),
            "options": {
                "legend": {"calcs": ["last"], "displayMode": "list", "placement": "bottom", "showLegend": True},
                "tooltip": {"mode": "multi", "sort": "none"},
            },
            "pluginVersion": PV,
            "targets": [tgt(sql, "table")],
            "title": title,
            "type": "timeseries",
        }
    )


def pie(title, w, h, sql) -> None:
    panels.append(
        {
            "datasource": _DS,
            "fieldConfig": fc(),
            "gridPos": place(w, h),
            "id": nid(),
            "options": {
                "legend": {"displayMode": "list", "placement": "right", "showLegend": True, "values": ["value"]},
                "pieType": "pie",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                "tooltip": {"mode": "single", "sort": "none"},
            },
            "pluginVersion": PV,
            "targets": [tgt(sql)],
            "title": title,
            "type": "piechart",
        }
    )


def histogram(title, w, h, sql, unit=None, color="blue", bucket=None) -> None:
    f = fc(unit, TH_PNL)
    f["defaults"]["color"] = {"mode": "fixed", "fixedColor": color}
    f["defaults"]["custom"] = {
        "fillOpacity": 70,
        "gradientMode": "none",
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
        "lineWidth": 1,
    }
    opts = {
        "bucketOffset": 0,
        "combine": False,
        "legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": False},
        "tooltip": {"mode": "single", "sort": "none"},
    }
    if bucket is not None:
        opts["bucketSize"] = bucket
    panels.append(
        {
            "datasource": _DS,
            "fieldConfig": f,
            "gridPos": place(w, h),
            "id": nid(),
            "options": opts,
            "pluginVersion": PV,
            "targets": [tgt(sql)],
            "title": title,
            "type": "histogram",
        }
    )


def table(title, w, h, sql, overrides=None) -> None:
    f = fc()
    f["defaults"]["custom"] = {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False}
    f["overrides"] = overrides or []
    panels.append(
        {
            "datasource": _DS,
            "fieldConfig": f,
            "gridPos": place(w, h),
            "id": nid(),
            "options": {
                "cellHeight": "sm",
                "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
                "showHeader": True,
            },
            "pluginVersion": PV,
            "targets": [tgt(sql)],
            "title": title,
            "type": "table",
        }
    )


def col_override(col, unit, th=None) -> dict:
    return {
        "matcher": {"id": "byName", "options": col},
        "properties": [
            {"id": "unit", "value": unit},
            {"id": "custom.cellOptions", "value": {"mode": "gradient", "type": "color-background"}},
            {"id": "thresholds", "value": th or TH_PNL},
        ],
    }


# Reusable SQL fragments — the archive's dev slate, closed trades only.
_DEV = "env='dev'"
_CLOSED = "env='dev' AND exit_ts IS NOT NULL AND pnl_net_usdt IS NOT NULL"
_PF = (
    "ROUND((SUM(CASE WHEN pnl_net_usdt>0 THEN pnl_net_usdt ELSE 0 END)/"
    "NULLIF(-SUM(CASE WHEN pnl_net_usdt<0 THEN pnl_net_usdt ELSE 0 END),0))::numeric,2)"
)


# ── Section 1: Dataset overview ────────────────────────────────────────────────
section("Dataset Overview — what is recorded (live DB + restored archive)")
use(DS_LIVE)
stat("Candles (live)", 4, 4, "SELECT count(*) AS v FROM candles", color="background", thresholds=TH_BLUE)
stat(
    "Microstructure snapshots (live)",
    5,
    4,
    "SELECT count(*) AS v FROM microstructure",
    color="background",
    thresholds=TH_BLUE,
)
stat(
    "Pairs w/ order-flow",
    3,
    4,
    "SELECT count(DISTINCT pair) AS v FROM microstructure",
    color="background",
    thresholds=TH_BLUE,
)
use(DS_ARCH)
stat("Archived trades", 4, 4, f"SELECT count(*) AS v FROM trades WHERE {_DEV}", color="background", thresholds=TH_BLUE)
stat("Archived events", 4, 4, "SELECT count(*) AS v FROM events", color="background", thresholds=TH_BLUE)
stat("Archived signals", 4, 4, "SELECT count(*) AS v FROM signals", color="background", thresholds=TH_BLUE)
use(DS_LIVE)
ts(
    "Order-flow recording coverage — snapshots/hour per pair (live)",
    24,
    7,
    "SELECT date_trunc('hour', to_timestamp(ts/1000.0)) AS \"time\", pair, count(*) AS snapshots "
    "FROM microstructure GROUP BY 1,2 ORDER BY 1",
    unit="short",
)


# ── Section 2: Trade analytics (full history · archive) ────────────────────────
section("Trade Analytics — full recorded history (archive)")
use(DS_ARCH)
stat("Closed trades", 4, 4, f"SELECT count(*) AS v FROM trades WHERE {_CLOSED}", color="background", thresholds=TH_BLUE)
stat(
    "Win rate %",
    4,
    4,
    f"SELECT ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS v FROM trades WHERE {_CLOSED}",
    unit="percent",
    thresholds=TH_WIN,
    color="background",
    decimals=1,
)
stat(
    "Net PnL",
    4,
    4,
    f"SELECT ROUND(SUM(pnl_net_usdt)::numeric,2) AS v FROM trades WHERE {_CLOSED}",
    unit="currencyUSD",
    thresholds=TH_PNL,
    color="background",
    decimals=2,
)
stat(
    "Profit factor",
    4,
    4,
    f"SELECT {_PF} AS v FROM trades WHERE {_CLOSED}",
    thresholds=TH_PF,
    color="background",
    decimals=2,
)
stat(
    "Avg PnL / trade",
    4,
    4,
    f"SELECT ROUND(AVG(pnl_net_usdt)::numeric,4) AS v FROM trades WHERE {_CLOSED}",
    unit="currencyUSD",
    thresholds=TH_PNL,
    color="background",
    decimals=4,
)
stat(
    "Distinct pairs traded",
    4,
    4,
    f"SELECT count(DISTINCT pair) AS v FROM trades WHERE {_DEV}",
    color="background",
    thresholds=TH_BLUE,
)
ts(
    "Cumulative net PnL (equity curve, all archived trades)",
    24,
    8,
    f'SELECT to_timestamp(exit_ts/1000.0) AS "time", '
    f"SUM(pnl_net_usdt) OVER (ORDER BY exit_ts) AS cumulative_net_usdt "
    f"FROM trades WHERE {_CLOSED} ORDER BY exit_ts",
    unit="currencyUSD",
    color="green",
)
table(
    "Per-strategy performance",
    12,
    9,
    f"SELECT split_part(bot_id,'-',4) AS strategy, count(*) AS trades, "
    f"ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS win_pct, "
    f"ROUND(SUM(pnl_net_usdt)::numeric,2) AS net_usdt, "
    f"ROUND(AVG(pnl_net_usdt)::numeric,4) AS avg_usdt, {_PF} AS pf "
    f"FROM trades WHERE {_CLOSED} GROUP BY 1 ORDER BY net_usdt ASC",
    overrides=[
        col_override("net_usdt", "currencyUSD"),
        col_override("avg_usdt", "currencyUSD"),
        col_override("win_pct", "percent", TH_WIN),
        col_override("pf", "short", TH_PF),
    ],
)
table(
    "Per-pair performance",
    12,
    9,
    f"SELECT pair, count(*) AS trades, ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS win_pct, "
    f"ROUND(SUM(pnl_net_usdt)::numeric,2) AS net_usdt, {_PF} AS pf "
    f"FROM trades WHERE {_CLOSED} GROUP BY 1 ORDER BY net_usdt ASC",
    overrides=[
        col_override("net_usdt", "currencyUSD"),
        col_override("win_pct", "percent", TH_WIN),
        col_override("pf", "short", TH_PF),
    ],
)
pie(
    "Close-reason mix",
    8,
    8,
    f"SELECT close_reason, count(*) AS n FROM trades WHERE {_CLOSED} AND close_reason IS NOT NULL GROUP BY 1",
)
histogram(
    "Net PnL per trade (USDT)",
    8,
    8,
    f"SELECT pnl_net_usdt FROM trades WHERE {_CLOSED}",
    unit="currencyUSD",
    color="blue",
    bucket=0.05,
)
table(
    "Win% & net by close reason",
    8,
    8,
    f"SELECT close_reason, count(*) AS n, ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS win_pct, "
    f"ROUND(SUM(pnl_net_usdt)::numeric,2) AS net_usdt "
    f"FROM trades WHERE {_CLOSED} AND close_reason IS NOT NULL GROUP BY 1 ORDER BY net_usdt ASC",
    overrides=[col_override("net_usdt", "currencyUSD"), col_override("win_pct", "percent", TH_WIN)],
)
table(
    "Win% & expectancy by UTC session",
    12,
    7,
    f"SELECT {_session_case('entry_ts')} AS session, count(*) AS trades, "
    f"ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS win_pct, "
    f"ROUND(AVG(pnl_net_usdt)::numeric,4) AS avg_usdt "
    f"FROM trades WHERE {_CLOSED} GROUP BY 1 ORDER BY avg_usdt DESC",
    overrides=[col_override("avg_usdt", "currencyUSD"), col_override("win_pct", "percent", TH_WIN)],
)
table(
    "Win% & net by regime (trades ⋈ signals)",
    12,
    7,
    f"SELECT s.regime, count(*) AS trades, ROUND(100.0*AVG((t.pnl_net_usdt>0)::int),1) AS win_pct, "
    f"ROUND(SUM(t.pnl_net_usdt)::numeric,2) AS net_usdt "
    f"FROM trades t JOIN signals s ON s.trade_id=t.id WHERE t.{_CLOSED} AND s.regime IS NOT NULL "
    f"GROUP BY 1 ORDER BY net_usdt ASC",
    overrides=[col_override("net_usdt", "currencyUSD"), col_override("win_pct", "percent", TH_WIN)],
)


# ── Section 3: Signal funnel (archive) ─────────────────────────────────────────
section("Signal Funnel — where signals die before firing (archive events)")
use(DS_ARCH)
table(
    "Rejection reasons (pipeline + risk stages)",
    12,
    9,
    "SELECT split_part(split_part(message,':',2),':',1) AS reject_reason, count(*) AS n "
    "FROM events WHERE message LIKE 'signal_rejected:%' OR message LIKE 'risk_rejected:%' "
    "GROUP BY 1 ORDER BY n DESC LIMIT 20",
)
pie(
    "Evaluation outcomes",
    12,
    9,
    "SELECT CASE WHEN message LIKE 'signal_rejected:%' THEN 'rejected (pipeline)' "
    "WHEN message LIKE 'risk_rejected:%' THEN 'rejected (risk)' "
    "WHEN message LIKE 'signal_fired%' OR message LIKE 'order_%' THEN 'fired' ELSE 'other' END AS outcome, "
    "count(*) AS n FROM events WHERE category IN ('signal','risk','order') GROUP BY 1 ORDER BY n DESC",
)


# ── Section 4: Order-flow / microstructure (the depth_imb5 gate thesis) ─────────
section("Order-Flow / Microstructure — depth imbalance & the alignment-gate thesis (archive)")
use(DS_ARCH)
table(
    "★ Gate thesis: trade win% by order-flow ALIGNMENT at entry "
    "(depth_imb5 nearest snapshot ≤30s; signed by direction)",
    24,
    8,
    "WITH j AS ("
    "  SELECT t.direction, t.pnl_net_usdt, ("
    "    SELECT m.depth_imb5 FROM microstructure m "
    "    WHERE m.pair=t.pair AND m.ts BETWEEN t.entry_ts-30000 AND t.entry_ts+30000 "
    "    ORDER BY abs(m.ts-t.entry_ts) LIMIT 1) AS imb "
    f"  FROM trades t WHERE t.{_CLOSED}) "
    "SELECT CASE WHEN imb IS NULL THEN 'no order-flow coverage' "
    "  WHEN (direction='long' AND imb>0) OR (direction='short' AND imb<0) THEN 'flow-ALIGNED' "
    "  ELSE 'flow-AGAINST' END AS bucket, "
    "count(*) AS trades, ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS win_pct, "
    "ROUND(AVG(pnl_net_usdt)::numeric,4) AS avg_usdt, ROUND(SUM(pnl_net_usdt)::numeric,2) AS net_usdt "
    "FROM j GROUP BY 1 ORDER BY win_pct DESC NULLS LAST",
    overrides=[
        col_override("win_pct", "percent", TH_WIN),
        col_override("avg_usdt", "currencyUSD"),
        col_override("net_usdt", "currencyUSD"),
    ],
)
histogram(
    "depth_imb5 distribution (all snapshots)",
    8,
    8,
    "SELECT depth_imb5 FROM microstructure WHERE depth_imb5 IS NOT NULL",
    unit="short",
    color="purple",
    bucket=0.1,
)
histogram(
    "Spread (bps) distribution",
    8,
    8,
    "SELECT spread_bps FROM microstructure WHERE spread_bps IS NOT NULL AND spread_bps<20",
    unit="short",
    color="orange",
    bucket=0.25,
)
table(
    "Per-pair microstructure summary",
    8,
    8,
    "SELECT pair, count(*) AS snapshots, ROUND(AVG(spread_bps)::numeric,3) AS avg_spread_bps, "
    "ROUND(AVG(depth_imb5)::numeric,4) AS avg_depth_imb5, ROUND(STDDEV(depth_imb5)::numeric,3) AS sd_imb5 "
    "FROM microstructure GROUP BY 1 ORDER BY snapshots DESC",
)


# ── Dashboard wrapper ──────────────────────────────────────────────────────────
dashboard = {
    "annotations": {
        "list": [
            {
                "builtIn": 1,
                "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "enable": True,
                "hide": True,
                "iconColor": "rgba(0, 211, 255, 1)",
                "name": "Annotations & Alerts",
                "type": "dashboard",
            }
        ]
    },
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 0,
    "id": None,
    "links": [
        {
            "asDropdown": False,
            "icon": "external link",
            "tags": [],
            "targetBlank": False,
            "title": "Phase Monitor",
            "type": "link",
            "url": "/d/kestrel-main",
        }
    ],
    "panels": panels,
    "refresh": "",
    "schemaVersion": 39,
    "tags": ["kestrel", "analysis"],
    "templating": {"list": []},
    "time": {"from": "now-90d", "to": "now"},
    "timepicker": {},
    "timezone": "utc",
    "title": "Kestrel — Data Analysis (full history + backups)",
    "uid": "kestrel-analysis",
    "version": 1,
    "weekStart": "",
}

with open(OUT, "w") as f:
    json.dump(dashboard, f, indent=2)
n_p = len([p for p in panels if p["type"] != "row"])
n_s = len([p for p in panels if p["type"] == "row"])
print(f"wrote {os.path.normpath(OUT)} with {n_p} panels + {n_s} sections")
