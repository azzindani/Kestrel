#!/usr/bin/env python3
"""Dynamic tier promoter — moves strategy RECIPES between dev → lab → staging on live evidence.

Owner idea 2026-09-24: "the bots can be promoted and demoted dynamically between the
environment". Owner 2026-10-14: "dev is the place to trial and error ... any algorithms,
strategies, and time ranges are permitted ... I want to see some profit". So:

  * The unit is the RECIPE (pattern × timeframe × full params × leverage), pooled over
    every pair and every tier it runs on. Promote by BOT profit and you promote luck
    (iter 74: bot PnL halves Spearman −0.08); a recipe pooled over 34 pairs has the
    sample size to mean something.
  * The score is NET PROFIT per trade (bps of notional, after fees, slippage and funding),
    as a t-stat, with both time-halves positive. Iter 74 scored directional skill alone
    and promoted four recipes whose skill was real-looking but whose net was negative —
    all four were demoted within days. Profit is the objective, so profit is the bar.
  * Where the exit is a plain price bracket, the paired-skill replay (same bracket in the
    signal's and the opposite direction at the same moment) must also be positive: that
    rejects a long-biased recipe that merely rode the market's drift.
  * Evidence SURVIVES slate resets. Every run folds the DB's scored trades into a daily
    per-recipe aggregate file (tier_evidence.json) and judges on that, not on whatever
    the DB holds since the last reset. Before this, a reset erased the losing weeks and
    the promoter judged a recipe on its latest lucky fortnight (cci_mom 1h: −51, −37,
    then +29 bps over three slates).

The ladder (min_n scales with timeframe: ≤5m 150 · ≤30m 100 · ≤2h 60 · slower 40 trades):

  dev      always runs everything (the owner's trial-and-error tier; never pruned here)
  lab      promote   last WINDOW_DAYS: n ≥ min_n, net t ≥ 2.0, both halves net > 0, skill > 0*
           demote    since entering lab: n ≥ min_n and net t < −1.0
  staging  promote   in lab ≥ 3 days; since entering lab: n ≥ min_n, net t ≥ 1.5, halves > 0, skill > 0*
           demote    since entering staging: n ≥ min_n and net t < 0 (to lab if t ≥ −1, else dev)
  prod     NEVER automatic — real money is the owner's call (CLAUDE.md §18)
  (* skill only where the exit is a replayable price bracket)

Every timeframe, exit type and bracket geometry is eligible (owner 2026-10-14 lifts the
earlier minutes-only / high-win-only admission rules for the paper tiers). Cross-sectional
recipes (xs_*) rank their whole universe inside one env, so they are promoted on every
pair they run; the rest go to the lab's twelve / staging's eight target pairs. A promoted
bot keeps every key of its dev template (leverage included). Retired recipes
(retired_strategies.json) are never promoted. The owner's hand-added lab bots — and any
bot on global params — are never touched.

State: tier_ledger.json (when each recipe entered each tier, and every move) and
tier_evidence.json (daily per-recipe trade aggregates + bot→recipe map). The tier files
are rewritten in place; each move is an events row (category 'system'), and each run's
'tier_review' event carries the full scoreboard (every recipe's tier, evidence and
blockers) that the Grafana "Tier Promotion" section reads. Recent ledger moves a slate
reset wiped from events are re-inserted at their original ts each run.

Run (host — evaluates inside the image, then backfills new bots and restarts only the
tiers that changed):
    python3 scripts/tier_promoter.py run [--dry-run]
Inside the image (what `run` calls):
    python scripts/tier_promoter.py evaluate [--dry-run]
Fold an archived slate (a backup restored into another database) into the evidence:
    python scripts/tier_promoter.py seed --trades-db kestrel_r0924 [--extra-bots f.json ...]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

import build_curated_tiers as curated
import retired_ledger

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TIER_FILES = {"dev": "bots.json", "lab": "bots.lab.json", "staging": "bots.staging.json"}
_LEDGER = os.path.join(_ROOT, "tier_ledger.json")
_EVIDENCE = os.path.join(_ROOT, "tier_evidence.json")
_PLAN_DIR = os.path.join(_ROOT, "reports", "tier_promoter")
_PLAN = os.path.join(_PLAN_DIR, "last_plan.json")
_IMAGE = "kestrel-kestrel:latest"
_BOT_ID = "tier-promoter"
_SESSION = "tier-promoter"

_DAY_MS = 86_400_000
_TF_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
_BRACKET = ("tp_atr_multiplier", "sl_atr_multiplier", "max_hold_candles")
_EXCLUDED_REASONS = ("orphaned_crash_recovery", "manual")  # forced closes, not the recipe's exit
_TEMPLATE_OWN_KEYS = ("bot_id", "pair", "strategy")  # set per promoted bot; every other key is copied

WINDOW_DAYS = 28  # dev → lab evidence window (the evidence file outlives resets)
EVIDENCE_KEEP_DAYS = 120
T_LAB = 2.0
T_STAGING = 1.5
T_KEEP_STAGING = 0.0
T_KEEP_LAB = -1.0
MIN_DWELL_DAYS = 3
COOLDOWN_DAYS = 7
MAX_PROMOTIONS_PER_TIER = 3
EVENT_RETENTION_DAYS = 30  # cleanup.sh deletes events older than this (CLAUDE.md §19)

# Target pair lists. Lab: the twelve best live-win pairs (build_curated_tiers). Staging:
# a recipe's own lockbox-vetted list when build_curated_tiers has one, else the liquid core.
_LAB_N = 12
_STAGING_N = 8
_STAGING_CORE = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "PEPE"]


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    """One closed trade, scored. skill_bps = (same-side − opposite-side replay) / 2, or
    None when the recipe's exit cannot be replayed (indicator / trailing / pct exits)."""

    entry_ts: int
    skill_bps: Optional[float]
    gross_bps: float
    net_bps: float


@dataclass
class DayAgg:
    """One recipe's closed trades entered on one UTC day, as sums (mergeable, tiny)."""

    n: int = 0
    net_sum: float = 0.0
    net_sq: float = 0.0
    gross_sum: float = 0.0
    k: int = 0  # trades with a skill replay
    skill_sum: float = 0.0
    skill_sq: float = 0.0


@dataclass(frozen=True)
class Evidence:
    n: int
    net_bps: float
    net_t: float
    net_h1: float
    net_h2: float
    gross_bps: float
    skill_n: int
    skill_bps: float
    skill_t: float
    days: int


@dataclass(frozen=True)
class Move:
    recipe: str
    label: str
    kind: str  # promote | demote
    src: str
    dst: str  # the tier it lands in; 'dev' = back to dev-only
    reason: str
    evidence: Evidence


@dataclass
class LedgerEntry:
    label: str
    lab_since: Optional[int] = None
    staging_since: Optional[int] = None
    last_move: Optional[int] = None


@dataclass
class Plan:
    moves: list[Move] = field(default_factory=list)
    new_bots: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    changed_tiers: list[str] = field(default_factory=list)


def recipe_key(bot: dict[str, Any]) -> Optional[str]:
    """Canonical recipe id, or None when the bot is outside the promoter's scope.

    Scope: a single pattern with its own per-bot params on a known timeframe. Bots on the
    global params.json (no per-bot params — the owner's hand-added bots) are skipped.
    Leverage is part of the recipe when the bot sets it (it changes the economics); the
    key is unchanged for bots that do not, so ledger rows from before it stay valid.
    """
    patterns = bot.get("patterns") or []
    params = bot.get("params") or {}
    tf = bot.get("timeframe_entry")
    if len(patterns) != 1 or tf not in _TF_MS or not params:
        return None
    key: dict[str, Any] = {"pattern": patterns[0], "tf": tf, "params": params}
    if "leverage" in bot:
        key["leverage"] = bot["leverage"]
    return json.dumps(key, sort_keys=True)


def recipe_parts(recipe: str) -> dict[str, Any]:
    return json.loads(recipe)


def replayable(params: dict[str, Any]) -> bool:
    """True when the exit is a plain ATR bracket the skill replay mirrors exactly
    (SimulationExecution.check_exits: close-resolved TP/SL, timeout after max_hold)."""
    return (
        all(k in params for k in _BRACKET)
        and not params.get("trailing_enabled")
        and not params.get("indicator_exit_mode")
        and not params.get("tp_sl_pct_enabled")
        and float(params["tp_atr_multiplier"]) < 10.0
    )


def cross_sectional(recipe: str) -> bool:
    """xs_* patterns rank their whole universe inside one env — they need every pair."""
    return str(recipe_parts(recipe)["pattern"]).startswith("xs_")


def min_n(recipe: str) -> int:
    ms = _TF_MS[recipe_parts(recipe)["tf"]]
    if ms <= 300_000:
        return 150
    if ms <= 1_800_000:
        return 100
    if ms <= 7_200_000:
        return 60
    return 40


def walk_exit(
    closes: Sequence[float], idx: int, sign: int, tp_dist: float, sl_dist: float, max_hold: int
) -> Optional[float]:
    """Gross bps of a bracket entered at closes[idx], resolved the way the sim does it.

    Mirror of SimulationExecution.check_exits: each later close is checked for TP, then
    SL; the position times out on the max_hold-th close; the fill is that close. None
    when the series ends before the trade resolves.
    """
    entry = closes[idx]
    tp, sl = entry + sign * tp_dist, entry - sign * sl_dist
    for k in range(1, max_hold + 1):
        if idx + k >= len(closes):
            return None
        px = closes[idx + k]
        if sign * (px - tp) >= 0 or sign * (px - sl) <= 0 or k == max_hold:
            return sign * (px - entry) / entry * 1e4
    return None


def paired_skill(closes: Sequence[float], atr: float, idx: int, sign: int, params: dict[str, Any]) -> Optional[float]:
    """Half the gap between trading the signal's side and the opposite side, same moment."""
    tp_d = float(params["tp_atr_multiplier"]) * atr
    sl_d = float(params["sl_atr_multiplier"]) * atr
    hold = int(params["max_hold_candles"])
    same = walk_exit(closes, idx, sign, tp_d, sl_d, hold)
    opp = walk_exit(closes, idx, -sign, tp_d, sl_d, hold)
    if same is None or opp is None:
        return None
    return (same - opp) / 2.0


def entry_index(candle_ts: Sequence[int], tf_ms: int, entry_ts: int) -> Optional[int]:
    """Index of the latest candle that had CLOSED by entry_ts (the one the signal fired on)."""
    lo, hi = 0, len(candle_ts)
    while lo < hi:
        mid = (lo + hi) // 2
        if candle_ts[mid] + tf_ms <= entry_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1 if lo > 0 else None


def day_of(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def day_start_ms(day: str) -> int:
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def aggregate(samples: dict[str, list[Sample]]) -> dict[str, dict[str, DayAgg]]:
    """Scored trades → per-recipe, per-entry-day sums."""
    out: dict[str, dict[str, DayAgg]] = {}
    for recipe, rows in samples.items():
        days = out.setdefault(recipe, {})
        for s in rows:
            a = days.setdefault(day_of(s.entry_ts), DayAgg())
            a.n += 1
            a.net_sum += s.net_bps
            a.net_sq += s.net_bps**2
            a.gross_sum += s.gross_bps
            if s.skill_bps is not None:
                a.k += 1
                a.skill_sum += s.skill_bps
                a.skill_sq += s.skill_bps**2
    return out


def merge_days(
    stored: dict[str, dict[str, DayAgg]], fresh: dict[str, dict[str, DayAgg]], keep_from: str
) -> dict[str, dict[str, DayAgg]]:
    """Union of two evidence stores; on a (recipe, day) in both, the one with more trades
    wins. A day's closed trades only ever grow, so the larger count is the more complete
    one — and after a slate reset the DB holds FEWER, so the stored day survives it.
    Days before keep_from are dropped."""
    out: dict[str, dict[str, DayAgg]] = {}
    for src in (stored, fresh):
        for recipe, days in src.items():
            tgt = out.setdefault(recipe, {})
            for day, agg in days.items():
                if day >= keep_from and (day not in tgt or agg.n > tgt[day].n):
                    tgt[day] = agg
    return {r: d for r, d in out.items() if d}


def _t(n: int, s: float, sq: float) -> tuple[float, float]:
    """(mean, t-stat) from count, sum and sum of squares."""
    if n == 0:
        return 0.0, 0.0
    mean = s / n
    if n < 2:
        return mean, 0.0
    var = max(sq - n * mean * mean, 0.0) / (n - 1)
    return mean, (mean / math.sqrt(var / n) if var > 0 else 0.0)


def summarize(days: dict[str, DayAgg], since_ms: Optional[int], until_ms: int) -> Evidence:
    """Evidence over the days in (since, until]. since None/0 = everything stored.

    A day counts only when it starts after `since`'s day (the day a recipe moved is
    split between tiers, so it is left out of the new tier's evidence). Halves split the
    days in time order at half the trades."""
    lo = day_of(since_ms) if since_ms else None
    hi = day_of(until_ms)
    chosen = sorted((d, a) for d, a in days.items() if (lo is None or d > lo) and d <= hi)
    n = sum(a.n for _, a in chosen)
    net_mean, net_t = _t(n, sum(a.net_sum for _, a in chosen), sum(a.net_sq for _, a in chosen))
    k = sum(a.k for _, a in chosen)
    sk_mean, sk_t = _t(k, sum(a.skill_sum for _, a in chosen), sum(a.skill_sq for _, a in chosen))
    first: list[DayAgg] = []
    acc = 0
    for _, a in chosen:
        if acc >= n / 2:
            break
        first.append(a)
        acc += a.n
    second = [a for _, a in chosen[len(first) :]]

    def mean(group: list[DayAgg]) -> float:
        m = sum(a.n for a in group)
        return sum(a.net_sum for a in group) / m if m else 0.0

    return Evidence(
        n=n,
        net_bps=net_mean,
        net_t=net_t,
        net_h1=mean(first),
        net_h2=mean(second),
        gross_bps=sum(a.gross_sum for _, a in chosen) / n if n else 0.0,
        skill_n=k,
        skill_bps=sk_mean,
        skill_t=sk_t,
        days=len(chosen),
    )


def blockers(recipe: str, ev: Evidence, t_min: float) -> list[str]:
    """Every promotion condition this evidence fails (empty = it clears the bar)."""
    out = []
    need = min_n(recipe)
    if ev.n < need:
        out.append(f"n {ev.n}<{need}")
    if ev.net_t < t_min:
        out.append(f"net t {ev.net_t:+.2f}<{t_min}")
    if ev.net_h1 <= 0:
        out.append("1st half net≤0")
    if ev.net_h2 <= 0:
        out.append("2nd half net≤0")
    if replayable(recipe_parts(recipe)["params"]) and ev.skill_n and ev.skill_bps <= 0:
        out.append("skill≤0 (drift, not direction)")
    return out


def _fmt(ev: Evidence) -> str:
    return (
        f"n={ev.n} net={ev.net_bps:+.2f}bps t={ev.net_t:+.2f} halves={ev.net_h1:+.1f}/{ev.net_h2:+.1f} "
        f"skill={ev.skill_bps:+.2f} days={ev.days}"
    )


def _since(e: LedgerEntry, tier: str) -> Optional[int]:
    return e.staging_since if tier == "staging" else e.lab_since


def decide(
    tiers: dict[str, set[str]],
    evidence: dict[str, dict[str, DayAgg]],
    ledger: dict[str, LedgerEntry],
    labels: dict[str, str],
    retired: set[str],
    now_ms: int,
) -> list[Move]:
    """Every tier move this run warrants. Pure — the caller applies them."""
    moves: list[Move] = []
    cooled = {
        r for r, e in ledger.items() if e.last_move is not None and now_ms - e.last_move < COOLDOWN_DAYS * _DAY_MS
    }

    def entry(recipe: str) -> LedgerEntry:
        return ledger.get(recipe) or LedgerEntry(label=labels[recipe])

    def ev_since(recipe: str, since: Optional[int]) -> Evidence:
        return summarize(evidence.get(recipe, {}), since, now_ms)

    # 1. staging: demote first, then consider lab recipes for promotion
    for r in sorted(tiers["staging"] - cooled):
        ev = ev_since(r, entry(r).staging_since)
        if ev.n >= min_n(r) and ev.net_t < T_KEEP_STAGING:
            dst = "lab" if ev.net_t >= T_KEEP_LAB else "dev"
            moves.append(Move(r, labels[r], "demote", "staging", dst, f"staging t<{T_KEEP_STAGING}: {_fmt(ev)}", ev))

    staged = 0
    for r in sorted(tiers["lab"] - tiers["staging"] - cooled - retired):
        e = entry(r)
        if e.lab_since is not None and now_ms - e.lab_since < MIN_DWELL_DAYS * _DAY_MS:
            continue
        ev = ev_since(r, e.lab_since)
        if staged < MAX_PROMOTIONS_PER_TIER and not blockers(r, ev, T_STAGING):
            moves.append(Move(r, labels[r], "promote", "lab", "staging", f"lab→staging: {_fmt(ev)}", ev))
            staged += 1

    # 2. lab: demote on a losing post-entry record (a staging copy is demoted above)
    demoted = {m.recipe for m in moves if m.kind == "demote"}
    for r in sorted(tiers["lab"] - cooled - demoted):
        ev = ev_since(r, entry(r).lab_since)
        if ev.n >= min_n(r) and ev.net_t < T_KEEP_LAB:
            moves.append(Move(r, labels[r], "demote", "lab", "dev", f"lab t<{T_KEEP_LAB}: {_fmt(ev)}", ev))

    # 3. dev → lab, strongest first
    candidates = []
    for r in tiers["dev"] - tiers["lab"] - tiers["staging"] - cooled - retired:
        ev = ev_since(r, now_ms - WINDOW_DAYS * _DAY_MS)
        if not blockers(r, ev, T_LAB):
            candidates.append((ev.net_t, r, ev))
    for _, r, ev in sorted(candidates, reverse=True)[:MAX_PROMOTIONS_PER_TIER]:
        moves.append(Move(r, labels[r], "promote", "dev", "lab", f"dev→lab: {_fmt(ev)}", ev))
    return moves


def scoreboard(
    tiers: dict[str, set[str]],
    evidence: dict[str, dict[str, DayAgg]],
    ledger: dict[str, LedgerEntry],
    labels: dict[str, str],
    retired: set[str],
    moves: Sequence[Move],
    now_ms: int,
) -> list[dict[str, Any]]:
    """One row per recipe: its tier, the evidence its next gate reads, and what blocks it.

    Pure — mirrors decide(): dev recipes are judged on the last WINDOW_DAYS against the
    lab bar; lab recipes on evidence since entering lab against the staging bar; staging
    recipes on evidence since entering staging against the keep line.
    """
    moved = {m.recipe: m for m in moves}
    rows = []
    for r, label in labels.items():
        e = ledger.get(r) or LedgerEntry(label=label)
        cooled = e.last_move is not None and now_ms - e.last_move < COOLDOWN_DAYS * _DAY_MS
        days = evidence.get(r, {})
        if r in tiers["staging"]:
            tier, ev = "staging", summarize(days, e.staging_since, now_ms)
            gate, t_min = f"keep: net t≥{T_KEEP_STAGING}", T_KEEP_STAGING
            block = (
                [f"net t {ev.net_t:+.2f}<{T_KEEP_STAGING}"] if ev.n >= min_n(r) and ev.net_t < T_KEEP_STAGING else []
            )
        elif r in tiers["lab"]:
            tier, ev = "lab", summarize(days, e.lab_since, now_ms)
            gate, t_min = f"→staging: net t≥{T_STAGING}", T_STAGING
            block = blockers(r, ev, T_STAGING)
            if e.lab_since is not None and now_ms - e.lab_since < MIN_DWELL_DAYS * _DAY_MS:
                block.append(f"lab dwell<{MIN_DWELL_DAYS}d")
        else:
            tier, ev = "dev", summarize(days, now_ms - WINDOW_DAYS * _DAY_MS, now_ms)
            gate, t_min = f"→lab: net t≥{T_LAB}", T_LAB
            block = blockers(r, ev, T_LAB)
        if cooled:
            block.append(f"cooldown {COOLDOWN_DAYS}d")
        if r in retired:
            block.append("retired")
        m = moved.get(r)
        status = f"{m.kind}d → {m.dst}" if m else ("eligible" if not block else "blocked")
        parts = recipe_parts(r)
        rows.append(
            {
                "label": label,
                "tier": tier,
                "tf": parts["tf"],
                "gate": gate,
                "t_needed": t_min,
                "min_n": min_n(r),
                "status": status,
                "blockers": "; ".join(block),
                **asdict(ev),
            }
        )
    order = {"staging": 0, "lab": 1, "dev": 2}
    return sorted(rows, key=lambda row: (order[row["tier"]], -row["net_t"]))


def tier_label(recipe: str, bots_by_tier: dict[str, list[dict[str, Any]]]) -> str:
    """Label a recipe runs under in lab/staging: an existing curated label when it has one,
    else 'cur_' + its dev label with the hw33 bracket tag dropped (hw33_tsmom_z → cur_tsmom_z)."""
    for tier in ("lab", "staging"):
        found = sorted(b["strategy"] for b in bots_by_tier.get(tier, []) if recipe_key(b) == recipe)
        if found:
            return found[0]
    dev = sorted(b["strategy"] for b in bots_by_tier.get("dev", []) if recipe_key(b) == recipe)
    base = dev[0] if dev else recipe_parts(recipe)["pattern"]
    core = "_".join(tok for tok in base.split("_") if tok != "hw33")
    label = core if core.startswith("cur_") else f"cur_{core}"
    # 5m labels predate multi-timeframe promotion (ledger rows use them); every other
    # timeframe is suffixed so 1h sma_cross and 5m hw33_sma_cross never share a label.
    tf = recipe_parts(recipe)["tf"]
    return label if tf == "5m" or label.endswith(f"_{tf}") else f"{label}_{tf}"


def target_pairs(available: Sequence[str], preferred: Sequence[str], k: int) -> list[str]:
    """Up to k symbols: preferred order first, then the recipe's other pairs alphabetically.
    Restricted to pairs the recipe already runs on."""
    have = set(available)
    chosen = [p for p in preferred if p in have][:k]
    for p in sorted(have):
        if len(chosen) >= k:
            break
        if p not in chosen:
            chosen.append(p)
    return chosen


def _sym(pair: str) -> str:
    return pair.split("/")[0]


def make_bot(env: str, sym: str, label: str, template: dict[str, Any]) -> dict[str, Any]:
    """A tier copy of a dev bot: every template key (leverage, regime tf, buckets, ...)
    carried over; only the identity — bot_id, pair, strategy label — is rewritten."""
    tf = template["timeframe_entry"]
    bot = {k: v for k, v in template.items() if k not in _TEMPLATE_OWN_KEYS}
    bot.update(
        {
            "bot_id": f"{env}-{sym}USDT-{tf}-{label}-01",
            "pair": f"{sym}/USDT",
            "strategy": label,
            "patterns": list(template["patterns"]),
            "params": dict(template["params"]),
        }
    )
    bot.setdefault("timeframe_regime", tf)
    bot.setdefault("max_active_buckets", 1)
    return bot


def apply_moves(
    bots_by_tier: dict[str, list[dict[str, Any]]],
    moves: Sequence[Move],
    keep_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """New lab/staging bot lists after the moves. dev is never edited."""
    out = {tier: list(bots) for tier, bots in bots_by_tier.items()}
    templates: dict[str, dict[str, Any]] = {}
    pairs: dict[str, set[str]] = {}
    for tier in ("dev", "lab", "staging"):
        for b in bots_by_tier.get(tier, []):
            r = recipe_key(b)
            if r is None:
                continue
            templates.setdefault(r, b)
            pairs.setdefault(r, set()).add(_sym(b["pair"]))

    def drop(tier: str, recipe: str) -> None:
        out[tier] = [b for b in out[tier] if b["bot_id"] in keep_ids or recipe_key(b) != recipe]

    def add(tier: str, recipe: str, label: str) -> None:
        if cross_sectional(recipe):
            syms = sorted(pairs[recipe])
        elif tier == "lab":
            syms = target_pairs(sorted(pairs[recipe]), curated._LAB_PAIRS, _LAB_N)
        else:
            preferred = curated._STAGING_PAIRS.get(label, _STAGING_CORE)
            syms = target_pairs(sorted(pairs[recipe]), preferred, _STAGING_N)
        have = {b["bot_id"] for b in out[tier]}
        for sym in syms:
            bot = make_bot(tier, sym, label, templates[recipe])
            if bot["bot_id"] not in have:
                out[tier].append(bot)

    for m in moves:
        if m.kind == "promote":
            add(m.dst, m.recipe, m.label)
        else:
            drop(m.src, m.recipe)
            if m.src == "staging" and m.dst == "lab":
                add("lab", m.recipe, m.label)
            if m.dst == "dev":  # out of both curated tiers
                drop("lab", m.recipe)
                drop("staging", m.recipe)
    return out


def update_ledger(ledger: dict[str, LedgerEntry], moves: Sequence[Move], now_ms: int) -> dict[str, LedgerEntry]:
    out = {r: LedgerEntry(**asdict(e)) for r, e in ledger.items()}
    for m in moves:
        e = out.setdefault(m.recipe, LedgerEntry(label=m.label))
        e.last_move = now_ms
        if m.dst == "lab":
            e.lab_since = e.lab_since if m.src == "staging" and e.lab_since is not None else now_ms
        if m.dst == "staging":
            e.staging_since = now_ms
        if m.src == "staging":
            e.staging_since = None
        if m.dst == "dev":
            e.lab_since = None
            e.staging_since = None
    return out


def seed_ledger(
    ledger: dict[str, LedgerEntry], tiers: dict[str, set[str]], labels: dict[str, str]
) -> dict[str, LedgerEntry]:
    """Recipes already sitting in lab/staging with no ledger row count ALL their evidence
    (since=0): they have held the tier at least since the last reset."""
    out = {r: LedgerEntry(**asdict(e)) for r, e in ledger.items()}
    for r in tiers["lab"] | tiers["staging"]:
        e = out.setdefault(r, LedgerEntry(label=labels[r]))
        if r in tiers["lab"] and e.lab_since is None:
            e.lab_since = 0
        if r in tiers["staging"] and e.staging_since is None:
            e.staging_since = 0
    return out


_EVENT_MATCH_MS = 3_600_000  # a ledger move and its live event row are written ~seconds apart


def missing_move_events(
    history: Sequence[dict[str, Any]], logged: Sequence[tuple[int, str, str, str, str]], since_ms: int
) -> list[dict[str, Any]]:
    """Ledger moves since since_ms with no events row. Pure.

    A row matches on (message, label, src, dst) within _EVENT_MATCH_MS: the ledger
    stamps a move at the run's start, the live event at write time, so exact-ts
    matching would duplicate every move the run itself logged.
    """
    out = []
    for h in history:
        key = (f"tier_{h['kind']}d", h["label"], h["src"], h["dst"])
        if h["ts"] < since_ms:
            continue
        if not any(row[1:] == key and abs(row[0] - int(h["ts"])) < _EVENT_MATCH_MS for row in logged):
            out.append(h)
    return out


def score_trades(
    trades: Sequence[dict[str, Any]],
    series: dict[tuple[str, str], tuple[list[int], list[float], list[float]]],
    bot_recipe: dict[str, str],
) -> dict[str, list[Sample]]:
    """Group trades by recipe and score each. Pure. Trades on unknown bots are skipped;
    a replayable recipe's trade with no candle context keeps its net and gets no skill."""
    out: dict[str, list[Sample]] = {}
    for t in trades:
        recipe = bot_recipe.get(t["bot_id"])
        if recipe is None:
            continue
        params = recipe_parts(recipe)["params"]
        skill: Optional[float] = None
        s = series.get((t["pair"], t["timeframe"]))
        if s is not None and replayable(params) and t["timeframe"] in _TF_MS:
            ts, closes, atrs = s
            idx = entry_index(ts, _TF_MS[t["timeframe"]], int(t["entry_ts"]))
            if idx is not None and atrs[idx] > 0:
                skill = paired_skill(closes, atrs[idx], idx, 1 if t["direction"] == "long" else -1, params)
        notional = float(t["notional_usdt"])
        out.setdefault(recipe, []).append(
            Sample(
                entry_ts=int(t["entry_ts"]),
                skill_bps=skill,
                gross_bps=float(t["pnl_gross_usdt"]) / notional * 1e4,
                net_bps=float(t["pnl_net_usdt"]) / notional * 1e4,
            )
        )
    return out


# ---------------------------------------------------------------------------
# File I/O (host and container)
# ---------------------------------------------------------------------------


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_json(path: str, data: Any, indent: Optional[int] = 2) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=indent)
        fh.write("\n")
    os.replace(tmp, path)


def _bot_list(raw: Any) -> list[dict[str, Any]]:
    return raw if isinstance(raw, list) else list(raw.get("bots", []))


def load_tiers() -> dict[str, list[dict[str, Any]]]:
    return {tier: _bot_list(_load_json(os.path.join(_ROOT, name), [])) for tier, name in _TIER_FILES.items()}


def load_ledger() -> tuple[dict[str, LedgerEntry], list[dict[str, Any]]]:
    raw = _load_json(_LEDGER, {})
    entries = {r: LedgerEntry(**e) for r, e in raw.get("recipes", {}).items()}
    return entries, list(raw.get("history", []))


def save_ledger(entries: dict[str, LedgerEntry], history: list[dict[str, Any]]) -> None:
    _save_json(
        _LEDGER,
        {
            "_doc": "scripts/tier_promoter.py state: when each recipe entered lab/staging (unix ms; "
            "0 = seeded, all evidence counts) and every move. Recipe = pattern × tf × params.",
            "recipes": {r: asdict(e) for r, e in sorted(entries.items())},
            "history": history,
        },
    )


def load_evidence() -> tuple[dict[str, dict[str, DayAgg]], dict[str, str]]:
    raw = _load_json(_EVIDENCE, {})
    days = {r: {d: DayAgg(**a) for d, a in ds.items()} for r, ds in raw.get("days", {}).items()}
    return days, dict(raw.get("bots", {}))


def save_evidence(days: dict[str, dict[str, DayAgg]], bots: dict[str, str]) -> None:
    _save_json(
        _EVIDENCE,
        {
            "_doc": "scripts/tier_promoter.py evidence: per recipe, per UTC entry day, sums of net/gross/skill "
            "bps over closed trades (survives slate resets; a day keeps its most complete version), "
            "and every bot_id ever mapped to a recipe.",
            "days": {r: {d: asdict(a) for d, a in sorted(ds.items())} for r, ds in sorted(days.items())},
            "bots": dict(sorted(bots.items())),
        },
        indent=None,
    )


def retired_recipes(bots_by_tier: dict[str, list[dict[str, Any]]]) -> set[str]:
    ledger = _load_json(os.path.join(_ROOT, "retired_strategies.json"), {})
    out = set()
    for bots in bots_by_tier.values():
        for b in bots:
            r = recipe_key(b)
            if r is not None and any(retired_ledger._matches(b, e) for e in ledger.get("retired", [])):
                out.add(r)
    return out


def bot_map(bot_lists: Sequence[Sequence[dict[str, Any]]]) -> dict[str, str]:
    out = {}
    for bots in bot_lists:
        for b in bots:
            r = recipe_key(b)
            if r is not None:
                out[b["bot_id"]] = r
    return out


# ---------------------------------------------------------------------------
# DB I/O (inside the image)
# ---------------------------------------------------------------------------


async def _fetch_trades(conn: Any, since_ms: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT bot_id, pair, timeframe, direction, entry_ts, notional_usdt, pnl_gross_usdt, pnl_net_usdt
        FROM trades
        WHERE exit_ts IS NOT NULL AND entry_ts >= $1
          AND close_reason <> ALL($2::text[]) AND notional_usdt > 0
        """,
        since_ms,
        list(_EXCLUDED_REASONS),
    )
    return [dict(r) for r in rows]


_SERIES_TRIES = 3


async def _fetch_series(
    conn: Any, sources: dict[tuple[str, str], list[str]], since_ms: int
) -> dict[tuple[str, str], tuple[list[int], list[float], list[float]]]:
    """One close/ATR series per (pair, tf), read from the first candidate bot that has
    candles. Candidates are bot_ids that traded it (dev first — resets keep candles, and
    dev bots run continuously), so each read is a (bot_id, pair, timeframe, ts) index scan
    rather than a whole-table aggregate (which hit the pool's statement timeout)."""
    series: dict[tuple[str, str], tuple[list[int], list[float], list[float]]] = {}
    for (pair, tf), bot_ids in sorted(sources.items()):
        rows: list[Any] = []
        for bot_id in bot_ids[:_SERIES_TRIES]:
            rows = await conn.fetch(
                "SELECT ts, close, atr14 FROM candles WHERE bot_id = $1 AND pair = $2 AND timeframe = $3 "
                "AND ts >= $4 ORDER BY ts",
                bot_id,
                pair,
                tf,
                since_ms - _DAY_MS,
            )
            if rows:
                break
        if not rows:
            continue
        series[(pair, tf)] = (
            [int(r["ts"]) for r in rows],
            [float(r["close"]) for r in rows],
            [float(r["atr14"]) if r["atr14"] is not None else 0.0 for r in rows],
        )
    return series


async def load_samples(trades_conn: Any, candles_conn: Any, bot_recipe: dict[str, str], since_ms: int):
    """Closed trades since since_ms, each scored (net always; skill where replayable)."""
    trades = [t for t in await _fetch_trades(trades_conn, since_ms) if t["bot_id"] in bot_recipe]
    sources: dict[tuple[str, str], set[str]] = {}
    for t in trades:
        if replayable(recipe_parts(bot_recipe[t["bot_id"]])["params"]) and t["timeframe"] in _TF_MS:
            sources.setdefault((t["pair"], t["timeframe"]), set()).add(t["bot_id"])
    ordered = {k: sorted(v, key=lambda b: (not b.startswith("dev-"), b)) for k, v in sources.items()}
    first = min((int(t["entry_ts"]) for t in trades), default=since_ms)
    series = await _fetch_series(candles_conn, ordered, first)
    return score_trades(trades, series, bot_recipe)


async def sync_move_events(env: str, history: Sequence[dict[str, Any]], now_ms: int) -> int:
    """Re-insert recent ledger moves a reset wiped from events, at their original ts,
    so the Grafana tier timeline survives slate resets (events retention is 30 days)."""
    from src.db import connection as db_conn

    since = now_ms - EVENT_RETENTION_DAYS * _DAY_MS
    async with db_conn.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ts, message, payload->>'label' AS label, payload->>'src' AS src, payload->>'dst' AS dst "
            "FROM events WHERE bot_id = $1 AND message IN ('tier_promoted', 'tier_demoted') AND ts >= $2",
            _BOT_ID,
            since - _EVENT_MATCH_MS,
        )
        logged = [(int(r["ts"]), r["message"], r["label"], r["src"], r["dst"]) for r in rows]
        todo = missing_move_events(history, logged, since)
        for h in todo:
            await conn.execute(
                "INSERT INTO events (bot_id, session_id, env, ts, level, category, message, payload) "
                "VALUES ($1, $2, $3, $4, 'INFO', 'system', $5, $6)",
                _BOT_ID,
                _SESSION,
                env,
                int(h["ts"]),
                f"tier_{h['kind']}d",
                json.dumps({k: h[k] for k in ("label", "src", "dst", "reason")} | dict(h["evidence"])),
            )
    return len(todo)


def _app_config():
    from dotenv import load_dotenv

    from src.config import AppConfig

    load_dotenv()
    return AppConfig.from_mapping(os.environ)


async def seed(trades_db: str, extra_bots: Sequence[str]) -> int:
    """Fold an archived slate's trades (a backup restored into trades_db) into the
    evidence file. Candles come from the live DB (resets keep candles)."""
    import asyncpg

    from src.db import connection as db_conn

    cfg = _app_config()
    now_ms = int(time.time() * 1000)
    stored, bots = load_evidence()
    extra = [_bot_list(_load_json(p, [])) for p in extra_bots]
    bot_recipe = bots | bot_map(extra + list(load_tiers().values()))
    other = await asyncpg.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=trades_db,
    )
    await db_conn.init_pool(cfg)
    try:
        async with db_conn.acquire() as conn:
            samples = await load_samples(other, conn, bot_recipe, 0)
    finally:
        await other.close()
        await db_conn.close_pool()
    keep_from = day_of(now_ms - EVIDENCE_KEEP_DAYS * _DAY_MS)
    save_evidence(merge_days(stored, aggregate(samples), keep_from), bot_recipe)
    return sum(len(v) for v in samples.values())


async def evaluate(dry_run: bool) -> Plan:
    """Container shell: DB trades → evidence file → decisions → tier files + ledger + events."""
    from src.db import connection as db_conn
    from src.db import writer as db

    cfg = _app_config()
    now_ms = int(time.time() * 1000)
    bots_by_tier = load_tiers()
    tiers: dict[str, set[str]] = {}
    for tier, bots in bots_by_tier.items():
        tiers[tier] = {r for r in (recipe_key(b) for b in bots) if r is not None}
    labels = {r: tier_label(r, bots_by_tier) for r in set().union(*tiers.values())}
    entries, history = load_ledger()
    entries = seed_ledger(entries, tiers, labels)
    stored, known_bots = load_evidence()
    bot_recipe = known_bots | bot_map(list(bots_by_tier.values()))
    retired = retired_recipes(bots_by_tier)

    await db_conn.init_pool(cfg)
    try:
        async with db_conn.acquire() as conn:
            samples = await load_samples(conn, conn, bot_recipe, now_ms - WINDOW_DAYS * _DAY_MS)
        evidence = merge_days(stored, aggregate(samples), day_of(now_ms - EVIDENCE_KEEP_DAYS * _DAY_MS))
        moves = decide(tiers, evidence, entries, labels, retired, now_ms)
        new_tiers = apply_moves(bots_by_tier, moves, set(curated._LAB_KEEP_IDS))
        plan = Plan(moves=moves)
        for tier in ("lab", "staging"):
            old_ids = {b["bot_id"] for b in bots_by_tier[tier]}
            new_ids = {b["bot_id"] for b in new_tiers[tier]}
            if old_ids != new_ids:
                plan.changed_tiers.append(tier)
                plan.new_bots[tier] = [b for b in new_tiers[tier] if b["bot_id"] not in old_ids]
        board = scoreboard(tiers, evidence, entries, labels, retired, moves, now_ms)

        if not dry_run:
            save_evidence(evidence, bot_recipe | bot_map(list(new_tiers.values())))
            for tier in plan.changed_tiers:
                _save_json(os.path.join(_ROOT, _TIER_FILES[tier]), new_tiers[tier])
            await sync_move_events(cfg.env.value, history, now_ms)
            history += [{"ts": now_ms, **{k: v for k, v in asdict(m).items() if k != "recipe"}} for m in moves]
            save_ledger(update_ledger(entries, moves, now_ms), history)
            for m in moves:
                await db.write_event(
                    _BOT_ID,
                    _SESSION,
                    cfg.env.value,
                    "INFO",
                    "system",
                    f"tier_{m.kind}d",
                    {"label": m.label, "src": m.src, "dst": m.dst, "reason": m.reason, **asdict(m.evidence)},
                )
            await db.write_event(
                _BOT_ID,
                _SESSION,
                cfg.env.value,
                "INFO",
                "system",
                "tier_review",
                {
                    "moves": len(moves),
                    "changed_tiers": plan.changed_tiers,
                    "recipes_scored": len(board),
                    "window_days": WINDOW_DAYS,
                    "scoreboard": board,
                },
            )
        os.makedirs(_PLAN_DIR, exist_ok=True)
        _save_json(
            _PLAN,
            {
                "ts": now_ms,
                "dry_run": dry_run,
                "moves": [{k: v for k, v in asdict(m).items() if k != "recipe"} for m in moves],
                "changed_tiers": plan.changed_tiers,
                "new_bots": plan.new_bots,
                "scoreboard": board,
            },
        )
        return plan
    finally:
        await db_conn.close_pool()


# ---------------------------------------------------------------------------
# Host shell
# ---------------------------------------------------------------------------


def _docker_python(args: list[str], env: str) -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "kestrel_net",
            "--env-file",
            ".env",
            "-e",
            "DB_HOST=postgres",
            "-e",
            "DB_PORT=5432",
            "-e",
            f"ENV={env}",
            "-e",
            "PYTHONPATH=/app",
            "-v",
            f"{_ROOT}:/app",
            "-w",
            "/app",
            "--entrypoint",
            "python",
            _IMAGE,
            *args,
        ],
        cwd=_ROOT,
        check=True,
    )


def run(dry_run: bool) -> int:
    """Host: evaluate in the image; for each changed tier backfill its new bots and restart it."""
    _docker_python(["scripts/tier_promoter.py", "evaluate", *(["--dry-run"] if dry_run else [])], "dev")
    plan = _load_json(_PLAN, {})
    for m in plan.get("moves", []):
        print(f"{m['kind']:8s} {m['label']:28s} {m['src']} -> {m['dst']}   {m['reason']}")
    if dry_run or not plan.get("changed_tiers"):
        print("no tier changes applied" if not dry_run else "dry run — nothing written")
        return 0
    for tier in plan["changed_tiers"]:
        new = plan["new_bots"].get(tier, [])
        if new:
            path = os.path.join(_PLAN_DIR, f"new_{tier}_bots.json")
            _save_json(path, new)
            _docker_python(
                ["scripts/backfill_history.py", "--bots", os.path.relpath(path, _ROOT), "--source", "gate"], tier
            )
    subprocess.run(
        ["docker", "compose", "--profile", "lab", "--profile", "staging", "restart", *plan["changed_tiers"]],
        cwd=_ROOT,
        check=True,
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "evaluate", "seed"])
    ap.add_argument("--dry-run", action="store_true", help="decide and report, write nothing")
    ap.add_argument("--trades-db", help="seed: database holding the archived slate's trades table")
    ap.add_argument("--extra-bots", nargs="*", default=[], help="seed: older tier files mapping retired bot_ids")
    args = ap.parse_args()
    if args.cmd == "evaluate":
        asyncio.run(evaluate(args.dry_run))
        return 0
    if args.cmd == "seed":
        if not args.trades_db:
            ap.error("seed needs --trades-db")
        asyncio.run(seed(args.trades_db, args.extra_bots))
        return 0
    return run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
