#!/usr/bin/env python3
"""
Dashboard-as-code generator for the Kestrel STAGING / live-ops board.

Writes infra/grafana/dashboards/kestrel-staging.json — a SECOND provisioned
dashboard (the file provider loads every *.json in the dir), dedicated to the
operational concerns of a live-venue phase that the labs board (kestrel.json)
does not surface:

  * Quarantine health — is the live demo connection actually up? (heartbeats,
    feed liveness, connection/order errors — things a paper sim never had)
  * Order flow — orders/fills/rejections against a real matching engine
  * Realized performance for the selected phase
  * Fill realism vs the model — realized entry slippage vs the §18 0.05%/side
    assumption (the §18 "simulated fee+slippage vs real fills <15% deviation"
    check). NOTE: LiveExecution records the MODELED taker fee (it computes
    notional×0.04%, not the venue's reported fee — execution/live.py is frozen),
    so fee deviation is not measurable here; slippage IS (fill vs candle close).
  * Labs↔phase cross-check — same (pair, strategy) cells in dev vs the phase, to
    see whether the demo venue behaves like the simulation that selected them.

`env` template variable (Phase: dev | staging | prod, DEFAULT staging) scopes
every single-phase panel. The labs↔phase panel is intentionally cross-env.
Self-contained (own copies of the small panel helpers) so it can never perturb
the main board generator.

Run:  python3 scripts/build_staging_dashboard.py
"""

from __future__ import annotations

import json
import os

DS = {"type": "postgres", "uid": "kestrel-db"}
PV = "11.3.0"
OUT = os.path.join(os.path.dirname(__file__), "..", "infra", "grafana", "dashboards", "kestrel-staging.json")

ENVF = "${env}"
NOW_MS = "(EXTRACT(EPOCH FROM NOW())*1000)::BIGINT"

TH_BLUE = {"mode": "absolute", "steps": [{"color": "blue", "value": None}]}
TH_PNL = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 0}]}
TH_GREEN = {"mode": "absolute", "steps": [{"color": "green", "value": None}]}
TH_WIN = {
    "mode": "absolute",
    "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 50}, {"color": "green", "value": 55}],
}
TH_PF = {
    "mode": "absolute",
    "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 1.2}],
}
TH_LIMIT = {
    "mode": "absolute",
    "steps": [
        {"color": "red", "value": None},
        {"color": "orange", "value": -4},
        {"color": "yellow", "value": -2},
        {"color": "green", "value": 0},
    ],
}
# errors / staleness: green at 0/low, red as it grows
TH_ERR = {
    "mode": "absolute",
    "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}],
}
# heartbeat / feed age in seconds: green fresh, red stale
TH_AGE = {
    "mode": "absolute",
    "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 90}, {"color": "red", "value": 300}],
}
# bots up: red at 0, green when present
TH_UP = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}
# slippage as ×model (1.0 = exactly the 0.05% assumption): green near/under model, red over
TH_SLIP = {
    "mode": "absolute",
    "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1.15}, {"color": "red", "value": 2}],
}

# ── Layout engine (self-contained copies) ───────────────────────────────────
_cur = {"x": 0, "y": 0, "rowh": 0}
panels: list = []
_pid = 0


def nid():
    global _pid
    _pid += 1
    return _pid


def section(title):
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


def place(w, h):
    if _cur["x"] + w > 24:
        _cur["x"], _cur["y"] = 0, _cur["y"] + _cur["rowh"]
        _cur["rowh"] = 0
    g = {"x": _cur["x"], "y": _cur["y"], "w": w, "h": h}
    _cur["x"] += w
    _cur["rowh"] = max(_cur["rowh"], h)
    return g


def tgt(sql, fmt="table"):
    return {"datasource": DS, "format": fmt, "rawQuery": True, "rawSql": sql, "refId": "A"}


def fc(unit=None, thresholds=None, custom=None, mn=None, mx=None, decimals=None):
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


def stat(title, w, h, sql, unit=None, thresholds=None, color="value", graph="none", decimals=None):
    panels.append(
        {
            "datasource": DS,
            "fieldConfig": fc(unit, thresholds, decimals=decimals),
            "gridPos": place(w, h),
            "id": nid(),
            "options": {
                "colorMode": color,
                "graphMode": graph,
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


def gauge(title, w, h, sql, unit=None, thresholds=None, mn=None, mx=None):
    panels.append(
        {
            "datasource": DS,
            "fieldConfig": fc(unit, thresholds, mn=mn, mx=mx),
            "gridPos": place(w, h),
            "id": nid(),
            "options": {
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "showThresholdLabels": False,
                "showThresholdMarkers": True,
                "orientation": "auto",
            },
            "pluginVersion": PV,
            "targets": [tgt(sql)],
            "title": title,
            "type": "gauge",
        }
    )


def ts(title, w, h, sql, unit="currencyUSD", fmt="table", color=None, tline=None):
    custom = {
        "axisBorderShow": False,
        "axisCenteredZero": False,
        "axisColorMode": "text",
        "axisLabel": "",
        "axisPlacement": "auto",
        "barAlignment": 0,
        "drawStyle": "line",
        "fillOpacity": 20,
        "gradientMode": "opacity",
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
        "lineInterpolation": "linear",
        "lineWidth": 2,
        "pointSize": 5,
        "scaleDistribution": {"type": "linear"},
        "showPoints": "never",
        "spanNulls": True,
        "stacking": {"group": "A", "mode": "none"},
        "thresholdsStyle": {"mode": "line"} if tline is not None else {"mode": "off"},
    }
    th = {"mode": "absolute", "steps": [{"color": "green", "value": None}]}
    if tline is not None:
        th = {"mode": "absolute", "steps": [{"color": "transparent", "value": None}, {"color": "blue", "value": tline}]}
    f = fc(unit, th, custom)
    f["defaults"]["color"] = {"mode": "fixed", "fixedColor": color} if color else {"mode": "palette-classic"}
    panels.append(
        {
            "datasource": DS,
            "fieldConfig": f,
            "gridPos": place(w, h),
            "id": nid(),
            "options": {
                "legend": {"calcs": ["last"], "displayMode": "list", "placement": "bottom", "showLegend": True},
                "tooltip": {"mode": "multi", "sort": "none"},
            },
            "pluginVersion": PV,
            "targets": [tgt(sql, fmt)],
            "title": title,
            "type": "timeseries",
        }
    )


def histogram(title, w, h, sql, unit=None, color="blue"):
    f = fc(unit, TH_PNL)
    f["defaults"]["color"] = {"mode": "fixed", "fixedColor": color}
    f["defaults"]["custom"] = {
        "fillOpacity": 70,
        "gradientMode": "none",
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
        "lineWidth": 1,
    }
    panels.append(
        {
            "datasource": DS,
            "fieldConfig": f,
            "gridPos": place(w, h),
            "id": nid(),
            "options": {
                "bucketOffset": 0,
                "combine": False,
                "legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": False},
                "tooltip": {"mode": "single", "sort": "none"},
            },
            "pluginVersion": PV,
            "targets": [tgt(sql)],
            "title": title,
            "type": "histogram",
        }
    )


def table(title, w, h, sql, overrides=None):
    f = fc()
    f["defaults"]["custom"] = {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False}
    f["overrides"] = overrides or []
    panels.append(
        {
            "datasource": DS,
            "fieldConfig": f,
            "gridPos": place(w, h),
            "id": nid(),
            "options": {
                "cellHeight": "sm",
                "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
                "showHeader": True,
            },
            "pluginVersion": PV,
            "targets": [tgt(sql, "table")],
            "title": title,
            "type": "table",
        }
    )


# Common predicates for the selected phase.
ENV_T = f"env = '{ENVF}'"  # trades / signals / events / candles
ENV_HB = f"bot_id LIKE '{ENVF}-%'"  # heartbeats has no env column
CLOSED = f"WHERE {ENV_T} AND exit_ts IS NOT NULL"
# Realized entry slippage vs the candle the signal acted on (closest closed
# candle at/just before the fill), signed so + = adverse fill. Model = 0.05%/side.
SLIP_LATERAL = (
    "LEFT JOIN LATERAL (SELECT close FROM candles WHERE pair=t.pair "
    "AND timeframe=t.timeframe AND env=t.env AND ts <= t.entry_ts "
    "ORDER BY ts DESC LIMIT 1) c ON true"
)
SLIP_EXPR = (
    "(CASE WHEN t.direction='long' THEN (t.entry_price - c.close) "
    "ELSE (c.close - t.entry_price) END) / NULLIF(c.close,0) * 100"
)
REALIZED_R = (
    "(CASE WHEN direction='long' THEN exit_price-entry_price ELSE entry_price-exit_price END)"
    "/NULLIF(ABS(entry_price-sl_price),0)"
)

# ── Panels ───────────────────────────────────────────────────────────────────
section("Quarantine Health — is the live demo actually up?")
stat(
    "Live Bots (hb < 90s)",
    4,
    4,
    f"SELECT COUNT(*) AS v FROM heartbeats WHERE {ENV_HB} AND ts >= {NOW_MS} - 90000",
    thresholds=TH_UP,
    color="background",
)
stat("Configured Bots", 4, 4, f"SELECT COUNT(*) AS v FROM heartbeats WHERE {ENV_HB}", thresholds=TH_BLUE)
stat(
    "Last Heartbeat Age (s)",
    4,
    4,
    f"SELECT COALESCE(({NOW_MS} - MAX(ts))/1000.0, 999999) AS v FROM heartbeats WHERE {ENV_HB}",
    "s",
    TH_AGE,
    decimals=0,
)
stat(
    "Feed: Last Candle Age (min)",
    4,
    4,
    f"SELECT COALESCE(({NOW_MS} - MAX(ts))/60000.0, 999999) AS v FROM candles WHERE {ENV_T}",
    "m",
    {
        "mode": "absolute",
        "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 250}, {"color": "red", "value": 600}],
    },
    decimals=0,
)
stat(
    "Connection Errors (24h)",
    4,
    4,
    f"SELECT COUNT(*) AS v FROM events WHERE {ENV_T} AND category='connection' AND level IN ('WARN','ERROR','CRITICAL') AND ts >= {NOW_MS} - 86400000",
    thresholds=TH_ERR,
    color="background",
)
stat("Open Positions", 4, 4, f"SELECT COUNT(*) AS v FROM trades WHERE {ENV_T} AND exit_ts IS NULL", thresholds=TH_BLUE)

section("Order Flow — real matching engine (rejections/latency a sim never sees)")
stat(
    "Orders Entered (24h)",
    4,
    4,
    f"SELECT COUNT(*) AS v FROM trades WHERE {ENV_T} AND entry_ts >= {NOW_MS} - 86400000",
    thresholds=TH_BLUE,
)
stat(
    "Fills Closed (24h)",
    4,
    4,
    f"SELECT COUNT(*) AS v FROM trades WHERE {ENV_T} AND exit_ts >= {NOW_MS} - 86400000",
    thresholds=TH_BLUE,
)
stat(
    "Order Errors (24h)",
    4,
    4,
    f"SELECT COUNT(*) AS v FROM events WHERE {ENV_T} AND category='order' AND level IN ('ERROR','CRITICAL') AND ts >= {NOW_MS} - 86400000",
    thresholds=TH_ERR,
    color="background",
)
stat(
    "Risk Rejections (24h)",
    4,
    4,
    f"SELECT COUNT(*) AS v FROM signals WHERE {ENV_T} AND outcome='rejected' AND ts >= {NOW_MS} - 86400000",
    thresholds=TH_BLUE,
)
stat(
    "Liquidations (all)",
    4,
    4,
    f"SELECT COUNT(*) AS v FROM trades WHERE {ENV_T} AND close_reason='liquidated'",
    thresholds=TH_ERR,
    color="background",
)
stat(
    "Avg Fill→Close Hold (candles)",
    4,
    4,
    f"SELECT ROUND(AVG(hold_candles),1) AS v FROM trades {CLOSED}",
    thresholds=TH_BLUE,
    decimals=1,
)
table(
    "Recent Order / Position / Connection Events",
    24,
    8,
    f"SELECT to_timestamp(ts/1000.0) AS time, bot_id, level, category, message FROM events WHERE {ENV_T} AND category IN ('order','position','connection') ORDER BY ts DESC LIMIT 30",
)

section("Realized Performance — selected phase ($env)")
stat(
    "Account Balance (start $100)",
    4,
    4,
    f"SELECT 100 + COALESCE(SUM(pnl_net_usdt),0) AS v FROM trades {CLOSED}",
    "currencyUSD",
    TH_PNL,
    graph="area",
)
stat("Realized PnL", 4, 4, f"SELECT COALESCE(SUM(pnl_net_usdt),0) AS v FROM trades {CLOSED}", "currencyUSD", TH_PNL)
stat(
    "Win Rate",
    4,
    4,
    f"SELECT CASE WHEN COUNT(*)=0 THEN 0 ELSE 100.0*SUM((pnl_net_usdt>0)::int)/COUNT(*) END AS v FROM trades {CLOSED}",
    "percent",
    TH_WIN,
    decimals=1,
)
stat(
    "Expectancy ($/trade)",
    4,
    4,
    f"SELECT COALESCE(AVG(pnl_net_usdt),0) AS v FROM trades {CLOSED}",
    "currencyUSD",
    TH_PNL,
    decimals=4,
)
stat(
    "Profit Factor",
    4,
    4,
    f"SELECT COALESCE(SUM(pnl_net_usdt) FILTER (WHERE pnl_net_usdt>0)/NULLIF(-SUM(pnl_net_usdt) FILTER (WHERE pnl_net_usdt<=0),0),0) AS v FROM trades {CLOSED}",
    thresholds=TH_PF,
    decimals=2,
)
gauge(
    "Today PnL vs −$5 limit (UTC)",
    4,
    4,
    f"SELECT COALESCE(SUM(pnl_net_usdt),0) AS v FROM trades WHERE {ENV_T} AND exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) >= date_trunc('day', NOW() AT TIME ZONE 'UTC')",
    "currencyUSD",
    TH_LIMIT,
    mn=-5,
    mx=5,
)
ts(
    "Equity Curve ($100 + cumulative realized PnL)",
    16,
    8,
    f'SELECT to_timestamp(exit_ts/1000.0) AS "time", 100 + SUM(pnl_net_usdt) OVER (ORDER BY exit_ts) AS equity FROM trades {CLOSED} AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() ORDER BY exit_ts',
)
table(
    "Per-Bot Leaderboard ($env)",
    8,
    8,
    f"SELECT bot_id, COUNT(*) AS trades, ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS win_pct, ROUND(SUM(pnl_net_usdt),4) AS net_usdt, ROUND(AVG({REALIZED_R}),2) AS avg_r FROM trades {CLOSED} GROUP BY bot_id ORDER BY net_usdt DESC",
)

section("Fill Realism vs Model — §18 sim-vs-real (target < 15% deviation)")
stat(
    "Avg Entry Slippage % (signed)",
    6,
    4,
    f"SELECT ROUND(AVG({SLIP_EXPR}),4) AS v FROM trades t {SLIP_LATERAL} WHERE t.env='{ENVF}' AND t.exit_ts IS NOT NULL",
    "percent",
    TH_PNL,
    decimals=4,
)
stat(
    "Slippage vs 0.05% model (×)",
    6,
    4,
    f"SELECT ROUND(AVG(ABS({SLIP_EXPR}))/0.05, 2) AS v FROM trades t {SLIP_LATERAL} WHERE t.env='{ENVF}' AND t.exit_ts IS NOT NULL",
    thresholds=TH_SLIP,
    color="background",
    decimals=2,
)
stat(
    "Avg Abs Slippage %",
    6,
    4,
    f"SELECT ROUND(AVG(ABS({SLIP_EXPR})),4) AS v FROM trades t {SLIP_LATERAL} WHERE t.env='{ENVF}' AND t.exit_ts IS NOT NULL",
    "percent",
    TH_BLUE,
    decimals=4,
)
stat(
    "Trades Measured",
    6,
    4,
    f"SELECT COUNT(*) AS v FROM trades t {SLIP_LATERAL} WHERE t.env='{ENVF}' AND t.exit_ts IS NOT NULL AND c.close IS NOT NULL",
    thresholds=TH_BLUE,
)
histogram(
    "Entry slippage % — distribution (0.05% = model)",
    12,
    7,
    f"SELECT ROUND({SLIP_EXPR}, 4) AS slippage_pct FROM trades t {SLIP_LATERAL} WHERE t.env='{ENVF}' AND t.exit_ts IS NOT NULL",
    "percent",
    color="orange",
)
ts(
    "Entry slippage % over time (line = 0.05% model)",
    12,
    7,
    f"SELECT to_timestamp(t.entry_ts/1000.0) AS \"time\", {SLIP_EXPR} AS slippage_pct FROM trades t {SLIP_LATERAL} WHERE t.env='{ENVF}' AND t.exit_ts IS NOT NULL AND to_timestamp(t.entry_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() ORDER BY t.entry_ts",
    unit="percent",
    color="orange",
    tline=0.05,
)

section("Labs ↔ Phase cross-check — does the demo venue match the sim?")
table(
    "Same (pair, strategy) cells: dev vs $env",
    24,
    9,
    "SELECT pair, split_part(bot_id,'-',4) AS strategy, env, COUNT(*) AS trades, "
    "ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS win_pct, ROUND(AVG(pnl_net_usdt),4) AS expectancy, "
    "ROUND(SUM(pnl_net_usdt),4) AS net_usdt "
    f"FROM trades WHERE env IN ('dev', '{ENVF}') AND exit_ts IS NOT NULL "
    "GROUP BY pair, strategy, env ORDER BY pair, strategy, env",
)

section("Recent Staging Trades")
table(
    "Recent Trades ($env)",
    24,
    9,
    "SELECT to_timestamp(entry_ts/1000.0) AS entry, bot_id, pair, direction, "
    "ROUND(entry_price::numeric,6) AS entry_px, ROUND(exit_price::numeric,6) AS exit_px, close_reason, "
    f"ROUND(pnl_net_usdt,4) AS net_usdt, hold_candles FROM trades WHERE {ENV_T} ORDER BY entry_ts DESC LIMIT 30",
)

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
    "links": [],
    "panels": panels,
    "refresh": "30s",
    "schemaVersion": 39,
    "tags": ["kestrel", "staging", "live-ops"],
    "templating": {
        "list": [
            {
                "name": "env",
                "label": "Phase",
                "type": "custom",
                "description": "Phase to monitor — defaults to staging (Phase 2 BingX VST demo).",
                "query": "dev,staging,prod",
                "options": [
                    {"text": "dev", "value": "dev", "selected": False},
                    {"text": "staging", "value": "staging", "selected": True},
                    {"text": "prod", "value": "prod", "selected": False},
                ],
                "current": {"text": "staging", "value": "staging", "selected": True},
                "includeAll": False,
                "multi": False,
                "skipUrlSync": False,
                "hide": 0,
            }
        ]
    },
    "time": {"from": "now-7d", "to": "now"},
    "timepicker": {},
    "timezone": "utc",
    "title": "Kestrel — Staging / Live Ops ($env)",
    "uid": "kestrel-staging",
    "version": 1,
    "weekStart": "",
}

with open(OUT, "w") as f:
    json.dump(dashboard, f, indent=2)
n_p = len([p for p in panels if p["type"] != "row"])
n_s = len([p for p in panels if p["type"] == "row"])
print(f"wrote {os.path.normpath(OUT)} with {n_p} panels + {n_s} sections")


if __name__ == "__main__":
    pass
