"""
Layer 1 — pattern memory logic.

This module provides pure functions that operate on pattern memory data
already loaded from the DB. DB reads/writes are handled by db/writer.py
and called by the engine layer, which passes the data in here.

The only allowed adjustment is a gentle confidence penalty/boost based on
historical win rate for this pattern × direction × session × regime combination.
"""
from __future__ import annotations

from typing import Optional


def adjust_confidence(
    raw_confidence: float,
    memory: Optional[dict],
    min_samples: int = 10,
) -> float:
    """
    Adjust a raw confidence score based on historical pattern memory.

    If sample_count < min_samples, return raw_confidence unchanged.
    Otherwise blend the raw confidence toward the historical win rate.

    The blend factor is capped so a strong signal is never fully suppressed
    and a weak signal is never fully promoted.

    Args:
        raw_confidence: confidence from the pattern detector (0–1)
        memory:         dict from pattern_memory table, or None
        min_samples:    minimum sample count before adjustments apply

    Returns:
        Adjusted confidence, clamped to [0.30, 0.95].
    """
    if memory is None:
        return raw_confidence

    sample_count = memory.get("sample_count", 0)
    if sample_count < min_samples:
        return raw_confidence

    win_rate = memory.get("win_rate")
    if win_rate is None:
        return raw_confidence

    # Blend: 70% raw signal + 30% historical performance
    adjusted = raw_confidence * 0.70 + float(win_rate) * 0.30
    return round(max(0.30, min(0.95, adjusted)), 3)


def should_suppress(
    pattern: str,
    direction: str,
    session: str,
    regime: str,
    memory: Optional[dict],
    min_samples: int = 20,
    min_win_rate: float = 0.35,
) -> bool:
    """
    Return True if the pattern should be suppressed based on persistent poor performance.

    A pattern is suppressed only when:
        sample_count >= min_samples AND win_rate < min_win_rate

    This avoids suppressing newly observed patterns before statistical validity.
    """
    if memory is None:
        return False

    sample_count = memory.get("sample_count", 0)
    if sample_count < min_samples:
        return False

    win_rate = memory.get("win_rate")
    if win_rate is None:
        return False

    return float(win_rate) < min_win_rate


def updated_memory(
    existing: Optional[dict],
    won: bool,
    pnl_pct: float,
    ts_ms: int,
    pattern: str,
    direction: str,
    session: str,
    regime: str,
) -> dict:
    """
    Compute the updated memory record after a trade closes.

    Returns a dict ready to be passed to db/writer.upsert_pattern_memory().
    This is a pure function — no DB calls here.
    """
    sample_count = (existing or {}).get("sample_count", 0) + 1
    win_count = (existing or {}).get("win_count", 0) + (1 if won else 0)

    prev_avg = (existing or {}).get("avg_pnl_pct") or 0.0
    # Incremental running average
    avg_pnl_pct = (prev_avg * (sample_count - 1) + pnl_pct) / sample_count

    return {
        "pattern": pattern,
        "direction": direction,
        "session": session,
        "regime": regime,
        "sample_count": sample_count,
        "win_count": win_count,
        "win_rate": round(win_count / sample_count, 4),
        "avg_pnl_pct": round(avg_pnl_pct, 4),
        "last_updated": ts_ms,
    }
