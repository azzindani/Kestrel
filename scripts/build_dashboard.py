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
Run:  python3 scripts/build_dashboard.py
"""

from __future__ import annotations

import json
import os

DS = {"type": "postgres", "uid": "kestrel-db"}
PV = "11.3.0"
OUT = os.path.join(os.path.dirname(__file__), "..", "infra", "grafana", "dashboards", "kestrel.json")

TH_PNL = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 0}]}
TH_BLUE = {"mode": "absolute", "steps": [{"color": "blue", "value": None}]}
TH_WIN = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 50}, {"color": "green", "value": 55}]}
TH_LIMIT = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": -4}, {"color": "yellow", "value": -2}, {"color": "green", "value": 0}]}
TH_DD = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": -2}, {"color": "green", "value": -0.0001}]}

# Session CASE replicating src/config.get_trading_session (UTC hour from epoch ms)
_SESSION_CASE = ("CASE WHEN h>=13 AND h<16 THEN 'OVERLAP' WHEN h>=8 AND h<16 THEN 'LONDON' "
                 "WHEN h>=13 AND h<21 THEN 'US' ELSE 'ASIAN' END")
# Equity time-series subquery (running balance from $100)
_EQUITY = "SELECT exit_ts, to_timestamp(exit_ts/1000.0) AS t, 100 + SUM(pnl_net_usdt) OVER (ORDER BY exit_ts) AS equity FROM trades WHERE exit_ts IS NOT NULL"
_MOVE = "CASE WHEN t.direction='long' THEN (c.close - t.entry_price) ELSE (t.entry_price - c.close) END"
_LATERAL = "LEFT JOIN LATERAL (SELECT close FROM candles WHERE pair=t.pair AND timeframe=t.timeframe ORDER BY ts DESC LIMIT 1) c ON true"
# Per-trade path stats (MAE/MFE) — max favourable/adverse excursion during the hold,
# from the candle high/low band between entry_ts and exit_ts. Duplicate (pair,tf,ts)
# rows across bot_ids don't affect MAX(high)/MIN(low).
_PX = ("LEFT JOIN LATERAL (SELECT MAX(high) AS hi, MIN(low) AS lo FROM candles "
       "WHERE pair=t.pair AND timeframe=t.timeframe AND ts BETWEEN t.entry_ts AND t.exit_ts) px ON true")
_MFE = ("CASE WHEN t.direction='long' THEN (px.hi - t.entry_price)/t.entry_price*100 "
        "ELSE (t.entry_price - px.lo)/t.entry_price*100 END")
_MAE = ("CASE WHEN t.direction='long' THEN (px.lo - t.entry_price)/t.entry_price*100 "
        "ELSE (t.entry_price - px.hi)/t.entry_price*100 END")
# Risk-multiple maths: planned risk = |entry-sl|; realized R = signed move / planned risk.
_RISK = "NULLIF(ABS(t.entry_price - t.sl_price),0)"
_REALIZED_R = ("ROUND((CASE WHEN t.direction='long' THEN t.exit_price-t.entry_price "
               "ELSE t.entry_price-t.exit_price END)/" + _RISK + ",2)")
_PLANNED_RR = "ROUND(ABS(t.tp_price-t.entry_price)/" + _RISK + ",2)"

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
    panels.append({"collapsed": False, "gridPos": {"x": 0, "y": _cur["y"], "w": 24, "h": 1},
                   "id": nid(), "panels": [], "title": title, "type": "row"})
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
    return {"datasource": DS, "format": fmt, "rawQuery": True, "rawSql": sql, "refId": "A"}


def fc(unit=None, thresholds=None, custom=None, mn=None, mx=None):
    d = {"color": {"mode": "thresholds"}, "mappings": [], "thresholds": thresholds or TH_BLUE}
    if unit:
        d["unit"] = unit
    if custom is not None:
        d["custom"] = custom
    if mn is not None:
        d["min"] = mn
    if mx is not None:
        d["max"] = mx
    return {"defaults": d, "overrides": []}


def stat(title, w, h, sql, unit=None, thresholds=None, color="value", graph="none"):
    panels.append({"datasource": DS, "fieldConfig": fc(unit, thresholds), "gridPos": place(w, h), "id": nid(),
                   "options": {"colorMode": color, "graphMode": graph, "justifyMode": "auto", "orientation": "auto",
                               "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "textMode": "auto"},
                   "pluginVersion": PV, "targets": [tgt(sql)], "title": title, "type": "stat"})


def gauge(title, w, h, sql, unit=None, thresholds=None, mn=None, mx=None):
    panels.append({"datasource": DS, "fieldConfig": fc(unit, thresholds, mn=mn, mx=mx), "gridPos": place(w, h), "id": nid(),
                   "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                               "showThresholdLabels": False, "showThresholdMarkers": True, "orientation": "auto"},
                   "pluginVersion": PV, "targets": [tgt(sql)], "title": title, "type": "gauge"})


def ts(title, w, h, sql, unit="currencyUSD", fmt="table", bars=False, fill=20, color=None, step=False, mx=None, tline=None):
    custom = {"axisBorderShow": False, "axisCenteredZero": False, "axisColorMode": "text", "axisLabel": "",
              "axisPlacement": "auto", "barAlignment": 0, "drawStyle": "bars" if bars else "line", "fillOpacity": fill,
              "gradientMode": "opacity", "hideFrom": {"legend": False, "tooltip": False, "viz": False},
              "lineInterpolation": "stepAfter" if step else "linear", "lineWidth": 2, "pointSize": 5,
              "scaleDistribution": {"type": "linear"}, "showPoints": "never", "spanNulls": True,
              "stacking": {"group": "A", "mode": "none"},
              "thresholdsStyle": {"mode": "line"} if tline is not None else {"mode": "off"}}
    th = {"mode": "absolute", "steps": [{"color": "green", "value": None}]}
    if tline is not None:
        th = {"mode": "absolute", "steps": [{"color": "transparent", "value": None}, {"color": "blue", "value": tline}]}
    f = fc(unit, th, custom, mx=mx)
    f["defaults"]["color"] = {"mode": "fixed", "fixedColor": color} if color else {"mode": "palette-classic"}
    panels.append({"datasource": DS, "fieldConfig": f, "gridPos": place(w, h), "id": nid(),
                   "options": {"legend": {"calcs": ["last"], "displayMode": "list", "placement": "bottom", "showLegend": True},
                               "tooltip": {"mode": "multi", "sort": "none"}},
                   "pluginVersion": PV, "targets": [tgt(sql, fmt)], "title": title, "type": "timeseries"})


def bargauge(title, w, h, sql, unit=None, thresholds=None):
    panels.append({"datasource": DS, "fieldConfig": fc(unit, thresholds), "gridPos": place(w, h), "id": nid(),
                   "options": {"displayMode": "gradient", "orientation": "horizontal",
                               "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                               "showUnfilled": True, "valueMode": "color"},
                   "pluginVersion": PV, "targets": [tgt(sql)], "title": title, "type": "bargauge"})


def pie(title, w, h, sql):
    panels.append({"datasource": DS, "fieldConfig": fc(), "gridPos": place(w, h), "id": nid(),
                   "options": {"legend": {"displayMode": "list", "placement": "right", "showLegend": True, "values": ["value"]},
                               "pieType": "pie", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                               "tooltip": {"mode": "single", "sort": "none"}},
                   "pluginVersion": PV, "targets": [tgt(sql)], "title": title, "type": "piechart"})


def table(title, w, h, sql, overrides=None):
    f = fc()
    f["defaults"]["custom"] = {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False}
    f["overrides"] = overrides or []
    panels.append({"datasource": DS, "fieldConfig": f, "gridPos": place(w, h), "id": nid(),
                   "options": {"cellHeight": "sm", "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False}, "showHeader": True},
                   "pluginVersion": PV, "targets": [tgt(sql)], "title": title, "type": "table"})


def col_override(col, unit, bg=True):
    cell = {"mode": "gradient", "type": "color-background"} if bg else {"type": "color-text"}
    return {"matcher": {"id": "byName", "options": col},
            "properties": [{"id": "unit", "value": unit}, {"id": "custom.cellOptions", "value": cell}, {"id": "thresholds", "value": TH_PNL}]}


def map_override(col, mapping):
    return {"matcher": {"id": "byName", "options": col},
            "properties": [{"id": "mappings", "value": [{"type": "value", "options": mapping}]},
                           {"id": "custom.cellOptions", "value": {"type": "color-text"}}]}


# ════════════════════════════════════════════════════════════════════════════
# Section 1 — Account & Performance
# ════════════════════════════════════════════════════════════════════════════
section("💰 Account & Performance")
stat("Account Balance (start $100)", 4, 4, "SELECT 100 + COALESCE(SUM(pnl_net_usdt),0) AS balance FROM trades WHERE exit_ts IS NOT NULL", "currencyUSD", TH_PNL, graph="area")
stat("Realized PnL (all-time)", 4, 4, "SELECT COALESCE(SUM(pnl_net_usdt),0) AS pnl FROM trades WHERE exit_ts IS NOT NULL", "currencyUSD", TH_PNL)
stat("Unrealized PnL (open, MtM)", 4, 4, f"SELECT COALESCE(SUM(({_MOVE})/t.entry_price * t.notional_usdt),0) AS unrealized FROM trades t {_LATERAL} WHERE t.exit_ts IS NULL", "currencyUSD", TH_PNL)
stat("Open Positions", 3, 4, "SELECT COUNT(*) AS open FROM trades WHERE exit_ts IS NULL", thresholds=TH_BLUE)
stat("Total Trades", 3, 4, "SELECT COUNT(*) AS total FROM trades", thresholds=TH_BLUE)
stat("Win Rate (closed)", 3, 4, "SELECT CASE WHEN COUNT(*)=0 THEN 0 ELSE 100.0*SUM((pnl_net_usdt>0)::int)/COUNT(*) END AS win_rate FROM trades WHERE exit_ts IS NOT NULL", "percent", TH_WIN)
gauge("Today PnL vs −$5 limit (UTC)", 3, 4, "SELECT COALESCE(SUM(pnl_net_usdt),0) AS today FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) >= date_trunc('day', NOW() AT TIME ZONE 'UTC')", "currencyUSD", TH_LIMIT, mn=-5, mx=5)

# ════════════════════════════════════════════════════════════════════════════
# Section 2 — Equity, PnL & Drawdown
# ════════════════════════════════════════════════════════════════════════════
section("📈 Equity, PnL, Drawdown & Exposure")
ts("Account Balance Over Time (equity curve)", 12, 8,
   f"SELECT q.t AS \"time\", q.equity FROM ({_EQUITY}) q WHERE q.t BETWEEN $__timeFrom() AND $__timeTo() ORDER BY q.exit_ts")
ts("Cumulative PnL per Bot", 12, 8,
   "SELECT to_timestamp(exit_ts/1000.0) AS \"time\", bot_id AS metric, SUM(pnl_net_usdt) OVER (PARTITION BY bot_id ORDER BY exit_ts) AS cumulative_pnl FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() ORDER BY exit_ts", fmt="time_series")
ts("Drawdown (equity − peak, from $100 start)", 12, 8,
   f"SELECT q.t AS \"time\", q.equity - GREATEST(MAX(q.equity) OVER (ORDER BY q.exit_ts), 100) AS drawdown FROM ({_EQUITY}) q WHERE q.t BETWEEN $__timeFrom() AND $__timeTo() ORDER BY q.exit_ts",
   color="red", fill=40)
stat("Max Drawdown", 6, 8,
     f"SELECT MIN(equity - GREATEST(peak, 100)) AS max_dd FROM (SELECT equity, MAX(equity) OVER (ORDER BY exit_ts) AS peak FROM ({_EQUITY}) a) b",
     "currencyUSD", TH_DD)
stat("Current Drawdown", 6, 8,
     f"SELECT (SELECT 100 + COALESCE(SUM(pnl_net_usdt),0) FROM trades WHERE exit_ts IS NOT NULL) - GREATEST((SELECT COALESCE(MAX(equity),100) FROM ({_EQUITY}) z), 100) AS current_dd",
     "currencyUSD", TH_DD)
ts("Net PnL per Trade", 24, 7,
   "SELECT to_timestamp(exit_ts/1000.0) AS \"time\", pnl_net_usdt AS net_pnl FROM trades WHERE exit_ts IS NOT NULL AND to_timestamp(exit_ts/1000.0) BETWEEN $__timeFrom() AND $__timeTo() ORDER BY exit_ts", bars=True, fill=70)
ts("Exposure Over Time — open notional & margin deployed", 24, 7,
   "SELECT q.t AS \"time\", q.notional_exposure AS \"notional_$\", q.margin_used AS \"margin_$\" FROM (SELECT ts, to_timestamp(ts/1000.0) AS t, SUM(nd) OVER (ORDER BY ts, ord) AS notional_exposure, SUM(md) OVER (ORDER BY ts, ord) AS margin_used FROM (SELECT entry_ts AS ts, notional_usdt AS nd, size_usdt AS md, 0 AS ord FROM trades UNION ALL SELECT exit_ts AS ts, -notional_usdt AS nd, -size_usdt AS md, 1 AS ord FROM trades WHERE exit_ts IS NOT NULL) e) q WHERE q.t BETWEEN $__timeFrom() AND $__timeTo() ORDER BY q.ts",
   unit="currencyUSD", step=True, fill=15)

# ════════════════════════════════════════════════════════════════════════════
# Section 3 — Trade Analytics
# ════════════════════════════════════════════════════════════════════════════
section("🔬 Trade Analytics")
table("🏆 Strategy Leaderboard — head-to-head by net PnL (bake-off)", 24, 9,
      "SELECT split_part(bot_id,'-',4) AS strategy, COUNT(*) FILTER (WHERE exit_ts IS NOT NULL) AS closed, COUNT(*) FILTER (WHERE exit_ts IS NULL) AS open, ROUND(100.0*SUM((pnl_net_usdt>0)::int) FILTER (WHERE exit_ts IS NOT NULL)/NULLIF(COUNT(*) FILTER (WHERE exit_ts IS NOT NULL),0),1) AS win_rate, ROUND(AVG(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS avg_pnl, ROUND(SUM(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS net_pnl FROM trades GROUP BY strategy ORDER BY net_pnl DESC NULLS LAST",
      overrides=[col_override("net_pnl", "currencyUSD"), col_override("avg_pnl", "currencyUSD"), col_override("win_rate", "percent", bg=False)])
ts("Rolling Win-Rate — last 20 trades vs cumulative (55% = go-live target)", 24, 7,
   "SELECT q.t AS \"time\", q.r20 AS \"rolling_20\", q.cum AS \"cumulative\" FROM (SELECT exit_ts, to_timestamp(exit_ts/1000.0) AS t, 100.0*AVG((pnl_net_usdt>0)::int) OVER (ORDER BY exit_ts ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS r20, 100.0*AVG((pnl_net_usdt>0)::int) OVER (ORDER BY exit_ts) AS cum FROM trades WHERE exit_ts IS NOT NULL) q WHERE q.t BETWEEN $__timeFrom() AND $__timeTo() ORDER BY q.exit_ts",
   unit="percent", mx=100, tline=55, fill=8)
pie("Wins vs Losses", 6, 8, "SELECT 'Wins' AS metric, COUNT(*) AS value FROM trades WHERE exit_ts IS NOT NULL AND pnl_net_usdt>0 UNION ALL SELECT 'Losses', COUNT(*) FROM trades WHERE exit_ts IS NOT NULL AND pnl_net_usdt<=0")
bargauge("Close Reasons", 6, 8, "SELECT close_reason AS metric, COUNT(*) AS value FROM trades WHERE exit_ts IS NOT NULL GROUP BY close_reason ORDER BY value DESC")
table("Trade Stats", 12, 8, "SELECT ROUND(AVG(pnl_net_usdt) FILTER (WHERE pnl_net_usdt>0),4) AS avg_win, ROUND(AVG(pnl_net_usdt) FILTER (WHERE pnl_net_usdt<=0),4) AS avg_loss, ROUND(MAX(pnl_net_usdt),4) AS largest_win, ROUND(MIN(pnl_net_usdt),4) AS largest_loss, ROUND(AVG(hold_candles),1) AS avg_hold_candles, ROUND(COALESCE(SUM(pnl_net_usdt) FILTER (WHERE pnl_net_usdt>0)/NULLIF(-SUM(pnl_net_usdt) FILTER (WHERE pnl_net_usdt<=0),0),0),2) AS profit_factor FROM trades WHERE exit_ts IS NOT NULL",
      overrides=[col_override("avg_win", "currencyUSD"), col_override("avg_loss", "currencyUSD"), col_override("largest_win", "currencyUSD"), col_override("largest_loss", "currencyUSD")])
table("PnL by Pair", 12, 8, "SELECT pair, COUNT(*) AS trades, ROUND(100.0*SUM((pnl_net_usdt>0)::int)/NULLIF(COUNT(*) FILTER (WHERE exit_ts IS NOT NULL),0),1) AS win_rate, ROUND(SUM(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS net_pnl FROM trades GROUP BY pair ORDER BY net_pnl NULLS LAST",
      overrides=[col_override("net_pnl", "currencyUSD"), col_override("win_rate", "percent", bg=False)])
table("PnL by Pattern", 12, 8, "SELECT pattern, COUNT(*) FILTER (WHERE exit_ts IS NOT NULL) AS closed, ROUND(100.0*SUM((pnl_net_usdt>0)::int)/NULLIF(COUNT(*) FILTER (WHERE exit_ts IS NOT NULL),0),1) AS win_rate, ROUND(SUM(pnl_net_usdt) FILTER (WHERE exit_ts IS NOT NULL),4) AS net_pnl FROM trades GROUP BY pattern ORDER BY net_pnl NULLS LAST",
      overrides=[col_override("net_pnl", "currencyUSD"), col_override("win_rate", "percent", bg=False)])

# ════════════════════════════════════════════════════════════════════════════
# Section — HP-tuning dimensions (grid lab: avg net PnL/trade per grid axis)
# ════════════════════════════════════════════════════════════════════════════
section("🧪 HP-Tuning Dimensions (grid lab)")
_TOK = "split_part(bot_id,'-',4)"
_LAB = f"exit_ts IS NOT NULL AND {_TOK} ~ '^t[0-9]+s[0-9]+h[0-9]+$'"
bargauge("Avg net PnL/trade by TP multiple", 8, 8,
         f"SELECT 'TP '||round((regexp_match({_TOK},'t([0-9]+)'))[1]::numeric/10,1) AS metric, ROUND(AVG(pnl_net_usdt),5) AS value FROM trades WHERE {_LAB} GROUP BY 1 ORDER BY metric",
         "currencyUSD", TH_PNL)
bargauge("Avg net PnL/trade by SL multiple", 8, 8,
         f"SELECT 'SL '||round((regexp_match({_TOK},'s([0-9]+)'))[1]::numeric/10,1) AS metric, ROUND(AVG(pnl_net_usdt),5) AS value FROM trades WHERE {_LAB} GROUP BY 1 ORDER BY metric",
         "currencyUSD", TH_PNL)
bargauge("Avg net PnL/trade by max-hold", 8, 8,
         f"SELECT 'hold '||(regexp_match({_TOK},'h([0-9]+)'))[1] AS metric, ROUND(AVG(pnl_net_usdt),5) AS value FROM trades WHERE {_LAB} GROUP BY 1 ORDER BY metric",
         "currencyUSD", TH_PNL)

# ════════════════════════════════════════════════════════════════════════════
# Section 4 — Session breakdown (UTC trading sessions, CLAUDE.md §22)
# ════════════════════════════════════════════════════════════════════════════
section("🕑 Session Breakdown (UTC)")
table("Per-Session Performance", 12, 8,
      f"SELECT session, COUNT(*) AS trades, ROUND(100.0*SUM((pnl_net_usdt>0)::int)/COUNT(*),1) AS win_rate, ROUND(SUM(pnl_net_usdt),4) AS net_pnl, ROUND(AVG(pnl_net_usdt),4) AS avg_pnl FROM (SELECT pnl_net_usdt, {_SESSION_CASE} AS session FROM (SELECT pnl_net_usdt, ((entry_ts/3600000)::bigint % 24) AS h FROM trades WHERE exit_ts IS NOT NULL) a) b GROUP BY session ORDER BY net_pnl NULLS LAST",
      overrides=[col_override("net_pnl", "currencyUSD"), col_override("avg_pnl", "currencyUSD"), col_override("win_rate", "percent", bg=False)])
bargauge("Net PnL by Session", 12, 8,
         f"SELECT session AS metric, ROUND(SUM(pnl_net_usdt),4) AS value FROM (SELECT pnl_net_usdt, {_SESSION_CASE} AS session FROM (SELECT pnl_net_usdt, ((entry_ts/3600000)::bigint % 24) AS h FROM trades WHERE exit_ts IS NOT NULL) a) b GROUP BY session ORDER BY value",
         "currencyUSD", TH_PNL)

# ════════════════════════════════════════════════════════════════════════════
# Section — Trade Quality (risk-multiple, excursion, duration)
# Diagnoses WHY trades win/lose: did price run our way then give it back (high MFE
# on a timeout → TP too far)? Did we get stopped on noise (deep MAE on a win → SL
# too tight)? Realized R puts every outcome on one comparable scale.
# ════════════════════════════════════════════════════════════════════════════
section("📐 Trade Quality — R-multiple, excursion, duration")
_CLOSED = "FROM trades WHERE exit_ts IS NOT NULL"
stat("TP-hit Rate", 4, 4, f"SELECT 100.0*AVG((close_reason='take_profit')::int) AS v {_CLOSED}", "percent", TH_WIN)
stat("SL-hit Rate", 4, 4, f"SELECT 100.0*AVG((close_reason='stop_loss')::int) AS v {_CLOSED}", "percent", TH_BLUE)
stat("Timeout Rate", 4, 4, f"SELECT 100.0*AVG((close_reason='timeout')::int) AS v {_CLOSED}", "percent", TH_BLUE)
stat("Liquidation Rate", 4, 4, f"SELECT 100.0*AVG((close_reason='liquidated')::int) AS v {_CLOSED}", "percent", {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 0.001}]})
stat("Avg Realized R (per trade)", 4, 4, f"SELECT ROUND(AVG((CASE WHEN direction='long' THEN exit_price-entry_price ELSE entry_price-exit_price END)/NULLIF(ABS(entry_price-sl_price),0)),3) AS v {_CLOSED}", "short", TH_PNL)
stat("Avg Planned R/R (target)", 4, 4, f"SELECT ROUND(AVG(ABS(tp_price-entry_price)/NULLIF(ABS(entry_price-sl_price),0)),2) AS v {_CLOSED}", "short", TH_BLUE)
stat("Avg MFE % (best excursion)", 4, 4, f"SELECT ROUND(AVG({_MFE}),3) AS v FROM trades t {_PX} WHERE t.exit_ts IS NOT NULL", "percent", {"mode": "absolute", "steps": [{"color": "green", "value": None}]})
stat("Avg MAE % (worst excursion)", 4, 4, f"SELECT ROUND(AVG({_MAE}),3) AS v FROM trades t {_PX} WHERE t.exit_ts IS NOT NULL", "percent", {"mode": "absolute", "steps": [{"color": "red", "value": None}]})
stat("Avg Hold (candles)", 4, 4, f"SELECT ROUND(AVG(hold_candles),2) AS v {_CLOSED}", thresholds=TH_BLUE)
stat("Avg Hold (minutes)", 4, 4, f"SELECT ROUND(AVG((exit_ts-entry_ts)/60000.0),1) AS v {_CLOSED}", "m", TH_BLUE)
stat("Avg Fees ($/trade)", 4, 4, f"SELECT ROUND(AVG(fee_entry_usdt+COALESCE(fee_exit_usdt,0)),4) AS v {_CLOSED}", "currencyUSD", TH_BLUE)
stat("Avg Liq Distance %", 4, 4, f"SELECT ROUND(AVG(ABS(entry_price-liquidation_price)/entry_price*100),2) AS v {_CLOSED}", "percent", {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1.5}, {"color": "green", "value": 3}]})
bargauge("Realized R-multiple Distribution (closed trades)", 12, 8,
         f"SELECT bucket AS metric, COUNT(*) AS value FROM (SELECT CASE WHEN r<=-1 THEN '1: ≤ -1R (full stop)' WHEN r<0 THEN '2: -1..0R' WHEN r<1 THEN '3: 0..1R' WHEN r<2 THEN '4: 1..2R' ELSE '5: ≥ 2R' END AS bucket FROM (SELECT (CASE WHEN direction='long' THEN exit_price-entry_price ELSE entry_price-exit_price END)/NULLIF(ABS(entry_price-sl_price),0) AS r {_CLOSED}) x WHERE r IS NOT NULL) y GROUP BY bucket ORDER BY bucket")
table("Excursion by Close Reason — did winners run / losers get stopped on noise?", 12, 8,
      f"SELECT t.close_reason, COUNT(*) AS trades, ROUND(AVG({_MFE}),3) AS avg_mfe_pct, ROUND(AVG({_MAE}),3) AS avg_mae_pct, ROUND(AVG((CASE WHEN t.direction='long' THEN t.exit_price-t.entry_price ELSE t.entry_price-t.exit_price END)/{_RISK}),2) AS avg_realized_r FROM trades t {_PX} WHERE t.exit_ts IS NOT NULL GROUP BY t.close_reason ORDER BY trades DESC",
      overrides=[col_override("avg_mfe_pct", "percent", bg=False), col_override("avg_mae_pct", "percent", bg=False), col_override("avg_realized_r", "short")])

# ════════════════════════════════════════════════════════════════════════════
# Section 5 — Trade History (full per-trade detail)
# ════════════════════════════════════════════════════════════════════════════
section("📜 Trade History")
table("Trade History (closed) — full detail: TP/SL, R-multiple, MFE/MAE, duration", 24, 12,
      "SELECT to_timestamp(t.entry_ts/1000.0) AS entry_time, to_timestamp(t.exit_ts/1000.0) AS exit_time, "
      "t.pair, t.direction AS dir, t.leverage AS lev, ROUND(t.entry_price,6) AS entry, ROUND(t.exit_price,6) AS exit, "
      "ROUND(t.tp_price,6) AS tp, ROUND(t.sl_price,6) AS sl, "
      "ROUND(CASE WHEN t.direction='long' THEN t.exit_price-t.entry_price ELSE t.entry_price-t.exit_price END,6) AS points, "
      "ROUND(CASE WHEN t.direction='long' THEN (t.exit_price-t.entry_price)/t.entry_price ELSE (t.entry_price-t.exit_price)/t.entry_price END*100,3) AS move_pct, "
      f"{_REALIZED_R} AS r_mult, {_PLANNED_RR} AS plan_rr, "
      f"ROUND({_MFE},3) AS mfe_pct, ROUND({_MAE},3) AS mae_pct, "
      "t.hold_candles AS candles, ROUND((t.exit_ts-t.entry_ts)/60000.0,0) AS dur_min, "
      "ROUND(t.fee_entry_usdt+COALESCE(t.fee_exit_usdt,0),4) AS fees, "
      "ROUND(t.pnl_net_usdt,4) AS net_pnl, ROUND(t.pnl_pct,2) AS pnl_pct, "
      "ROUND(ABS(t.entry_price-t.liquidation_price)/t.entry_price*100,2) AS liq_dist_pct, "
      "ROUND(t.bucket_balance_after,4) AS bal_after, t.close_reason AS reason, t.pattern "
      f"FROM trades t {_PX} WHERE t.exit_ts IS NOT NULL ORDER BY t.exit_ts DESC LIMIT 200",
      overrides=[col_override("net_pnl", "currencyUSD"), col_override("points", "short", bg=False),
                 col_override("move_pct", "percent", bg=False), col_override("pnl_pct", "percent", bg=False),
                 col_override("r_mult", "short"), col_override("mfe_pct", "percent", bg=False),
                 col_override("mae_pct", "percent", bg=False), col_override("fees", "currencyUSD", bg=False),
                 col_override("bal_after", "currencyUSD", bg=False)])

# ════════════════════════════════════════════════════════════════════════════
# Section 6 — Open Positions (live mark-to-market)
# ════════════════════════════════════════════════════════════════════════════
section("📌 Open Positions (live)")
table("Open Positions — live mark-to-market", 24, 8,
      f"SELECT to_timestamp(t.entry_ts/1000.0) AS entry_time, t.pair, t.direction, t.leverage, ROUND(t.entry_price,6) AS entry, ROUND(c.close,6) AS current, ROUND({_MOVE},6) AS points, ROUND({_MOVE}/t.entry_price*100,3) AS move_pct, ROUND({_MOVE}/t.entry_price*t.notional_usdt,4) AS unrealized_pnl, ROUND(t.tp_price,6) AS tp, ROUND(t.sl_price,6) AS sl, t.size_usdt AS margin, t.notional_usdt AS notional, t.pattern FROM trades t {_LATERAL} WHERE t.exit_ts IS NULL ORDER BY t.entry_ts DESC",
      overrides=[col_override("unrealized_pnl", "currencyUSD"), col_override("move_pct", "percent", bg=False)])

# ════════════════════════════════════════════════════════════════════════════
# Section 7 — Activity & Health
# ════════════════════════════════════════════════════════════════════════════
section("🩺 Activity & Health")
bargauge("Signal Funnel — rejections by reason (24h)", 12, 8,
         "SELECT split_part(message,':',2) AS metric, COUNT(*) AS value FROM events WHERE category='signal' AND message LIKE 'signal_rejected:%' AND ts >= (EXTRACT(EPOCH FROM NOW())*1000)::BIGINT - 86400000 GROUP BY metric ORDER BY value DESC LIMIT 12")
table("Recent Events", 12, 8, "SELECT to_timestamp(ts/1000.0) AS time, bot_id, level, category, message FROM events ORDER BY ts DESC LIMIT 30",
      overrides=[map_override("level", {"CRITICAL": {"color": "red", "index": 0}, "ERROR": {"color": "red", "index": 1}, "INFO": {"color": "blue", "index": 2}, "WARN": {"color": "orange", "index": 3}})])
table("Bot Heartbeats", 24, 7, "SELECT bot_id, status, pid, to_timestamp(ts/1000.0) AS last_heartbeat, CASE WHEN ts >= (EXTRACT(EPOCH FROM NOW())*1000)::BIGINT - 90000 THEN 1 ELSE 0 END AS alive FROM heartbeats ORDER BY bot_id",
      overrides=[map_override("alive", {"0": {"color": "red", "index": 0, "text": "DOWN"}, "1": {"color": "green", "index": 1, "text": "UP"}})])
stat("Heartbeat age (s)", 8, 4, "SELECT COALESCE(((EXTRACT(EPOCH FROM NOW())*1000)::BIGINT - MAX(ts))/1000.0, 9999) AS age FROM heartbeats", "s",
     {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 60}, {"color": "red", "value": 90}]}, graph="area")
stat("Candles written (1h)", 8, 4, "SELECT COUNT(*) AS c FROM candles WHERE to_timestamp(ts/1000.0) >= NOW() - INTERVAL '1 hour'",
     thresholds={"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 50}]})
stat("Bots alive (<90s)", 8, 4, "SELECT COUNT(DISTINCT bot_id) AS bots FROM heartbeats WHERE ts >= (EXTRACT(EPOCH FROM NOW())*1000)::BIGINT - 90000",
     thresholds={"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 26}]})

dashboard = {
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                              "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                              "name": "Annotations & Alerts", "type": "dashboard"}]},
    "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 0, "id": None, "links": [],
    "panels": panels, "refresh": "30s", "schemaVersion": 39, "tags": ["kestrel"],
    "templating": {"list": []}, "time": {"from": "now-7d", "to": "now"}, "timepicker": {},
    "timezone": "utc", "title": "Kestrel — Multi-Bot Overview", "uid": "kestrel-main",
    "version": 1, "weekStart": "",
}

with open(OUT, "w") as f:
    json.dump(dashboard, f, indent=2)
n_p = len([p for p in panels if p["type"] != "row"])
n_s = len([p for p in panels if p["type"] == "row"])
print(f"wrote {os.path.normpath(OUT)} with {n_p} panels + {n_s} sections")
