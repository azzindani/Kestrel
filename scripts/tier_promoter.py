#!/usr/bin/env python3
"""Dynamic tier promoter — moves strategy RECIPES between dev → lab → staging on live evidence.

Owner idea 2026-09-24: "the bots can be promoted and demoted dynamically between the
environment". What the 5-day live data said about HOW (iter 74):

  * Promote by BOT profit and you promote luck. Bots ranked on the first half of the
    slate: the best quintile went +16 → −10 bps/trade in the second half, the worst
    −35 → −0.2 (Spearman −0.08). A bot holds ~15 trades against an 87 bps per-trade
    std — its PnL is noise next to the ~7 bps cost.
  * Win rate is not evidence either. Under the hw33 bracket (tp 0.5 / sl 1.5 ATR) a
    RANDOM entry wins 61.3%; the fleet's signals won 60.9%.
  * What persists is a recipe's DIRECTIONAL SKILL, pooled over all its pairs: at each
    entry, replay the same bracket in the signal's direction and in the opposite one,
    and take half the difference. That cancels the market's drift and the bracket's
    built-in win rate, leaving only "did it pick the right side". Per-pattern skill
    ranks with Spearman +0.72 across the two halves.

So the unit is the RECIPE (pattern × timeframe × full params — the same thing the dev
`hw33_x` and lab/staging `cur_x` labels run), the score is the paired-skill t-stat,
and every move needs a minimum sample, both halves agreeing, a lab dwell, fresh
post-promotion evidence for staging, and a cooldown. The ladder:

  dev      always runs everything (the owner's "always expanding" tier; never pruned here)
  lab      promote   n ≥ 150, skill t ≥ 2.5, both halves > 0, realised gross > 0
           demote    n ≥ 150 since it entered lab and skill t < 0
  staging  promote   in lab ≥ 3 days; since entering lab: n ≥ 150, t ≥ 2.0, halves > 0, gross > 0
           demote    n ≥ 150 since it entered staging and t < 1.0 (falls back to lab if t ≥ 0)
  prod     NEVER automatic — real money is the owner's call (CLAUDE.md §18)

Owner tier rule (2026-07-24 / 08-24): lab and staging are HIGH-WIN tiers, so a recipe is
promoted into them only on a high-win bracket design — tp/sl ≤ HIGH_WIN_MAX_TP_SL (hw33 is
0.33). Wider, expectancy-style brackets stay in dev, the unlimited-test tier.

Only minutes recipes (1m–5m, §13) with a plain price bracket are eligible: the skill
replay mirrors SimulationExecution.check_exits (close-resolved TP/SL, timeout after
max_hold closes), so trailing / indicator exits are out of scope. Retired recipes
(retired_strategies.json) are never promoted. The owner's hand-added lab bots are
never touched.

State: tier_ledger.json (when each recipe entered each tier, and every move). The
tier files are rewritten in place; each move is an events row (category 'system').

Run (host — evaluates inside the image, then backfills new bots and restarts only the
tiers that changed):
    python3 scripts/tier_promoter.py run [--dry-run]
Inside the image (what `run` calls):
    python scripts/tier_promoter.py evaluate [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
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
_PLAN_DIR = os.path.join(_ROOT, "reports", "tier_promoter")
_PLAN = os.path.join(_PLAN_DIR, "last_plan.json")
_IMAGE = "kestrel-kestrel:latest"
_BOT_ID = "tier-promoter"
_SESSION = "tier-promoter"

_DAY_MS = 86_400_000
_TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000}  # §13: minutes-only live cohorts
_BRACKET = ("tp_atr_multiplier", "sl_atr_multiplier", "max_hold_candles")
_EXCLUDED_REASONS = ("orphaned_crash_recovery", "manual")  # forced closes, not the recipe's exit

WINDOW_DAYS = 14
MIN_N = 150
T_LAB = 2.5
T_STAGING = 2.0
T_KEEP_STAGING = 1.0
T_KEEP_LAB = 0.0
MIN_DWELL_DAYS = 3
COOLDOWN_DAYS = 7
MAX_PROMOTIONS_PER_TIER = 3
HIGH_WIN_MAX_TP_SL = 0.5  # hiwin33 0.33 · hiwin43 0.43 · hiwin50 0.5 · scratch 0.25

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
    """One closed trade, scored. skill_bps = (same-side − opposite-side replay) / 2."""

    entry_ts: int
    skill_bps: float
    gross_bps: float
    net_bps: float


@dataclass(frozen=True)
class Evidence:
    n: int
    skill_bps: float
    skill_t: float
    skill_h1: float
    skill_h2: float
    gross_bps: float
    net_bps: float


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

    Scope: a single pattern on a minutes timeframe with an explicit plain bracket.
    Bots on global params (no per-bot params) or with trailing / indicator exits
    (tp ≥ 10 ATR is how the sigexit presets disable the price target) are skipped.
    """
    patterns = bot.get("patterns") or []
    params = bot.get("params") or {}
    tf = bot.get("timeframe_entry")
    if len(patterns) != 1 or tf not in _TF_MS or not all(k in params for k in _BRACKET):
        return None
    if params.get("trailing_enabled") or float(params["tp_atr_multiplier"]) >= 10.0:
        return None
    return json.dumps({"pattern": patterns[0], "tf": tf, "params": params}, sort_keys=True)


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


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def summarize(samples: Sequence[Sample]) -> Evidence:
    ordered = sorted(samples, key=lambda s: s.entry_ts)
    skills = [s.skill_bps for s in ordered]
    n = len(skills)
    mean = _mean(skills)
    t = 0.0
    if n >= 2:
        var = sum((x - mean) ** 2 for x in skills) / (n - 1)
        t = mean / math.sqrt(var / n) if var > 0 else 0.0
    half = n // 2
    return Evidence(
        n=n,
        skill_bps=mean,
        skill_t=t,
        skill_h1=_mean(skills[:half]),
        skill_h2=_mean(skills[half:]),
        gross_bps=_mean([s.gross_bps for s in ordered]),
        net_bps=_mean([s.net_bps for s in ordered]),
    )


def _since(samples: Sequence[Sample], since: Optional[int]) -> list[Sample]:
    return [s for s in samples if since is None or s.entry_ts >= since]


def high_win_design(recipe: str) -> bool:
    """The owner's lab/staging admission geometry: an inverted, high-win bracket."""
    params = json.loads(recipe)["params"]
    return float(params["tp_atr_multiplier"]) / float(params["sl_atr_multiplier"]) <= HIGH_WIN_MAX_TP_SL


def _promotable(recipe: str, ev: Evidence, t_min: float) -> bool:
    return (
        high_win_design(recipe)
        and ev.n >= MIN_N
        and ev.skill_t >= t_min
        and ev.skill_h1 > 0
        and ev.skill_h2 > 0
        and ev.gross_bps > 0
    )


def _fmt(ev: Evidence) -> str:
    return f"n={ev.n} skill={ev.skill_bps:+.2f}bps t={ev.skill_t:+.2f} halves={ev.skill_h1:+.1f}/{ev.skill_h2:+.1f}"


def decide(
    tiers: dict[str, set[str]],
    samples: dict[str, list[Sample]],
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

    # 1. staging: demote first, then consider lab recipes for promotion
    for r in sorted(tiers["staging"] - cooled):
        ev = summarize(_since(samples.get(r, []), entry(r).staging_since))
        if ev.n >= MIN_N and ev.skill_t < T_KEEP_STAGING:
            dst = "lab" if ev.skill_t >= T_KEEP_LAB else "dev"
            moves.append(Move(r, labels[r], "demote", "staging", dst, f"staging t<{T_KEEP_STAGING}: {_fmt(ev)}", ev))

    staged = 0
    for r in sorted(tiers["lab"] - tiers["staging"] - cooled - retired):
        e = entry(r)
        if e.lab_since is not None and now_ms - e.lab_since < MIN_DWELL_DAYS * _DAY_MS:
            continue
        ev = summarize(_since(samples.get(r, []), e.lab_since))
        if staged < MAX_PROMOTIONS_PER_TIER and _promotable(r, ev, T_STAGING):
            moves.append(Move(r, labels[r], "promote", "lab", "staging", f"lab→staging: {_fmt(ev)}", ev))
            staged += 1

    # 2. lab: demote on negative post-entry skill (a staging copy is demoted above)
    demoted = {m.recipe for m in moves if m.kind == "demote"}
    for r in sorted(tiers["lab"] - cooled - demoted):
        ev = summarize(_since(samples.get(r, []), entry(r).lab_since))
        if ev.n >= MIN_N and ev.skill_t < T_KEEP_LAB:
            moves.append(Move(r, labels[r], "demote", "lab", "dev", f"lab t<{T_KEEP_LAB}: {_fmt(ev)}", ev))

    # 3. dev → lab
    promoted = 0
    candidates = []
    for r in tiers["dev"] - tiers["lab"] - tiers["staging"] - cooled - retired:
        ev = summarize(samples.get(r, []))
        if _promotable(r, ev, T_LAB):
            candidates.append((ev.skill_t, r, ev))
    for _, r, ev in sorted(candidates, reverse=True):
        if promoted >= MAX_PROMOTIONS_PER_TIER:
            break
        moves.append(Move(r, labels[r], "promote", "dev", "lab", f"dev→lab: {_fmt(ev)}", ev))
        promoted += 1
    return moves


def tier_label(recipe: str, bots_by_tier: dict[str, list[dict[str, Any]]]) -> str:
    """Label a recipe runs under in lab/staging: an existing curated label when it has one,
    else 'cur_' + its dev label with the hw33 bracket tag dropped (hw33_tsmom_z → cur_tsmom_z)."""
    for tier in ("lab", "staging"):
        found = sorted(b["strategy"] for b in bots_by_tier.get(tier, []) if recipe_key(b) == recipe)
        if found:
            return found[0]
    dev = sorted(b["strategy"] for b in bots_by_tier.get("dev", []) if recipe_key(b) == recipe)
    base = dev[0] if dev else json.loads(recipe)["pattern"]
    core = "_".join(tok for tok in base.split("_") if tok != "hw33")
    return core if core.startswith("cur_") else f"cur_{core}"


def target_pairs(available: Sequence[str], preferred: Sequence[str], k: int) -> list[str]:
    """Up to k symbols: preferred order first, then the recipe's other pairs alphabetically.
    Restricted to pairs the recipe already runs on (xs_rev only fires inside its universe)."""
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
    tf = template["timeframe_entry"]
    return {
        "bot_id": f"{env}-{sym}USDT-{tf}-{label}-01",
        "pair": f"{sym}/USDT",
        "timeframe_entry": tf,
        "timeframe_regime": template.get("timeframe_regime", tf),
        "max_active_buckets": 1,
        "strategy": label,
        "patterns": list(template["patterns"]),
        "params": dict(template["params"]),
    }


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
        if tier == "lab":
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


# ---------------------------------------------------------------------------
# File I/O (host and container)
# ---------------------------------------------------------------------------


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def load_tiers() -> dict[str, list[dict[str, Any]]]:
    out = {}
    for tier, name in _TIER_FILES.items():
        raw = _load_json(os.path.join(_ROOT, name), [])
        out[tier] = raw if isinstance(raw, list) else list(raw.get("bots", []))
    return out


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


def retired_recipes(bots_by_tier: dict[str, list[dict[str, Any]]]) -> set[str]:
    ledger = _load_json(os.path.join(_ROOT, "retired_strategies.json"), {})
    out = set()
    for bots in bots_by_tier.values():
        for b in bots:
            r = recipe_key(b)
            if r is not None and any(retired_ledger._matches(b, e) for e in ledger.get("retired", [])):
                out.add(r)
    return out


# ---------------------------------------------------------------------------
# DB I/O (inside the image)
# ---------------------------------------------------------------------------


async def load_samples(
    bot_recipe: dict[str, str], recipe_params: dict[str, dict[str, Any]], since_ms: int
) -> dict[str, list[Sample]]:
    """Closed trades in the window, each scored by the paired-skill replay on its pair's candles."""
    from src.db import connection as db_conn

    tfs = sorted(_TF_MS)
    async with db_conn.acquire() as conn:
        trades = await conn.fetch(
            """
            SELECT bot_id, pair, timeframe, direction, entry_ts, notional_usdt, pnl_gross_usdt, pnl_net_usdt
            FROM trades
            WHERE exit_ts IS NOT NULL AND entry_ts >= $1 AND timeframe = ANY($2::text[])
              AND close_reason <> ALL($3::text[]) AND notional_usdt > 0
            """,
            since_ms,
            tfs,
            list(_EXCLUDED_REASONS),
        )
        # one candle series per (pair, tf): the bot_id that recorded the most of the window
        sources = await conn.fetch(
            """
            SELECT DISTINCT ON (pair, timeframe) pair, timeframe, bot_id
            FROM (SELECT pair, timeframe, bot_id, count(*) AS c FROM candles
                  WHERE ts >= $1 AND timeframe = ANY($2::text[]) GROUP BY 1, 2, 3) s
            ORDER BY pair, timeframe, c DESC
            """,
            since_ms,
            tfs,
        )
        series: dict[tuple[str, str], tuple[list[int], list[float], list[float]]] = {}
        for src in sources:
            rows = await conn.fetch(
                "SELECT ts, close, atr14 FROM candles WHERE bot_id = $1 AND pair = $2 AND timeframe = $3 "
                "AND ts >= $4 ORDER BY ts",
                src["bot_id"],
                src["pair"],
                src["timeframe"],
                since_ms - _DAY_MS,
            )
            series[(src["pair"], src["timeframe"])] = (
                [int(r["ts"]) for r in rows],
                [float(r["close"]) for r in rows],
                [float(r["atr14"]) if r["atr14"] is not None else 0.0 for r in rows],
            )
    return score_trades([dict(t) for t in trades], series, bot_recipe, recipe_params)


def score_trades(
    trades: Sequence[dict[str, Any]],
    series: dict[tuple[str, str], tuple[list[int], list[float], list[float]]],
    bot_recipe: dict[str, str],
    recipe_params: dict[str, dict[str, Any]],
) -> dict[str, list[Sample]]:
    """Pure: group trades by recipe and score each (trades on unknown bots/candles are skipped)."""
    out: dict[str, list[Sample]] = {}
    for t in trades:
        recipe = bot_recipe.get(t["bot_id"])
        s = series.get((t["pair"], t["timeframe"]))
        if recipe is None or s is None:
            continue
        ts, closes, atrs = s
        idx = entry_index(ts, _TF_MS[t["timeframe"]], int(t["entry_ts"]))
        if idx is None or atrs[idx] <= 0:
            continue
        sign = 1 if t["direction"] == "long" else -1
        skill = paired_skill(closes, atrs[idx], idx, sign, recipe_params[recipe])
        if skill is None:
            continue
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


async def evaluate(dry_run: bool) -> Plan:
    """Container shell: evidence → decisions → rewritten tier files + ledger + events."""
    from dotenv import load_dotenv

    from src.config import AppConfig
    from src.db import connection as db_conn
    from src.db import writer as db

    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    now_ms = int(time.time() * 1000)
    bots_by_tier = load_tiers()
    tiers: dict[str, set[str]] = {}
    bot_recipe: dict[str, str] = {}
    recipe_params: dict[str, dict[str, Any]] = {}
    for tier, bots in bots_by_tier.items():
        tiers[tier] = set()
        for b in bots:
            r = recipe_key(b)
            if r is None:
                continue
            tiers[tier].add(r)
            bot_recipe[b["bot_id"]] = r
            recipe_params[r] = b["params"]
    labels = {r: tier_label(r, bots_by_tier) for r in recipe_params}
    entries, history = load_ledger()
    entries = seed_ledger(entries, tiers, labels)

    await db_conn.init_pool(cfg)
    try:
        samples = await load_samples(bot_recipe, recipe_params, now_ms - WINDOW_DAYS * _DAY_MS)
        moves = decide(tiers, samples, entries, labels, retired_recipes(bots_by_tier), now_ms)
        new_tiers = apply_moves(bots_by_tier, moves, set(curated._LAB_KEEP_IDS))
        plan = Plan(moves=moves)
        for tier in ("lab", "staging"):
            old_ids = {b["bot_id"] for b in bots_by_tier[tier]}
            new_ids = {b["bot_id"] for b in new_tiers[tier]}
            if old_ids != new_ids:
                plan.changed_tiers.append(tier)
                plan.new_bots[tier] = [b for b in new_tiers[tier] if b["bot_id"] not in old_ids]

        review = {
            label: asdict(summarize(samples.get(r, [])))
            for r, label in sorted(labels.items(), key=lambda kv: kv[1])
            if r in samples
        }
        if not dry_run:
            for tier in plan.changed_tiers:
                _save_json(os.path.join(_ROOT, _TIER_FILES[tier]), new_tiers[tier])
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
                {"moves": len(moves), "changed_tiers": plan.changed_tiers, "recipes_scored": len(review)},
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
                "review": review,
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
    ap.add_argument("cmd", choices=["run", "evaluate"])
    ap.add_argument("--dry-run", action="store_true", help="decide and report, write nothing")
    args = ap.parse_args()
    if args.cmd == "evaluate":
        asyncio.run(evaluate(args.dry_run))
        return 0
    return run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
