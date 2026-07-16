#!/usr/bin/env python3
"""
Dashboard-as-code generator for the Kestrel Grafana board.

Writes infra/grafana/dashboards/kestrel.json. The Grafana file-provider watches
that directory (updateIntervalSeconds: 30), so a regenerate + ~30s is all it
takes to refresh — no Grafana restart, no manual import.

Layout is auto-flowed: section() starts a titled row band; place(w, h) packs
panels left-to-right and wraps at width 24, so adding/removing panels never
requires hand-editing grid coordinates.

All panels are backed by the provisioned Postgres datasource (uid 'kestrel-db').
Capital model: nominal account = $100 (CLAUDE.md §17); equity = 100 + realized PnL.
Trading sessions match src/config.get_trading_session (UTC hour):
    OVERLAP 13-16 · LONDON 8-13 · US 16-21 · ASIAN 21-08.

Data notes (verified against the live DB):
  - candles.regime is NOT persisted (NULL) — regime context lives on signals/trades.
  - signal-rejection funnel comes from events ('signal_rejected:<reason>').
  - candles carry one row per (pair,tf,ts) per bot — dedup with DISTINCT ON / GROUP BY.
  - sub-cent pairs (PEPE) need 8-dp price display; ATR is shown as % of price so
    volatility is comparable across pairs.
Run:  python3 scripts/build_dashboard.py
"""

from __future__ import annotations

import json
import os
import re

DS = {"type": "postgres", "uid": "kestrel-db"}
PV = "11.3.0"
OUT = os.path.join(os.path.dirname(__file__), "..", "infra", "grafana", "dashboards", "kestrel.json")

# Phase scope — every query is rescoped to the dashboard's `env` template variable
# so ONE board serves all three phases (Phase 1 labs=dev · Phase 2 staging · Phase 3
# prod) via the top dropdown. Without this the panels aggregate ALL envs into one
# view, mixing labs paper trades with staging/prod once those start writing rows.
ENVF = "${env}"  # Grafana single-value variable → substituted to e.g. 'dev' at query time

# env-bearing tables (§19) filter on env; heartbeats has no env column, so it is
# scoped by the {env}- bot_id prefix instead (bot_id format {env}-{pair}-{tf}-{inst}).
_ENV_TABLES = {
    "trades": f"env = '{ENVF}'",
    "signals": f"env = '{ENVF}'",
    "events": f"env = '{ENVF}'",
    "candles": f"env = '{ENVF}'",
    "heartbeats": f"bot_id LIKE '{ENVF}-%'",
}


def _scope_env(sql: str) -> str:
    """Rescope every base-table reference in a raw SQL string to the `env` variable.

    Replaces `FROM/JOIN <table> [alias]` with `FROM/JOIN (SELECT * FROM <table>
    WHERE <pred>) <alias>`. A single re.sub pass per table is left-to-right and
    does NOT re-scan inserted text, so the `FROM <table>` inside the injected
    subquery is never re-wrapped. Existing lowercase aliases (t, s, …) are
    preserved; bare references get the table name as the derived-table alias.
    Keywords after the table (WHERE/GROUP/ORDER/UNION) are uppercase, so the
    `(?![A-Z])[a-z]` alias guard never mistakes a keyword for an alias.
    """
    for tbl, pred in _ENV_TABLES.items():

        def repl(m: "re.Match") -> str:
            alias = (m.group(2) or "").strip() or tbl
            return f"{m.group(1)} (SELECT * FROM {tbl} WHERE {pred}) {alias}"

        sql = re.sub(rf"\b(FROM|JOIN)\s+{tbl}\b(\s+(?![A-Z])[a-z]\w*)?", repl, sql)
    return sql


TH_PNL = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 0}]}
TH_BLUE = {"mode": "absolute", "steps": [{"color": "blue", "value": None}]}
TH_WIN = {
    "mode": "absolute",
    "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 50}, {"color": "green", "value": 55}],
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
TH_DD = {
    "mode": "absolute",
    "steps": [{"color": "red", "value": None}, {"color": "orange", "value": -2}, {"color": "green", "value": -0.0001}],
}
TH_PF = {
    "mode": "absolute",
    "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 1.5}],
}
TH_GREEN = {"mode": "absolute", "steps": [{"color": "green", "value": None}]}
TH_RED = {"mode": "absolute", "steps": [{"color": "red", "value": None}]}
TH_ZERO_BAD = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 0.5}]}

# Session CASE replicating src/config.get_trading_session (UTC hour from epoch ms)
_SESSION_CASE = (
    "CASE WHEN h>=13 AND h<16 THEN 'OVERLAP' WHEN h>=8 AND h<16 THEN 'LONDON' "
    "WHEN h>=13 AND h<21 THEN 'US' ELSE 'ASIAN' END"
)
_HOUR = "((entry_ts/3600000)::bigint % 24)"  # UTC hour-of-day from entry
# Equity time-series subquery (running balance from $100)
_EQUITY = "SELECT exit_ts, to_timestamp(exit_ts/1000.0) AS t, 100 + SUM(pnl_net_usdt) OVER (ORDER BY exit_ts) AS equity FROM trades WHERE exit_ts IS NOT NULL"
_MOVE = "CASE WHEN t.direction='long' THEN (c.close - t.entry_price) ELSE (t.entry_price - c.close) END"
_LATERAL = "LEFT JOIN LATERAL (SELECT close FROM candles WHERE pair=t.pair AND timeframe=t.timeframe ORDER BY ts DESC LIMIT 1) c ON true"
# Per-trade path stats (MAE/MFE) — max favourable/adverse excursion during the hold.
_PX = (
    "LEFT JOIN LATERAL (SELECT MAX(high) AS hi, MIN(low) AS lo FROM candles "
    "WHERE pair=t.pair AND timeframe=t.timeframe AND ts BETWEEN t.entry_ts AND t.exit_ts) px ON true"
)
_MFE = (
    "CASE WHEN t.direction='long' THEN (px.hi - t.entry_price)/t.entry_price*100 "
    "ELSE (t.entry_price - px.lo)/t.entry_price*100 END"
)
_MAE = (
    "CASE WHEN t.direction='long' THEN (px.lo - t.entry_price)/t.entry_price*100 "
    "ELSE (t.entry_price - px.hi)/t.entry_price*100 END"
)
_RISK = "NULLIF(ABS(t.entry_price - t.sl_price),0)"
_REALIZED_R = (
    "ROUND((CASE WHEN t.direction='long' THEN t.exit_price-t.entry_price "
    "ELSE t.entry_price-t.exit_price END)/" + _RISK + ",2)"
)
_PLANNED_RR = "ROUND(ABS(t.tp_price-t.entry_price)/" + _RISK + ",2)"
_CLOSED = "FROM trades WHERE exit_ts IS NOT NULL"
# Lab grid token (split_part(bot_id,'-',4) = e.g. t16s10h8) + its filter
_TOK = "split_part(bot_id,'-',4)"
_LAB = f"exit_ts IS NOT NULL AND {_TOK} ~ '^t[0-9]+s[0-9]+h[0-9]+$'"
# Latest candle per pair (dedup the per-bot duplicates)
_LATEST_CANDLE = "SELECT DISTINCT ON (pair) * FROM candles ORDER BY pair, ts DESC"
# One indicator value per (pair, ts) — collapses per-bot duplicate candle rows
_NOW_MS = "(EXTRACT(EPOCH FROM NOW())*1000)::BIGINT"

# ── Layout engine ────────────────────────────────────────────────────────────
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


# ── Field-config + panel helpers ─────────────────────────────────────────────
def tgt(sql, fmt="table"):
    return {"datasource": DS, "format": fmt, "rawQuery": True, "rawSql": _scope_env(sql), "refId": "A"}


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


def ts(
    title,
    w,
    h,
    sql,
    unit="currencyUSD",
    fmt="table",
    bars=False,
    fill=20,
    color=None,
    step=False,
    mx=None,
    mn=None,
    tline=None,
    stack=False,
):
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
        "lineInterpolation": "stepAfter" if step else "linear",
        "lineWidth": 2,
        "pointSize": 5,
        "scaleDistribution": {"type": "linear"},
        "showPoints": "never",
        "spanNulls": True,
        "stacking": {"group": "A", "mode": "normal" if stack else "none"},
        "thresholdsStyle": {"mode": "line"} if tline is not None else {"mode": "off"},
    }
    th = {"mode": "absolute", "steps": [{"color": "green", "value": None}]}
    if tline is not None:
        th = {"mode": "absolute", "steps": [{"color": "transparent", "value": None}, {"color": "blue", "value": tline}]}
    f = fc(unit, th, custom, mx=mx, mn=mn)
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


def bargauge(title, w, h, sql, unit=None, thresholds=None):
    panels.append(
        {
            "datasource": DS,
            "fieldConfig": fc(unit, thresholds),
            "gridPos": place(w, h),
            "id": nid(),
            "options": {
                "displayMode": "gradient",
                "orientation": "horizontal",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                "showUnfilled": True,
                "valueMode": "color",
            },
            "pluginVersion": PV,
            "targets": [tgt(sql)],
            "title": title,
            "type": "bargauge",
        }
    )


def pie(title, w, h, sql):
    panels.append(
        {
            "datasource": DS,
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


def histogram(title, w, h, sql, unit=None, color="blue", bucket=None):
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
            "datasource": DS,
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
            "targets": [tgt(sql)],
            "title": title,
            "type": "table",
        }
    )


def col_override(col, unit, bg=True, th=None):
    cell = {"mode": "gradient", "type": "color-background"} if bg else {"type": "color-text"}
    return {
        "matcher": {"id": "byName", "options": col},
        "properties": [
            {"id": "unit", "value": unit},
            {"id": "custom.cellOptions", "value": cell},
            {"id": "thresholds", "value": th or TH_PNL},
        ],
    }


def map_override(col, mapping):
    return {
        "matcher": {"id": "byName", "options": col},
        "properties": [
            {"id": "mappings", "value": [{"type": "value", "options": mapping}]},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
        ],
    }


# ════════════════════════════════════════════════════════════════════════════
# 1 — Account & Capital
# ════════════════════════════════════════════════════════════════════════════
section("💰 Account & Capital")
stat(
    "Account Balance (start $100)",
    4,
    4,
    "SELECT 100 + COALESCE(SUM(pnl_net_usdt),0) AS balance FROM trades WHERE exit_ts IS NOT NULL",
    "currencyUSD",
    TH_PNL,
    graph="area",
)
stat(
    "Realized PnL (all-time)",
    4,
    4,
    "SELECT COALESCE(SUM(pnl_net_usdt),0) AS pnl FROM trades WHERE exit_ts IS NOT NULL",
    "currencyUSD",
    TH_PNL,
)
stat(
    "Unrealized PnL (open, MtM)",
    4,
    4,
    f"SELECT COALESCE(SUM(({_MOVE})/t.entry_price * t.notional_usdt),0) AS unrealized FROM trades t {_LATERAL} WHERE t.exit_ts IS NULL",
    "currencyUSD",
    TH_PNL,
)
stat(
    "Return on Capital",
    4,
    4,
    "SELECT COALESCE(SUM(pnl_net_usdt),0) AS roi FROM trades WHERE exit_ts IS NOT NULL",
    "percent",
    TH_PNL,
    decimals=2,
)
stat("Open Positions", 4, 4, "SELECT COUNT(*) AS open FROM trades WHERE exit_ts IS NULL", thresholds=TH_BLUE)
gauge(
    "Today PnL vs −$5 limit (UTC)",
    4,
    4,
    "SELECT COALESCE(SUM(pnl_net_usdt),0) AS today FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) >= date_trunc('day', NOW() AT TIME ZONE 'UTC')",
    "currencyUSD",
    TH_LIMIT,
    mn=-5,
    mx=5,
)
stat("Total Trades", 3, 4, "SELECT COUNT(*) AS total FROM trades", thresholds=TH_BLUE)
stat("Closed", 3, 4, "SELECT COUNT(*) AS c FROM trades WHERE exit_ts IS NOT NULL", thresholds=TH_BLUE)
stat(
    "Win Rate (closed)",
    3,
    4,
    "SELECT CASE WHEN COUNT(*)=0 THEN 0 ELSE 100.0*SUM((pnl_net_usdt>0)::int)/COUNT(*) END AS win_rate FROM trades WHERE exit_ts IS NOT NULL",
    "percent",
    TH_WIN,
    decimals=1,
)
stat(
    "Expectancy ($/trade)",
    3,
    4,
    "SELECT COALESCE(AVG(pnl_net_usdt),0) AS v FROM trades WHERE exit_ts IS NOT NULL",
    "currencyUSD",
    TH_PNL,
    decimals=4,
)
stat(
    "Profit Factor",
    3,
    4,
    "SELECT COALESCE(SUM(pnl_net_usdt) FILTER (WHERE pnl_net_usdt>0)/NULLIF(-SUM(pnl_net_usdt) FILTER (WHERE pnl_net_usdt<=0),0),0) AS v FROM trades WHERE exit_ts IS NOT NULL",
    thresholds=TH_PF,
    decimals=2,
)
stat(
    "Avg Realized R",
    3,
    4,
    f"SELECT ROUND(AVG((CASE WHEN direction='long' THEN exit_price-entry_price ELSE entry_price-exit_price END)/NULLIF(ABS(entry_price-sl_price),0)),3) AS v {_CLOSED}",
    thresholds=TH_PNL,
    decimals=3,
)
stat(
    "Best Trade",
    3,
    4,
    "SELECT COALESCE(MAX(pnl_net_usdt),0) AS v FROM trades WHERE exit_ts IS NOT NULL",
    "currencyUSD",
    TH_GREEN,
    decimals=4,
)
stat(
    "Worst Trade",
    3,
    4,
    "SELECT COALESCE(MIN(pnl_net_usdt),0) AS v FROM trades WHERE exit_ts IS NOT NULL",
    "currencyUSD",
    TH_RED,
    decimals=4,
)
stat(
    "Capital Deployed (open margin)",
    3,
    4,
    "SELECT COALESCE(SUM(size_usdt),0) AS v FROM trades WHERE exit_ts IS NULL",
    "currencyUSD",
    TH_BLUE,
)
stat(
    "Open Notional Exposure",
    3,
    4,
    "SELECT COALESCE(SUM(notional_usdt),0) AS v FROM trades WHERE exit_ts IS NULL",
    "currencyUSD",
    TH_BLUE,
)

# ════════════════════════════════════════════════════════════════════════════
# 12 — Per-Bot / Pair Breakdown
# ════════════════════════════════════════════════════════════════════════════
section("🏆 Per-Bot & Per-Pair Breakdown")
table(
    "Per-Bot Performance",
    12,
    8,
    "SELECT bot_id, COUNT(*) FILTER (WHERE exit_ts IS NOT NULL) AS closed, COUNT(*) FILTER (WHERE exit_ts IS NULL) AS open, ROUND(100.0*SUM((pnl_net_usdt>0)::int) FILTER (WHERE exit_ts IS NOT NULL)/NULLIF(COUNT(*) FILTER (WHERE exit_ts IS NOT NULL),0),1) AS win_rate, ROUND(SUM(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS net_pnl FROM trades GROUP BY bot_id ORDER BY net_pnl DESC NULLS LAST LIMIT 40",
    overrides=[col_override("net_pnl", "currencyUSD"), col_override("win_rate", "percent", bg=False, th=TH_WIN)],
)
table(
    "PnL by Pair",
    12,
    8,
    "SELECT pair, COUNT(*) FILTER (WHERE exit_ts IS NOT NULL) AS closed, ROUND(100.0*SUM((pnl_net_usdt>0)::int) FILTER (WHERE exit_ts IS NOT NULL)/NULLIF(COUNT(*) FILTER (WHERE exit_ts IS NOT NULL),0),1) AS win_rate, ROUND(AVG(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS avg_pnl, ROUND(SUM(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS net_pnl FROM trades GROUP BY pair ORDER BY net_pnl NULLS LAST",
    overrides=[
        col_override("net_pnl", "currencyUSD"),
        col_override("avg_pnl", "currencyUSD"),
        col_override("win_rate", "percent", bg=False, th=TH_WIN),
    ],
)
bargauge(
    "Trades per Bot (activity)",
    12,
    7,
    "SELECT bot_id AS metric, COUNT(*) AS value FROM trades GROUP BY bot_id ORDER BY value DESC LIMIT 25",
    "short",
    TH_BLUE,
)
bargauge(
    "Net PnL by Pair",
    12,
    7,
    "SELECT pair AS metric, ROUND(SUM(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS value FROM trades GROUP BY pair ORDER BY value",
    "currencyUSD",
    TH_PNL,
)

# ════════════════════════════════════════════════════════════════════════════
# 4 — Win-Rate Analytics
# ════════════════════════════════════════════════════════════════════════════
section("🎯 Win-Rate Analytics")
ts(
    "Rolling Win-Rate — last 20 vs cumulative (55% = go-live target)",
    24,
    7,
    'SELECT q.t AS "time", q.r20 AS "rolling_20", q.cum AS "cumulative" FROM (SELECT exit_ts, to_timestamp(exit_ts/1000.0) AS t, 100.0*AVG((pnl_net_usdt>0)::int) OVER (ORDER BY exit_ts ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS r20, 100.0*AVG((pnl_net_usdt>0)::int) OVER (ORDER BY exit_ts) AS cum FROM trades WHERE exit_ts IS NOT NULL) q WHERE q.t BETWEEN $__timeFrom() AND $__timeTo() ORDER BY q.exit_ts',
    unit="percent",
    mx=100,
    tline=55,
    fill=8,
)
bargauge(
    "Win Rate by Direction",
    8,
    7,
    "SELECT direction AS metric, ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS value FROM trades WHERE exit_ts IS NOT NULL GROUP BY direction ORDER BY value DESC",
    "percent",
    TH_WIN,
)
bargauge(
    "Win Rate by Pair",
    8,
    7,
    "SELECT pair AS metric, ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS value FROM trades WHERE exit_ts IS NOT NULL GROUP BY pair ORDER BY value DESC",
    "percent",
    TH_WIN,
)
bargauge(
    "Win Rate by Hour-of-Day (UTC)",
    8,
    7,
    f"SELECT lpad(({_HOUR})::text,2,'0')||':00' AS metric, ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS value FROM trades WHERE exit_ts IS NOT NULL GROUP BY {_HOUR} ORDER BY {_HOUR}",
    "percent",
    TH_WIN,
)
table(
    "Win Rate by Confidence Band (signal→trade)",
    12,
    8,
    "SELECT band, COUNT(*) AS trades, ROUND(100.0*AVG(w),1) AS win_rate, ROUND(SUM(p),4) AS net_pnl FROM (SELECT CASE WHEN s.confidence>=0.75 THEN '≥0.75 (full bucket)' WHEN s.confidence>=0.55 THEN '0.55–0.74 (half)' ELSE '<0.55' END AS band, (t.pnl_net_usdt>0)::int AS w, t.pnl_net_usdt AS p FROM signals s JOIN trades t ON s.trade_id=t.id WHERE t.exit_ts IS NOT NULL) x GROUP BY band ORDER BY band DESC",
    overrides=[col_override("net_pnl", "currencyUSD"), col_override("win_rate", "percent", bg=False, th=TH_WIN)],
)
table(
    "Win Rate by Regime (signal→trade)",
    12,
    8,
    "SELECT s.regime, COUNT(t.id) AS trades, ROUND(100.0*AVG((t.pnl_net_usdt>0)::int),1) AS win_rate, ROUND(SUM(t.pnl_net_usdt),4) AS net_pnl FROM signals s JOIN trades t ON s.trade_id=t.id WHERE t.exit_ts IS NOT NULL GROUP BY s.regime ORDER BY net_pnl NULLS LAST",
    overrides=[col_override("net_pnl", "currencyUSD"), col_override("win_rate", "percent", bg=False, th=TH_WIN)],
)

# ════════════════════════════════════════════════════════════════════════════
# 1b — Position Sizing & Compounding (equity-scaled sizing, signal/sizing.py)
# ════════════════════════════════════════════════════════════════════════════
section("🪙 Position Sizing & Compounding (equity-scaled)")
stat("Avg Position Size", 4, 4, f"SELECT ROUND(AVG(size_usdt),2) AS v {_CLOSED}", "currencyUSD", TH_BLUE, decimals=2)
stat(
    "Latest Position Size",
    4,
    4,
    "SELECT size_usdt AS v FROM trades WHERE exit_ts IS NOT NULL ORDER BY entry_ts DESC LIMIT 1",
    "currencyUSD",
    TH_BLUE,
    graph="area",
    decimals=2,
)
stat(
    "Largest Position Size",
    4,
    4,
    f"SELECT COALESCE(MAX(size_usdt),0) AS v {_CLOSED}",
    "currencyUSD",
    TH_GREEN,
    decimals=2,
)
stat(
    "Smallest Position Size",
    4,
    4,
    "SELECT COALESCE(MIN(size_usdt),0) AS v FROM trades WHERE exit_ts IS NOT NULL AND size_usdt>0",
    "currencyUSD",
    TH_BLUE,
    decimals=2,
)
stat(
    "Avg Notional (size×lev)",
    4,
    4,
    f"SELECT ROUND(AVG(notional_usdt),2) AS v {_CLOSED}",
    "currencyUSD",
    TH_BLUE,
    decimals=2,
)
stat(
    "Avg Bucket Equity (now)",
    4,
    4,
    "SELECT ROUND(AVG(eq),2) AS v FROM (SELECT DISTINCT ON (bot_id) bucket_balance_after AS eq FROM trades WHERE exit_ts IS NOT NULL ORDER BY bot_id, exit_ts DESC) x",
    "currencyUSD",
    TH_PNL,
    decimals=2,
)
ts(
    "Position Size Over Time (per strategy)",
    12,
    8,
    f'SELECT to_timestamp(entry_ts/1000.0) AS "time", {_TOK} AS metric, AVG(size_usdt) AS size_usdt FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(entry_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY 1,2 ORDER BY 1',
    unit="currencyUSD",
    fmt="time_series",
)
ts(
    "Bucket Equity Over Time (per strategy, shows compounding)",
    12,
    8,
    f'SELECT to_timestamp(exit_ts/1000.0) AS "time", {_TOK} AS metric, AVG(bucket_balance_after) AS equity FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY 1,2 ORDER BY 1',
    unit="currencyUSD",
    fmt="time_series",
)
histogram("Position Size ($) — distribution", 8, 7, f"SELECT size_usdt {_CLOSED}", "currencyUSD", color="green")
ts(
    "Position Size vs Notional (avg over time)",
    8,
    7,
    'SELECT to_timestamp(entry_ts/1000.0) AS "time", AVG(size_usdt) AS margin, AVG(notional_usdt) AS notional FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(entry_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY 1 ORDER BY 1',
    unit="currencyUSD",
    fmt="time_series",
)
table(
    "Per-Strategy Sizing",
    8,
    7,
    f"SELECT {_TOK} AS strategy, COUNT(*) AS trades, ROUND(AVG(size_usdt),2) AS avg_size, ROUND(MAX(size_usdt),2) AS max_size, ROUND(AVG(notional_usdt),2) AS avg_notional, ROUND(AVG(leverage),1) AS leverage {_CLOSED} GROUP BY {_TOK} ORDER BY trades DESC",
    overrides=[
        col_override("avg_size", "currencyUSD", bg=False),
        col_override("max_size", "currencyUSD", bg=False),
        col_override("avg_notional", "currencyUSD", bg=False),
    ],
)
stat(
    "Bucket-Exhausted Stops (size→0)",
    8,
    4,
    "SELECT COUNT(*) AS v FROM events WHERE category='signal' AND message LIKE 'signal_rejected:bucket_exhausted%'",
    thresholds=TH_BLUE,
)

# ════════════════════════════════════════════════════════════════════════════
# 1b — Trailing-Close (ratchet exit) — live A/B: trailing variants vs fixed
# ════════════════════════════════════════════════════════════════════════════
# Trailing bots carry a '_t'-suffixed strategy token (e.g. ride_t); fixed don't.
_EXIT_POLICY = "CASE WHEN split_part(bot_id,'-',4) ~ '_t$' THEN 'trailing' ELSE 'fixed' END"
_BASE_STRAT = "regexp_replace(split_part(bot_id,'-',4),'_t$','')"
_TRAIL_F = "exit_ts IS NOT NULL AND split_part(bot_id,'-',4) ~ '_t$'"
_FIXED_F = "exit_ts IS NOT NULL AND split_part(bot_id,'-',4) !~ '_t$'"
section("🪤 Trailing-Close (ratchet exit) — live A/B vs fixed TP")
stat(
    "Trailing-Stop Exit Rate",
    4,
    4,
    f"SELECT ROUND(100.0*AVG((close_reason='trailing_stop')::int),1) AS v FROM trades WHERE {_TRAIL_F}",
    "percent",
    TH_WIN,
    decimals=1,
)
stat("Trailing Trades (closed)", 4, 4, f"SELECT COUNT(*) AS v FROM trades WHERE {_TRAIL_F}", thresholds=TH_BLUE)
stat(
    "Avg Net PnL — Trailing",
    4,
    4,
    f"SELECT ROUND(AVG(pnl_net_usdt),4) AS v FROM trades WHERE {_TRAIL_F}",
    "currencyUSD",
    TH_PNL,
    decimals=4,
)
stat(
    "Avg Net PnL — Fixed",
    4,
    4,
    f"SELECT ROUND(AVG(pnl_net_usdt),4) AS v FROM trades WHERE {_FIXED_F}",
    "currencyUSD",
    TH_PNL,
    decimals=4,
)
stat(
    "Avg Hold — Trailing (candles)",
    4,
    4,
    f"SELECT ROUND(AVG(hold_candles),1) AS v FROM trades WHERE {_TRAIL_F}",
    thresholds=TH_BLUE,
    decimals=1,
)
stat(
    "Avg Hold — Fixed (candles)",
    4,
    4,
    f"SELECT ROUND(AVG(hold_candles),1) AS v FROM trades WHERE {_FIXED_F}",
    thresholds=TH_BLUE,
    decimals=1,
)
stat(
    "Avg Realized R — Trailing",
    4,
    4,
    f"SELECT ROUND(AVG((CASE WHEN direction='long' THEN exit_price-entry_price ELSE entry_price-exit_price END)/NULLIF(ABS(entry_price-sl_price),0)),3) AS v FROM trades WHERE {_TRAIL_F}",
    "short",
    TH_PNL,
    decimals=3,
)
stat(
    "Avg Realized R — Fixed",
    4,
    4,
    f"SELECT ROUND(AVG((CASE WHEN direction='long' THEN exit_price-entry_price ELSE entry_price-exit_price END)/NULLIF(ABS(entry_price-sl_price),0)),3) AS v FROM trades WHERE {_FIXED_F}",
    "short",
    TH_PNL,
    decimals=3,
)
table(
    "Trailing vs Fixed — by strategy (live A/B)",
    12,
    8,
    f"SELECT {_BASE_STRAT} AS strategy, {_EXIT_POLICY} AS exit_policy, COUNT(*) AS trades, "
    f"ROUND(100.0*AVG((pnl_net_usdt>0)::int),1) AS win_pct, ROUND(AVG(pnl_net_usdt),4) AS avg_net, "
    f"ROUND(SUM(pnl_net_usdt),3) AS net, ROUND(AVG(hold_candles),1) AS avg_hold, "
    f"ROUND(100.0*AVG((close_reason='trailing_stop')::int),1) AS trail_exit_pct "
    f"{_CLOSED} GROUP BY 1,2 ORDER BY 1,2",
    overrides=[
        col_override("avg_net", "currencyUSD", bg=True, th=TH_PNL),
        col_override("net", "currencyUSD", bg=True, th=TH_PNL),
        col_override("win_pct", "percent", bg=False),
        col_override("trail_exit_pct", "percent", bg=False),
    ],
)
bargauge(
    "Close Reasons — Trailing variants",
    12,
    8,
    f"SELECT close_reason AS metric, COUNT(*) AS value FROM trades WHERE {_TRAIL_F} GROUP BY close_reason ORDER BY value DESC",
)
ts(
    "Avg Net PnL/trade Over Time — Trailing vs Fixed",
    12,
    7,
    f'SELECT to_timestamp(exit_ts/1000.0) AS "time", {_EXIT_POLICY} AS metric, AVG(pnl_net_usdt) AS avg_net FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY 1,2 ORDER BY 1',
    unit="currencyUSD",
    fmt="time_series",
)
ts(
    "Trailing-Stop Exits Over Time",
    12,
    7,
    f"SELECT to_timestamp(exit_ts/1000.0) AS \"time\", COUNT(*) FILTER (WHERE close_reason='trailing_stop') AS trailing_stops FROM trades WHERE {_TRAIL_F} AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY 1 ORDER BY 1",
    unit="short",
    fmt="time_series",
    bars=True,
    color="green",
)

# ════════════════════════════════════════════════════════════════════════════
# 2 — Equity, Drawdown & Returns
# ════════════════════════════════════════════════════════════════════════════
section("📈 Equity, Drawdown & Returns")
ts(
    "Account Balance Over Time (equity curve)",
    12,
    8,
    f'SELECT q.t AS "time", q.equity FROM ({_EQUITY}) q WHERE q.t BETWEEN $__timeFrom() AND $__timeTo() ORDER BY q.exit_ts',
)
ts(
    "Cumulative PnL per Bot",
    12,
    8,
    'SELECT to_timestamp(exit_ts/1000.0) AS "time", bot_id AS metric, SUM(pnl_net_usdt) OVER (PARTITION BY bot_id ORDER BY exit_ts) AS cumulative_pnl FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() ORDER BY exit_ts',
    fmt="time_series",
)
ts(
    "Drawdown (equity − peak, from $100 start)",
    12,
    7,
    f'SELECT q.t AS "time", q.equity - GREATEST(MAX(q.equity) OVER (ORDER BY q.exit_ts), 100) AS drawdown FROM ({_EQUITY}) q WHERE q.t BETWEEN $__timeFrom() AND $__timeTo() ORDER BY q.exit_ts',
    color="red",
    fill=40,
)
ts(
    "Underwater % (drawdown as % of peak)",
    12,
    7,
    f'SELECT q.t AS "time", (q.equity - GREATEST(MAX(q.equity) OVER (ORDER BY q.exit_ts),100)) / GREATEST(MAX(q.equity) OVER (ORDER BY q.exit_ts),100) * 100 AS underwater_pct FROM ({_EQUITY}) q WHERE q.t BETWEEN $__timeFrom() AND $__timeTo() ORDER BY q.exit_ts',
    unit="percent",
    color="orange",
    fill=40,
)
stat(
    "Max Drawdown",
    6,
    4,
    f"SELECT MIN(equity - GREATEST(peak, 100)) AS max_dd FROM (SELECT equity, MAX(equity) OVER (ORDER BY exit_ts) AS peak FROM ({_EQUITY}) a) b",
    "currencyUSD",
    TH_DD,
)
stat(
    "Current Drawdown",
    6,
    4,
    f"SELECT (SELECT 100 + COALESCE(SUM(pnl_net_usdt),0) FROM trades WHERE exit_ts IS NOT NULL) - GREATEST((SELECT COALESCE(MAX(equity),100) FROM ({_EQUITY}) z), 100) AS current_dd",
    "currencyUSD",
    TH_DD,
)
stat(
    "Peak Equity",
    6,
    4,
    f"SELECT GREATEST(COALESCE(MAX(equity),100),100) AS v FROM ({_EQUITY}) q",
    "currencyUSD",
    TH_GREEN,
)
stat(
    "Best Day (UTC)",
    3,
    4,
    "SELECT COALESCE(MAX(d),0) AS v FROM (SELECT SUM(pnl_net_usdt) d FROM trades WHERE exit_ts IS NOT NULL GROUP BY (exit_ts/86400000)) x",
    "currencyUSD",
    TH_GREEN,
    decimals=4,
)
stat(
    "Worst Day (UTC)",
    3,
    4,
    "SELECT COALESCE(MIN(d),0) AS v FROM (SELECT SUM(pnl_net_usdt) d FROM trades WHERE exit_ts IS NOT NULL GROUP BY (exit_ts/86400000)) x",
    "currencyUSD",
    TH_RED,
    decimals=4,
)
ts(
    "Net PnL per Trade",
    24,
    6,
    'SELECT to_timestamp(exit_ts/1000.0) AS "time", pnl_net_usdt AS net_pnl FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() ORDER BY exit_ts',
    bars=True,
    fill=70,
)
ts(
    "Daily Net PnL (UTC)",
    12,
    7,
    'SELECT to_timestamp((exit_ts/86400000)*86400) AS "time", SUM(pnl_net_usdt) AS daily_pnl FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY (exit_ts/86400000) ORDER BY 1',
    bars=True,
    fill=70,
)
ts(
    "Rolling Avg PnL — last 20 trades",
    12,
    7,
    'SELECT to_timestamp(exit_ts/1000.0) AS "time", AVG(pnl_net_usdt) OVER (ORDER BY exit_ts ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS rolling_avg_pnl FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() ORDER BY exit_ts',
    tline=0,
)
ts(
    "Exposure Over Time — open notional & margin deployed",
    24,
    7,
    'SELECT q.t AS "time", q.notional_exposure AS "notional_$", q.margin_used AS "margin_$" FROM (SELECT ts, to_timestamp(ts/1000.0) AS t, SUM(nd) OVER (ORDER BY ts, ord) AS notional_exposure, SUM(md) OVER (ORDER BY ts, ord) AS margin_used FROM (SELECT entry_ts AS ts, notional_usdt AS nd, size_usdt AS md, 0 AS ord FROM trades UNION ALL SELECT exit_ts AS ts, -notional_usdt AS nd, -size_usdt AS md, 1 AS ord FROM trades WHERE exit_ts IS NOT NULL) e) q WHERE q.t BETWEEN $__timeFrom() AND $__timeTo() ORDER BY q.ts',
    unit="currencyUSD",
    step=True,
    fill=15,
)

# ════════════════════════════════════════════════════════════════════════════
# 3 — PnL Distribution & Outcomes
# ════════════════════════════════════════════════════════════════════════════
section("💵 PnL Distribution & Outcomes")
histogram("Net PnL per Trade ($) — distribution", 8, 8, f"SELECT pnl_net_usdt AS net_pnl {_CLOSED}", "currencyUSD")
histogram("PnL % per Trade — distribution", 8, 8, f"SELECT pnl_pct AS pnl_pct {_CLOSED}", "percent", color="purple")
histogram(
    "Realized R-multiple — distribution",
    8,
    8,
    f"SELECT (CASE WHEN direction='long' THEN exit_price-entry_price ELSE entry_price-exit_price END)/NULLIF(ABS(entry_price-sl_price),0) AS r_multiple {_CLOSED}",
    color="green",
)
pie(
    "Wins vs Losses",
    6,
    8,
    "SELECT 'Wins' AS metric, COUNT(*) AS value FROM trades WHERE exit_ts IS NOT NULL AND pnl_net_usdt>0 UNION ALL SELECT 'Losses', COUNT(*) FROM trades WHERE exit_ts IS NOT NULL AND pnl_net_usdt<=0",
)
bargauge(
    "Close Reasons",
    6,
    8,
    "SELECT close_reason AS metric, COUNT(*) AS value FROM trades WHERE exit_ts IS NOT NULL GROUP BY close_reason ORDER BY value DESC",
)
table(
    "PnL Percentiles ($/trade)",
    12,
    8,
    f"SELECT ROUND(MIN(pnl_net_usdt),4) AS min, ROUND(percentile_cont(0.10) WITHIN GROUP (ORDER BY pnl_net_usdt)::numeric,4) AS p10, ROUND(percentile_cont(0.25) WITHIN GROUP (ORDER BY pnl_net_usdt)::numeric,4) AS p25, ROUND(percentile_cont(0.50) WITHIN GROUP (ORDER BY pnl_net_usdt)::numeric,4) AS median, ROUND(percentile_cont(0.75) WITHIN GROUP (ORDER BY pnl_net_usdt)::numeric,4) AS p75, ROUND(percentile_cont(0.90) WITHIN GROUP (ORDER BY pnl_net_usdt)::numeric,4) AS p90, ROUND(MAX(pnl_net_usdt),4) AS max {_CLOSED}",
)
bargauge(
    "Avg Win vs Avg Loss ($)",
    12,
    8,
    f"SELECT 'Avg Win' AS metric, ROUND(AVG(pnl_net_usdt) FILTER (WHERE pnl_net_usdt>0),4) AS value {_CLOSED} UNION ALL SELECT 'Avg Loss', ROUND(AVG(pnl_net_usdt) FILTER (WHERE pnl_net_usdt<=0),4) {_CLOSED}",
    "currencyUSD",
    TH_PNL,
)
table(
    "🥇 Best 10 Trades",
    12,
    8,
    "SELECT to_timestamp(exit_ts/1000.0) AS exit_time, pair, direction AS dir, ROUND(pnl_net_usdt,4) AS net_pnl, ROUND(pnl_pct,2) AS pnl_pct, close_reason, hold_candles AS hold, pattern FROM trades WHERE exit_ts IS NOT NULL ORDER BY pnl_net_usdt DESC LIMIT 10",
    overrides=[col_override("net_pnl", "currencyUSD"), col_override("pnl_pct", "percent", bg=False)],
)
table(
    "🪦 Worst 10 Trades",
    12,
    8,
    "SELECT to_timestamp(exit_ts/1000.0) AS exit_time, pair, direction AS dir, ROUND(pnl_net_usdt,4) AS net_pnl, ROUND(pnl_pct,2) AS pnl_pct, close_reason, hold_candles AS hold, pattern FROM trades WHERE exit_ts IS NOT NULL ORDER BY pnl_net_usdt ASC LIMIT 10",
    overrides=[col_override("net_pnl", "currencyUSD"), col_override("pnl_pct", "percent", bg=False)],
)

# ════════════════════════════════════════════════════════════════════════════
# 4b — Points Scoreboard (docs/13-points-framework.md — the primary scoreboard
# since 2026-07-09: gross bps of entry price + win rate, fee-free by design).
# Daily targets (§6.2): points win ≥ 65% (standard 70), expectancy > 0
# (maker-viable ≥ +4 bps), aggregate ≥ +100 bps/day, ≥ 30 closed/day.
# ════════════════════════════════════════════════════════════════════════════
section("📏 Points Scoreboard (gross bps — docs/13)")
_PTS = (
    "CASE WHEN direction='long' THEN (exit_price-entry_price)/entry_price*10000.0 "
    "ELSE (entry_price-exit_price)/entry_price*10000.0 END"
)
TH_PWIN = {
    "mode": "absolute",
    "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 65}, {"color": "green", "value": 70}],
}
TH_PTS_EXP = {
    "mode": "absolute",
    "steps": [
        {"color": "red", "value": None},
        {"color": "orange", "value": 0},
        {"color": "green", "value": 4},  # ≥ +4 bps gross = maker-viable shelf
    ],
}
TH_PTS_DAY = {
    "mode": "absolute",
    "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 0}, {"color": "green", "value": 100}],
}
stat(
    "Points Win Rate (all closed)",
    4,
    5,
    f"SELECT ROUND(100.0*AVG(({_PTS}>0)::int),1) FROM trades WHERE exit_ts IS NOT NULL",
    unit="percent",
    thresholds=TH_PWIN,
)
stat(
    "Points Expectancy (gross bps/trade)",
    4,
    5,
    f"SELECT ROUND(AVG({_PTS})::numeric,2) FROM trades WHERE exit_ts IS NOT NULL",
    thresholds=TH_PTS_EXP,
    decimals=2,
)
stat(
    "Aggregate Points Today (UTC)",
    4,
    5,
    f"SELECT COALESCE(ROUND(SUM({_PTS})::numeric,1),0) FROM trades WHERE exit_ts >= (extract(epoch from date_trunc('day', now() AT TIME ZONE 'utc'))*1000)::bigint",
    thresholds=TH_PTS_DAY,
    decimals=1,
)
stat(
    "Closed Trades Today (UTC)",
    4,
    5,
    "SELECT COUNT(*) FROM trades WHERE exit_ts >= (extract(epoch from date_trunc('day', now() AT TIME ZONE 'utc'))*1000)::bigint",
    thresholds={
        "mode": "absolute",
        "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 10}, {"color": "green", "value": 30}],
    },
)
stat(
    "exp_hiwin Points Win %",
    4,
    5,
    f"SELECT ROUND(100.0*AVG(({_PTS}>0)::int),1) FROM trades WHERE exit_ts IS NOT NULL AND bot_id LIKE '%exp_hiwin%'",
    unit="percent",
    thresholds=TH_PWIN,
)
stat(
    "exp_hiwin Closed Trades (→100-trade live leg)",
    4,
    5,
    "SELECT COUNT(*) FROM trades WHERE exit_ts IS NOT NULL AND bot_id LIKE '%exp_hiwin%'",
    thresholds={
        "mode": "absolute",
        "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 30}, {"color": "green", "value": 100}],
    },
)
ts(
    "Rolling Points Win-Rate — last 20 vs cumulative (70% = §6.3 program target)",
    24,
    7,
    f'SELECT q.t AS "time", q.r20 AS "rolling_20", q.cum AS "cumulative" FROM (SELECT exit_ts, to_timestamp(exit_ts/1000.0) AS t, 100.0*AVG(({_PTS}>0)::int) OVER (ORDER BY exit_ts ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS r20, 100.0*AVG(({_PTS}>0)::int) OVER (ORDER BY exit_ts) AS cum FROM trades WHERE exit_ts IS NOT NULL) q WHERE q.t BETWEEN $__timeFrom() AND $__timeTo() ORDER BY q.exit_ts',
    unit="percent",
    mx=100,
    tline=70,
    fill=8,
)
bargauge(
    "Points Expectancy by Lead (gross bps/trade)",
    12,
    8,
    f"SELECT split_part(bot_id,'-',4) AS metric, ROUND(AVG({_PTS})::numeric,2) AS value FROM trades WHERE exit_ts IS NOT NULL GROUP BY 1 ORDER BY value DESC",
    thresholds=TH_PTS_EXP,
)
table(
    "exp_hiwin Cohort — per-arm points scoreboard (live leg of §6.3)",
    12,
    8,
    f"SELECT split_part(bot_id,'-',4) AS arm, COUNT(*) AS n, ROUND(100.0*AVG(({_PTS}>0)::int),1) AS pts_win, ROUND(AVG({_PTS})::numeric,2) AS avg_bps, ROUND(SUM({_PTS})::numeric,1) AS total_bps, ROUND(SUM(pnl_net_usdt),4) AS net_usd FROM trades WHERE exit_ts IS NOT NULL AND bot_id LIKE '%exp_hiwin%' GROUP BY 1 ORDER BY avg_bps DESC NULLS LAST",
    overrides=[
        col_override("pts_win", "percent", bg=False, th=TH_PWIN),
        col_override("net_usd", "currencyUSD"),
    ],
)

# ════════════════════════════════════════════════════════════════════════════
# 5 — Risk, Streaks & Expectancy
# ════════════════════════════════════════════════════════════════════════════
section("⚖️ Risk, Streaks & Expectancy")
_STREAK = (
    "SELECT t.pnl_net_usdt>0 AS win, ROW_NUMBER() OVER (ORDER BY t.exit_ts) "
    "- ROW_NUMBER() OVER (PARTITION BY t.pnl_net_usdt>0 ORDER BY t.exit_ts) AS grp "
    "FROM trades t WHERE t.exit_ts IS NOT NULL"
)
stat(
    "Max Consecutive Losses",
    4,
    4,
    f"SELECT COALESCE(MAX(c),0) AS v FROM (SELECT COUNT(*) c FROM ({_STREAK}) s WHERE win=false GROUP BY grp) z",
    thresholds=TH_RED,
)
stat(
    "Max Consecutive Wins",
    4,
    4,
    f"SELECT COALESCE(MAX(c),0) AS v FROM (SELECT COUNT(*) c FROM ({_STREAK}) s WHERE win=true GROUP BY grp) z",
    thresholds=TH_GREEN,
)
stat(
    "Profit Factor",
    4,
    4,
    "SELECT COALESCE(SUM(pnl_net_usdt) FILTER (WHERE pnl_net_usdt>0)/NULLIF(-SUM(pnl_net_usdt) FILTER (WHERE pnl_net_usdt<=0),0),0) AS v FROM trades WHERE exit_ts IS NOT NULL",
    thresholds=TH_PF,
    decimals=2,
)
stat(
    "Payoff Ratio (avg win / avg loss)",
    4,
    4,
    "SELECT COALESCE(AVG(pnl_net_usdt) FILTER (WHERE pnl_net_usdt>0)/NULLIF(-AVG(pnl_net_usdt) FILTER (WHERE pnl_net_usdt<=0),0),0) AS v FROM trades WHERE exit_ts IS NOT NULL",
    thresholds=TH_PF,
    decimals=2,
)
stat(
    "Expectancy in R",
    4,
    4,
    f"SELECT ROUND(AVG((CASE WHEN direction='long' THEN exit_price-entry_price ELSE entry_price-exit_price END)/NULLIF(ABS(entry_price-sl_price),0)),3) AS v {_CLOSED}",
    thresholds=TH_PNL,
    decimals=3,
)
stat(
    "Largest Loss as % of Capital",
    4,
    4,
    "SELECT ROUND(ABS(LEAST(MIN(pnl_net_usdt),0))/100.0*100,3) AS v FROM trades WHERE exit_ts IS NOT NULL",
    "percent",
    TH_DD,
    decimals=3,
)
bargauge(
    "Realized R-multiple Distribution",
    12,
    8,
    f"SELECT bucket AS metric, COUNT(*) AS value FROM (SELECT CASE WHEN r<=-1 THEN '1: ≤ -1R (full stop)' WHEN r<0 THEN '2: -1..0R' WHEN r<1 THEN '3: 0..1R' WHEN r<2 THEN '4: 1..2R' ELSE '5: ≥ 2R' END AS bucket FROM (SELECT (CASE WHEN direction='long' THEN exit_price-entry_price ELSE entry_price-exit_price END)/NULLIF(ABS(entry_price-sl_price),0) AS r {_CLOSED}) x WHERE r IS NOT NULL) y GROUP BY bucket ORDER BY bucket",
)
table(
    "Trade Stats Summary",
    12,
    8,
    "SELECT ROUND(AVG(pnl_net_usdt) FILTER (WHERE pnl_net_usdt>0),4) AS avg_win, ROUND(AVG(pnl_net_usdt) FILTER (WHERE pnl_net_usdt<=0),4) AS avg_loss, ROUND(MAX(pnl_net_usdt),4) AS largest_win, ROUND(MIN(pnl_net_usdt),4) AS largest_loss, ROUND(AVG(hold_candles),1) AS avg_hold_candles, ROUND(COALESCE(SUM(pnl_net_usdt) FILTER (WHERE pnl_net_usdt>0)/NULLIF(-SUM(pnl_net_usdt) FILTER (WHERE pnl_net_usdt<=0),0),0),2) AS profit_factor FROM trades WHERE exit_ts IS NOT NULL",
    overrides=[
        col_override("avg_win", "currencyUSD"),
        col_override("avg_loss", "currencyUSD"),
        col_override("largest_win", "currencyUSD"),
        col_override("largest_loss", "currencyUSD"),
    ],
)

# ════════════════════════════════════════════════════════════════════════════
# 6 — Trade Quality (R-multiple, excursion, duration)
# ════════════════════════════════════════════════════════════════════════════
section("📐 Trade Quality — R-multiple, excursion, duration")
stat(
    "TP-hit Rate",
    4,
    4,
    f"SELECT 100.0*AVG((close_reason='take_profit')::int) AS v {_CLOSED}",
    "percent",
    TH_WIN,
    decimals=1,
)
stat(
    "SL-hit Rate",
    4,
    4,
    f"SELECT 100.0*AVG((close_reason='stop_loss')::int) AS v {_CLOSED}",
    "percent",
    TH_BLUE,
    decimals=1,
)
stat(
    "Timeout Rate",
    4,
    4,
    f"SELECT 100.0*AVG((close_reason='timeout')::int) AS v {_CLOSED}",
    "percent",
    TH_BLUE,
    decimals=1,
)
stat(
    "Liquidation Rate",
    4,
    4,
    f"SELECT 100.0*AVG((close_reason='liquidated')::int) AS v {_CLOSED}",
    "percent",
    TH_ZERO_BAD,
    decimals=1,
)
stat(
    "Avg Realized R (per trade)",
    4,
    4,
    f"SELECT ROUND(AVG((CASE WHEN direction='long' THEN exit_price-entry_price ELSE entry_price-exit_price END)/NULLIF(ABS(entry_price-sl_price),0)),3) AS v {_CLOSED}",
    "short",
    TH_PNL,
)
stat(
    "Avg Planned R/R (target)",
    4,
    4,
    f"SELECT ROUND(AVG(ABS(tp_price-entry_price)/NULLIF(ABS(entry_price-sl_price),0)),2) AS v {_CLOSED}",
    "short",
    TH_BLUE,
)
stat(
    "Avg MFE % (best excursion)",
    4,
    4,
    f"SELECT ROUND(AVG({_MFE}),3) AS v FROM trades t {_PX} WHERE t.exit_ts IS NOT NULL",
    "percent",
    TH_GREEN,
)
stat(
    "Avg MAE % (worst excursion)",
    4,
    4,
    f"SELECT ROUND(AVG({_MAE}),3) AS v FROM trades t {_PX} WHERE t.exit_ts IS NOT NULL",
    "percent",
    TH_RED,
)
stat("Avg Hold (candles)", 4, 4, f"SELECT ROUND(AVG(hold_candles),2) AS v {_CLOSED}", thresholds=TH_BLUE)
stat("Avg Hold (minutes)", 4, 4, f"SELECT ROUND(AVG((exit_ts-entry_ts)/60000.0),1) AS v {_CLOSED}", "m", TH_BLUE)
stat(
    "Avg Fees ($/trade)",
    4,
    4,
    f"SELECT ROUND(AVG(fee_entry_usdt+COALESCE(fee_exit_usdt,0)),4) AS v {_CLOSED}",
    "currencyUSD",
    TH_BLUE,
)
stat(
    "Avg Liq Distance %",
    4,
    4,
    f"SELECT ROUND(AVG(ABS(entry_price-liquidation_price)/entry_price*100),2) AS v {_CLOSED}",
    "percent",
    {
        "mode": "absolute",
        "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1.5}, {"color": "green", "value": 3}],
    },
)
histogram("Hold Duration (candles) — distribution", 8, 7, f"SELECT hold_candles {_CLOSED}", color="blue")
histogram(
    "MFE % — distribution",
    8,
    7,
    f"SELECT {_MFE} AS mfe_pct FROM trades t {_PX} WHERE t.exit_ts IS NOT NULL",
    "percent",
    color="green",
)
histogram(
    "MAE % — distribution",
    8,
    7,
    f"SELECT {_MAE} AS mae_pct FROM trades t {_PX} WHERE t.exit_ts IS NOT NULL",
    "percent",
    color="red",
)
table(
    "Excursion by Close Reason — did winners run / losers get stopped on noise?",
    24,
    8,
    f"SELECT t.close_reason, COUNT(*) AS trades, ROUND(AVG({_MFE}),3) AS avg_mfe_pct, ROUND(AVG({_MAE}),3) AS avg_mae_pct, ROUND(AVG((CASE WHEN t.direction='long' THEN t.exit_price-t.entry_price ELSE t.entry_price-t.exit_price END)/{_RISK}),2) AS avg_realized_r, ROUND(AVG(t.hold_candles),1) AS avg_hold, ROUND(SUM(t.pnl_net_usdt),4) AS net_pnl FROM trades t {_PX} WHERE t.exit_ts IS NOT NULL GROUP BY t.close_reason ORDER BY trades DESC",
    overrides=[
        col_override("avg_mfe_pct", "percent", bg=False),
        col_override("avg_mae_pct", "percent", bg=False),
        col_override("avg_realized_r", "short"),
        col_override("net_pnl", "currencyUSD"),
    ],
)

# ════════════════════════════════════════════════════════════════════════════
# 7 — Signal Funnel & Rejections  (why signals do / don't fire — from events)
# ════════════════════════════════════════════════════════════════════════════
section("🔬 Signal Funnel & Rejections")
stat("Signals Fired (all-time)", 4, 4, "SELECT COUNT(*) AS v FROM signals WHERE outcome='fired'", thresholds=TH_GREEN)
stat(
    "Signals Rejected (events)",
    4,
    4,
    "SELECT COUNT(*) AS v FROM events WHERE category='signal' AND message LIKE 'signal_rejected:%'",
    thresholds=TH_BLUE,
)
stat(
    "Risk Rejections",
    4,
    4,
    "SELECT COUNT(*) AS v FROM events WHERE category='risk' AND message LIKE 'risk_rejected%'",
    thresholds=TH_BLUE,
)
stat(
    "Orders Placed",
    4,
    4,
    "SELECT COUNT(*) AS v FROM events WHERE category='order' AND message LIKE 'order_placed%'",
    thresholds=TH_GREEN,
)
stat(
    "Fire Rate (fired / evaluated)",
    4,
    4,
    "SELECT ROUND(100.0*(SELECT COUNT(*) FROM signals WHERE outcome='fired')::numeric / NULLIF((SELECT COUNT(*) FROM signals WHERE outcome='fired') + (SELECT COUNT(*) FROM events WHERE category='signal' AND message LIKE 'signal_rejected:%'),0),3) AS v",
    "percent",
    TH_BLUE,
    decimals=3,
)
stat(
    "Avg Confidence (fired)",
    4,
    4,
    "SELECT ROUND(AVG(confidence),3) AS v FROM signals WHERE outcome='fired'",
    thresholds=TH_BLUE,
    decimals=3,
)
bargauge(
    "Rejection Funnel — by reason (all-time)",
    12,
    8,
    "SELECT split_part(message,':',2) AS metric, COUNT(*) AS value FROM events WHERE category='signal' AND message LIKE 'signal_rejected:%' GROUP BY metric ORDER BY value DESC LIMIT 15",
)
bargauge(
    "Signal-stage Layer Pass-rate (fired signals)",
    12,
    8,
    "SELECT m AS metric, ROUND(100.0*AVG(p),1) AS value FROM (SELECT unnest(ARRAY['1 regime','2 trend','3 momentum','4 volume']) AS m, unnest(ARRAY[layer_regime,layer_trend,layer_momentum,layer_volume]) AS p FROM signals) x GROUP BY m ORDER BY m",
)
ts(
    "Signals Over Time — fired vs rejected (hourly)",
    24,
    7,
    "SELECT to_timestamp((ts/3600000)*3600) AS \"time\", CASE WHEN message LIKE 'signal_rejected:%' THEN 'rejected' ELSE 'other' END AS metric, COUNT(*) AS n FROM events WHERE category='signal' AND to_timestamp(ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY (ts/3600000), metric ORDER BY 1",
    unit="short",
    fmt="time_series",
    bars=True,
    fill=60,
)
table(
    "Fired Signals — recent (pattern · confidence · regime · layers)",
    24,
    7,
    "SELECT to_timestamp(ts/1000.0) AS time, bot_id, pair, pattern, direction AS dir, ROUND(confidence,3) AS conf, regime, layers_passed AS layers, outcome FROM signals ORDER BY ts DESC LIMIT 30",
    overrides=[col_override("conf", "short", bg=False, th=TH_BLUE)],
)

# ════════════════════════════════════════════════════════════════════════════
# 8 — Pattern Analytics
# ════════════════════════════════════════════════════════════════════════════
section("🧩 Pattern Analytics")
table(
    "PnL by Pattern",
    8,
    8,
    "SELECT pattern, COUNT(*) FILTER (WHERE exit_ts IS NOT NULL) AS closed, ROUND(100.0*SUM((pnl_net_usdt>0)::int)/NULLIF(COUNT(*) FILTER (WHERE exit_ts IS NOT NULL),0),1) AS win_rate, ROUND(AVG(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS avg_pnl, ROUND(SUM(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS net_pnl FROM trades GROUP BY pattern ORDER BY net_pnl NULLS LAST",
    overrides=[
        col_override("net_pnl", "currencyUSD"),
        col_override("avg_pnl", "currencyUSD"),
        col_override("win_rate", "percent", bg=False, th=TH_WIN),
    ],
)
bargauge(
    "Signals Fired by Pattern",
    8,
    8,
    "SELECT pattern AS metric, COUNT(*) AS value FROM signals WHERE outcome='fired' GROUP BY pattern ORDER BY value DESC",
)
histogram(
    "Signal Confidence — distribution (fired)",
    8,
    8,
    "SELECT confidence FROM signals WHERE outcome='fired'",
    color="purple",
)
table(
    "Pattern Memory (learned win-rates by pattern · dir · session · regime)",
    24,
    8,
    "SELECT pattern, direction AS dir, session, regime, sample_count AS n, win_count AS wins, ROUND(win_rate*100,1) AS win_rate, ROUND(avg_pnl_pct,3) AS avg_pnl_pct, to_timestamp(last_updated/1000.0) AS updated FROM pattern_memory ORDER BY sample_count DESC LIMIT 50",
    overrides=[
        col_override("win_rate", "percent", bg=False, th=TH_WIN),
        col_override("avg_pnl_pct", "percent", bg=False),
    ],
)

# ════════════════════════════════════════════════════════════════════════════
# 9 — Regime Analytics  (regime lives on signals; joined to trades for PnL)
# ════════════════════════════════════════════════════════════════════════════
section("🌊 Regime Analytics")
pie(
    "Signals by Regime",
    8,
    8,
    "SELECT regime AS metric, COUNT(*) AS value FROM signals GROUP BY regime ORDER BY value DESC",
)
bargauge(
    "Net PnL by Regime (signal→trade)",
    8,
    8,
    "SELECT s.regime AS metric, ROUND(SUM(t.pnl_net_usdt),4) AS value FROM signals s JOIN trades t ON s.trade_id=t.id WHERE t.exit_ts IS NOT NULL GROUP BY s.regime ORDER BY value",
    "currencyUSD",
    TH_PNL,
)
table(
    "Regime Performance (signal→trade)",
    8,
    8,
    "SELECT s.regime, COUNT(t.id) AS trades, ROUND(100.0*AVG((t.pnl_net_usdt>0)::int),1) AS win_rate, ROUND(AVG(t.pnl_net_usdt),4) AS avg_pnl, ROUND(SUM(t.pnl_net_usdt),4) AS net_pnl FROM signals s JOIN trades t ON s.trade_id=t.id WHERE t.exit_ts IS NOT NULL GROUP BY s.regime ORDER BY net_pnl NULLS LAST",
    overrides=[
        col_override("net_pnl", "currencyUSD"),
        col_override("avg_pnl", "currencyUSD"),
        col_override("win_rate", "percent", bg=False, th=TH_WIN),
    ],
)

# ════════════════════════════════════════════════════════════════════════════
# 10 — Market & Indicators  (69k candles; ATR shown as % so it's pair-comparable)
# ════════════════════════════════════════════════════════════════════════════
section("📊 Market & Indicators (live, per pair)")
table(
    "Current Market Snapshot (latest candle per pair)",
    24,
    9,
    f"SELECT pair, ROUND(close,8) AS price, ROUND(rsi14,1) AS rsi14, ROUND(atr14/NULLIF(close,0)*100,3) AS atr_pct, ROUND(adx,1) AS adx, ROUND((ema9-ema21)/NULLIF(ema21,0)*100,3) AS ema_spread_pct, ROUND(bb_width,4) AS bb_width, ROUND(volume_ratio,2) AS vol_ratio, ROUND(body_ratio,2) AS body_ratio, direction AS last_dir, to_timestamp(ts/1000.0) AS as_of FROM ({_LATEST_CANDLE}) c ORDER BY pair",
    overrides=[
        col_override(
            "rsi14",
            "short",
            bg=False,
            th={
                "mode": "absolute",
                "steps": [
                    {"color": "green", "value": None},
                    {"color": "orange", "value": 70},
                    {"color": "red", "value": 80},
                ],
            },
        ),
        col_override(
            "adx",
            "short",
            bg=False,
            th={
                "mode": "absolute",
                "steps": [
                    {"color": "red", "value": None},
                    {"color": "orange", "value": 15},
                    {"color": "green", "value": 20},
                ],
            },
        ),
    ],
)
ts(
    "RSI(14) Over Time — per pair (70/30 bands)",
    12,
    8,
    'SELECT to_timestamp(ts/1000) AS "time", pair AS metric, AVG(rsi14) AS rsi FROM candles WHERE rsi14 IS NOT NULL AND to_timestamp(ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY ts, pair ORDER BY ts',
    unit="short",
    fmt="time_series",
    fill=0,
    mx=100,
    mn=0,
)
ts(
    "ATR % (volatility, pair-comparable) Over Time",
    12,
    8,
    'SELECT to_timestamp(ts/1000) AS "time", pair AS metric, AVG(atr14/NULLIF(close,0)*100) AS atr_pct FROM candles WHERE atr14 IS NOT NULL AND to_timestamp(ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY ts, pair ORDER BY ts',
    unit="percent",
    fmt="time_series",
    fill=0,
)
ts(
    "ADX(14) Over Time — per pair (20 = trend threshold)",
    12,
    8,
    'SELECT to_timestamp(ts/1000) AS "time", pair AS metric, AVG(adx) AS adx FROM candles WHERE adx IS NOT NULL AND to_timestamp(ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY ts, pair ORDER BY ts',
    unit="short",
    fmt="time_series",
    fill=0,
    tline=20,
)
ts(
    "Volume Ratio Over Time — per pair (1.0 = average)",
    12,
    8,
    'SELECT to_timestamp(ts/1000) AS "time", pair AS metric, AVG(volume_ratio) AS vol_ratio FROM candles WHERE volume_ratio IS NOT NULL AND to_timestamp(ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY ts, pair ORDER BY ts',
    unit="short",
    fmt="time_series",
    fill=0,
    tline=1,
)
pie(
    "Candle Direction Balance (up vs down)",
    8,
    7,
    "SELECT direction AS metric, COUNT(*) AS value FROM (SELECT DISTINCT ON (pair,ts) pair, ts, direction FROM candles WHERE direction IS NOT NULL ORDER BY pair, ts) x GROUP BY direction",
)
bargauge(
    "Avg ATR % (volatility) by Pair",
    8,
    7,
    "SELECT pair AS metric, ROUND(AVG(atr14/NULLIF(close,0)*100),3) AS value FROM candles WHERE atr14 IS NOT NULL GROUP BY pair ORDER BY value DESC",
    "percent",
    TH_BLUE,
)
bargauge(
    "Avg ADX by Pair (trend strength)",
    8,
    7,
    "SELECT pair AS metric, ROUND(AVG(adx),1) AS value FROM candles WHERE adx IS NOT NULL GROUP BY pair ORDER BY value DESC",
    "short",
    {
        "mode": "absolute",
        "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 15}, {"color": "green", "value": 20}],
    },
)

# ════════════════════════════════════════════════════════════════════════════
# 11 — HP-Tuning Lab (grid: TP×SL×hold)
# ════════════════════════════════════════════════════════════════════════════
section("🧪 HP-Tuning Lab (grid: TP×SL×hold)")
table(
    "🏆 Strategy Leaderboard — by net PnL (each row = one grid cell)",
    24,
    9,
    "SELECT split_part(bot_id,'-',4) AS cell, COUNT(*) FILTER (WHERE exit_ts IS NOT NULL) AS closed, COUNT(*) FILTER (WHERE exit_ts IS NULL) AS open, ROUND(100.0*SUM((pnl_net_usdt>0)::int) FILTER (WHERE exit_ts IS NOT NULL)/NULLIF(COUNT(*) FILTER (WHERE exit_ts IS NOT NULL),0),1) AS win_rate, ROUND(AVG(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS avg_pnl, ROUND(SUM(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS net_pnl FROM trades GROUP BY cell ORDER BY net_pnl DESC NULLS LAST",
    overrides=[
        col_override("net_pnl", "currencyUSD"),
        col_override("avg_pnl", "currencyUSD"),
        col_override("win_rate", "percent", bg=False, th=TH_WIN),
    ],
)
bargauge(
    "Avg net PnL/trade by TP multiple",
    8,
    7,
    f"SELECT 'TP '||round((regexp_match({_TOK},'t([0-9]+)'))[1]::numeric/10,1) AS metric, ROUND(AVG(pnl_net_usdt),5) AS value FROM trades WHERE {_LAB} GROUP BY 1 ORDER BY metric",
    "currencyUSD",
    TH_PNL,
)
bargauge(
    "Avg net PnL/trade by SL multiple",
    8,
    7,
    f"SELECT 'SL '||round((regexp_match({_TOK},'s([0-9]+)'))[1]::numeric/10,1) AS metric, ROUND(AVG(pnl_net_usdt),5) AS value FROM trades WHERE {_LAB} GROUP BY 1 ORDER BY metric",
    "currencyUSD",
    TH_PNL,
)
bargauge(
    "Avg net PnL/trade by max-hold",
    8,
    7,
    f"SELECT 'hold '||(regexp_match({_TOK},'h([0-9]+)'))[1] AS metric, ROUND(AVG(pnl_net_usdt),5) AS value FROM trades WHERE {_LAB} GROUP BY 1 ORDER BY metric",
    "currencyUSD",
    TH_PNL,
)
table(
    "TP × SL grid — avg net PnL/trade (rows=SL, cols=TP)",
    12,
    8,
    f"SELECT 'SL '||round((regexp_match({_TOK},'s([0-9]+)'))[1]::numeric/10,1) AS sl_mult, "
    f"ROUND(AVG(pnl_net_usdt) FILTER (WHERE (regexp_match({_TOK},'t([0-9]+)'))[1]='10'),5) AS \"TP 1.0\", "
    f"ROUND(AVG(pnl_net_usdt) FILTER (WHERE (regexp_match({_TOK},'t([0-9]+)'))[1]='16'),5) AS \"TP 1.6\", "
    f"ROUND(AVG(pnl_net_usdt) FILTER (WHERE (regexp_match({_TOK},'t([0-9]+)'))[1]='24'),5) AS \"TP 2.4\", "
    f"ROUND(AVG(pnl_net_usdt) FILTER (WHERE (regexp_match({_TOK},'t([0-9]+)'))[1]='30'),5) AS \"TP 3.0\" "
    f"FROM trades WHERE {_LAB} GROUP BY sl_mult ORDER BY sl_mult",
    overrides=[
        col_override("TP 1.0", "currencyUSD"),
        col_override("TP 1.6", "currencyUSD"),
        col_override("TP 2.4", "currencyUSD"),
        col_override("TP 3.0", "currencyUSD"),
    ],
)
bargauge(
    "Sample size (closed trades) per grid cell",
    12,
    8,
    f"SELECT {_TOK} AS metric, COUNT(*) AS value FROM trades WHERE {_LAB} GROUP BY 1 ORDER BY value DESC LIMIT 24",
    "short",
    {
        "mode": "absolute",
        "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 10}, {"color": "green", "value": 30}],
    },
)

# ════════════════════════════════════════════════════════════════════════════
# 13 — Session & Time-of-Day (UTC)
# ════════════════════════════════════════════════════════════════════════════
section("🕑 Session & Time-of-Day (UTC)")
table(
    "Per-Session Performance",
    12,
    8,
    f"SELECT session, COUNT(*) AS trades, ROUND(100.0*SUM((pnl_net_usdt>0)::int)/COUNT(*),1) AS win_rate, ROUND(SUM(pnl_net_usdt),4) AS net_pnl, ROUND(AVG(pnl_net_usdt),4) AS avg_pnl FROM (SELECT pnl_net_usdt, {_SESSION_CASE} AS session FROM (SELECT pnl_net_usdt, {_HOUR} AS h FROM trades WHERE exit_ts IS NOT NULL) a) b GROUP BY session ORDER BY net_pnl NULLS LAST",
    overrides=[
        col_override("net_pnl", "currencyUSD"),
        col_override("avg_pnl", "currencyUSD"),
        col_override("win_rate", "percent", bg=False, th=TH_WIN),
    ],
)
bargauge(
    "Net PnL by Session",
    12,
    8,
    f"SELECT session AS metric, ROUND(SUM(pnl_net_usdt),4) AS value FROM (SELECT pnl_net_usdt, {_SESSION_CASE} AS session FROM (SELECT pnl_net_usdt, {_HOUR} AS h FROM trades WHERE exit_ts IS NOT NULL) a) b GROUP BY session ORDER BY value",
    "currencyUSD",
    TH_PNL,
)
bargauge(
    "Net PnL by Hour-of-Day (UTC)",
    12,
    7,
    f"SELECT lpad(({_HOUR})::text,2,'0')||':00' AS metric, ROUND(SUM(pnl_net_usdt),4) AS value FROM trades WHERE exit_ts IS NOT NULL GROUP BY {_HOUR} ORDER BY {_HOUR}",
    "currencyUSD",
    TH_PNL,
)
bargauge(
    "Trades by Hour-of-Day (UTC)",
    12,
    7,
    f"SELECT lpad(({_HOUR})::text,2,'0')||':00' AS metric, COUNT(*) AS value FROM trades WHERE exit_ts IS NOT NULL GROUP BY {_HOUR} ORDER BY {_HOUR}",
    "short",
    TH_BLUE,
)
bargauge(
    "Net PnL by Day-of-Week (UTC)",
    12,
    7,
    "SELECT to_char(to_timestamp(entry_ts/1000.0),'Dy') AS metric, ROUND(SUM(pnl_net_usdt),4) AS value FROM trades WHERE exit_ts IS NOT NULL GROUP BY to_char(to_timestamp(entry_ts/1000.0),'Dy'), EXTRACT(ISODOW FROM to_timestamp(entry_ts/1000.0)) ORDER BY EXTRACT(ISODOW FROM to_timestamp(entry_ts/1000.0))",
    "currencyUSD",
    TH_PNL,
)
bargauge(
    "Trades by Day-of-Week (UTC)",
    12,
    7,
    "SELECT to_char(to_timestamp(entry_ts/1000.0),'Dy') AS metric, COUNT(*) AS value FROM trades WHERE exit_ts IS NOT NULL GROUP BY to_char(to_timestamp(entry_ts/1000.0),'Dy'), EXTRACT(ISODOW FROM to_timestamp(entry_ts/1000.0)) ORDER BY EXTRACT(ISODOW FROM to_timestamp(entry_ts/1000.0))",
    "short",
    TH_BLUE,
)

# ════════════════════════════════════════════════════════════════════════════
# 14 — Fees & Costs
# ════════════════════════════════════════════════════════════════════════════
section("💸 Fees & Costs")
stat(
    "Total Fees Paid",
    4,
    4,
    "SELECT ROUND(COALESCE(SUM(fee_entry_usdt+COALESCE(fee_exit_usdt,0)),0),4) AS v FROM trades",
    "currencyUSD",
    TH_RED,
)
stat(
    "Avg Fee per Trade",
    4,
    4,
    f"SELECT ROUND(AVG(fee_entry_usdt+COALESCE(fee_exit_usdt,0)),4) AS v {_CLOSED}",
    "currencyUSD",
    TH_BLUE,
)
stat(
    "Gross PnL (pre-fee)",
    4,
    4,
    f"SELECT ROUND(COALESCE(SUM(pnl_gross_usdt),0),4) AS v {_CLOSED}",
    "currencyUSD",
    TH_PNL,
)
stat("Net PnL (post-fee)", 4, 4, f"SELECT ROUND(COALESCE(SUM(pnl_net_usdt),0),4) AS v {_CLOSED}", "currencyUSD", TH_PNL)
stat(
    "Fee Drag (fees / |gross|)",
    4,
    4,
    f"SELECT ROUND(100.0*SUM(fee_entry_usdt+COALESCE(fee_exit_usdt,0))/NULLIF(ABS(SUM(pnl_gross_usdt)),0),1) AS v {_CLOSED}",
    "percent",
    TH_RED,
    decimals=1,
)
stat(
    "Avg Round-trip Cost (% of bucket)",
    4,
    4,
    f"SELECT ROUND(AVG((fee_entry_usdt+COALESCE(fee_exit_usdt,0))/NULLIF(size_usdt,0))*100,3) AS v {_CLOSED}",
    "percent",
    TH_RED,
    decimals=3,
)
ts(
    "Cumulative Fees Paid Over Time",
    12,
    7,
    'SELECT to_timestamp(exit_ts/1000.0) AS "time", SUM(fee_entry_usdt+COALESCE(fee_exit_usdt,0)) OVER (ORDER BY exit_ts) AS cumulative_fees FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() ORDER BY exit_ts',
    color="red",
    fill=30,
)
ts(
    "Gross vs Net PnL (cumulative)",
    12,
    7,
    'SELECT to_timestamp(exit_ts/1000.0) AS "time", SUM(pnl_gross_usdt) OVER (ORDER BY exit_ts) AS gross, SUM(pnl_net_usdt) OVER (ORDER BY exit_ts) AS net FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() ORDER BY exit_ts',
    fmt="time_series",
)

# ════════════════════════════════════════════════════════════════════════════
# 15 — Trade History (full per-trade detail)
# ════════════════════════════════════════════════════════════════════════════
section("📜 Trade History")
table(
    "Trade History (closed) — full detail: TP/SL, R-multiple, MFE/MAE, duration",
    24,
    12,
    "SELECT to_timestamp(t.entry_ts/1000.0) AS entry_time, to_timestamp(t.exit_ts/1000.0) AS exit_time, "
    "t.pair, t.direction AS dir, t.leverage AS lev, ROUND(t.entry_price,8) AS entry, ROUND(t.exit_price,8) AS exit, "
    "ROUND(t.tp_price,8) AS tp, ROUND(t.sl_price,8) AS sl, "
    "ROUND(CASE WHEN t.direction='long' THEN t.exit_price-t.entry_price ELSE t.entry_price-t.exit_price END,8) AS points, "
    "ROUND(CASE WHEN t.direction='long' THEN (t.exit_price-t.entry_price)/t.entry_price ELSE (t.entry_price-t.exit_price)/t.entry_price END*100,3) AS move_pct, "
    f"{_REALIZED_R} AS r_mult, {_PLANNED_RR} AS plan_rr, "
    f"ROUND({_MFE},3) AS mfe_pct, ROUND({_MAE},3) AS mae_pct, "
    "t.hold_candles AS candles, ROUND((t.exit_ts-t.entry_ts)/60000.0,0) AS dur_min, "
    "ROUND(t.fee_entry_usdt+COALESCE(t.fee_exit_usdt,0),4) AS fees, "
    "ROUND(t.pnl_net_usdt,4) AS net_pnl, ROUND(t.pnl_pct,2) AS pnl_pct, "
    "ROUND(ABS(t.entry_price-t.liquidation_price)/t.entry_price*100,2) AS liq_dist_pct, "
    "ROUND(t.bucket_balance_after,4) AS bal_after, t.close_reason AS reason, t.pattern "
    f"FROM trades t {_PX} WHERE t.exit_ts IS NOT NULL ORDER BY t.exit_ts DESC LIMIT 200",
    overrides=[
        col_override("net_pnl", "currencyUSD"),
        col_override("points", "short", bg=False),
        col_override("move_pct", "percent", bg=False),
        col_override("pnl_pct", "percent", bg=False),
        col_override("r_mult", "short"),
        col_override("mfe_pct", "percent", bg=False),
        col_override("mae_pct", "percent", bg=False),
        col_override("fees", "currencyUSD", bg=False),
        col_override("bal_after", "currencyUSD", bg=False),
    ],
)

# ════════════════════════════════════════════════════════════════════════════
# 16 — Open Positions (live mark-to-market)
# ════════════════════════════════════════════════════════════════════════════
section("📌 Open Positions (live)")
table(
    "Open Positions — live mark-to-market",
    24,
    8,
    f"SELECT to_timestamp(t.entry_ts/1000.0) AS entry_time, t.bot_id, t.pair, t.direction AS dir, t.leverage AS lev, ROUND(t.entry_price,8) AS entry, ROUND(c.close,8) AS current, ROUND({_MOVE},8) AS points, ROUND({_MOVE}/t.entry_price*100,3) AS move_pct, ROUND({_MOVE}/t.entry_price*t.notional_usdt,4) AS unrealized_pnl, ROUND(t.tp_price,8) AS tp, ROUND(t.sl_price,8) AS sl, t.size_usdt AS margin, t.notional_usdt AS notional, t.pattern FROM trades t {_LATERAL} WHERE t.exit_ts IS NULL ORDER BY t.entry_ts DESC",
    overrides=[col_override("unrealized_pnl", "currencyUSD"), col_override("move_pct", "percent", bg=False)],
)

# ════════════════════════════════════════════════════════════════════════════
# 17 — Activity & Health
# ════════════════════════════════════════════════════════════════════════════
section("🩺 Activity & Health")
stat(
    "Bots alive (<90s)",
    6,
    4,
    f"SELECT COUNT(DISTINCT bot_id) AS bots FROM heartbeats WHERE ts >= {_NOW_MS} - 90000",
    thresholds={
        "mode": "absolute",
        "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 200}],
    },
)
stat(
    "Heartbeat age (s)",
    6,
    4,
    "SELECT COALESCE(((EXTRACT(EPOCH FROM NOW())*1000)::BIGINT - MAX(ts))/1000.0, 9999) AS age FROM heartbeats",
    "s",
    {
        "mode": "absolute",
        "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 60}, {"color": "red", "value": 90}],
    },
    graph="area",
)
stat(
    "Candles written (1h)",
    6,
    4,
    "SELECT COUNT(*) AS c FROM candles WHERE to_timestamp(ts/1000.0) >= NOW() - INTERVAL '1 hour'",
    thresholds={
        "mode": "absolute",
        "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 50}],
    },
)
stat(
    "Errors+Critical (24h)",
    6,
    4,
    f"SELECT COUNT(*) AS c FROM events WHERE level IN ('ERROR','CRITICAL') AND ts >= {_NOW_MS} - 86400000",
    thresholds={
        "mode": "absolute",
        "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}],
    },
)
bargauge(
    "Events by Category (24h)",
    8,
    7,
    f"SELECT category AS metric, COUNT(*) AS value FROM events WHERE ts >= {_NOW_MS} - 86400000 GROUP BY category ORDER BY value DESC",
)
pie(
    "Events by Level (24h)",
    8,
    7,
    f"SELECT level AS metric, COUNT(*) AS value FROM events WHERE ts >= {_NOW_MS} - 86400000 GROUP BY level ORDER BY value DESC",
)
bargauge(
    "Signal Rejections by Reason (24h)",
    8,
    7,
    f"SELECT split_part(message,':',2) AS metric, COUNT(*) AS value FROM events WHERE category='signal' AND message LIKE 'signal_rejected:%' AND ts >= {_NOW_MS} - 86400000 GROUP BY metric ORDER BY value DESC LIMIT 12",
)
ts(
    "Event Rate Over Time (hourly, by category)",
    24,
    7,
    'SELECT to_timestamp((ts/3600000)*3600) AS "time", category AS metric, COUNT(*) AS n FROM events WHERE to_timestamp(ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() GROUP BY (ts/3600000), category ORDER BY 1',
    unit="short",
    fmt="time_series",
    bars=True,
    fill=50,
    stack=True,
)
table(
    "Bot Heartbeats",
    24,
    8,
    f"SELECT bot_id, status, pid, to_timestamp(ts/1000.0) AS last_heartbeat, CASE WHEN ts >= {_NOW_MS} - 90000 THEN 1 ELSE 0 END AS alive FROM heartbeats ORDER BY bot_id LIMIT 250",
    overrides=[
        map_override(
            "alive",
            {"0": {"color": "red", "index": 0, "text": "DOWN"}, "1": {"color": "green", "index": 1, "text": "UP"}},
        )
    ],
)
table(
    "Recent Events",
    24,
    8,
    "SELECT to_timestamp(ts/1000.0) AS time, bot_id, level, category, message FROM events ORDER BY ts DESC LIMIT 50",
    overrides=[
        map_override(
            "level",
            {
                "CRITICAL": {"color": "red", "index": 0},
                "ERROR": {"color": "red", "index": 1},
                "INFO": {"color": "blue", "index": 2},
                "WARN": {"color": "orange", "index": 3},
            },
        )
    ],
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
    "tags": ["kestrel"],
    "templating": {
        "list": [
            {
                "name": "env",
                "label": "Phase",
                "type": "custom",
                "description": "Phase 1 labs (dev) · lab (owner sandbox) · Phase 2 staging (BingX VST demo) · Phase 3 prod (real)",
                "query": "dev,lab,staging,prod",
                "options": [
                    {"text": "dev", "value": "dev", "selected": True},
                    {"text": "lab", "value": "lab", "selected": False},
                    {"text": "staging", "value": "staging", "selected": False},
                    {"text": "prod", "value": "prod", "selected": False},
                ],
                "current": {"text": "dev", "value": "dev", "selected": True},
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
    "title": "Kestrel — Phase Monitor ($env)",
    "uid": "kestrel-main",
    "version": 1,
    "weekStart": "",
}

with open(OUT, "w") as f:
    json.dump(dashboard, f, indent=2)
n_p = len([p for p in panels if p["type"] != "row"])
n_s = len([p for p in panels if p["type"] == "row"])
print(f"wrote {os.path.normpath(OUT)} with {n_p} panels + {n_s} sections")
