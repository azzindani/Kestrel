"""
Layer 3 boundary — portfolio circuit-breaker ("manager bot").

One in-process coordinator coroutine (sibling of daily_summary_task) that watches the
AGGREGATE unrealised PnL across every bot in the process and, when it crosses a
threshold (as a fraction of total account equity), directs every bot to force-close
its open positions:

    aggregate unrealised >= +portfolio_tp_pct × equity  → lock the gain   (profit-lock)
    aggregate unrealised <= -portfolio_dd_pct × equity  → cap the drawdown (drawdown-stop)

Design (CLAUDE.md §11): the guard only DECIDES and DIRECTS — each bot still owns and
closes its OWN positions (force_close_all), so one-owner-per-resource holds and there is
no cross-bot reach into another daemon's state. The guard reads each bot's snapshot
(equity, unrealised) via a cheap in-memory call; it never touches their positions.

HONESTY: this is RISK-SHAPING, not edge. On a no-edge book it locks/caps aggregate
swings and lowers variance, but adds no expected value, and every forced close is a
taker market-out (a real cost). Disabled unless a threshold > 0 (live-safe default).
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from src.config import AppConfig
from src.db import writer as db
from src.notify.telegram import TelegramNotifier


class _GuardedBot(Protocol):
    """Structural type for the bits of Daemon the guard uses (avoids a circular import)."""

    def portfolio_snapshot(self) -> tuple[float, float]: ...  # (equity_usdt, unrealised_usdt)

    async def force_close_all(self, reason: str) -> None: ...


class PortfolioGuard:
    """Aggregates unrealised PnL across bots and triggers a synchronized close-all."""

    def __init__(
        self,
        cfg: AppConfig,
        session_id: str,
        notifier: TelegramNotifier,
        tp_pct: float,
        dd_pct: float,
        check_interval_s: float = 20.0,
        cooldown_s: float = 60.0,
    ) -> None:
        self.cfg = cfg
        self.session_id = session_id
        self.notifier = notifier
        self.tp_pct = tp_pct
        self.dd_pct = dd_pct
        self.check_interval_s = check_interval_s
        self.cooldown_s = cooldown_s
        self._bots: list[_GuardedBot] = []

    @property
    def enabled(self) -> bool:
        return self.tp_pct > 0.0 or self.dd_pct > 0.0

    def attach(self, bots: list[_GuardedBot]) -> None:
        """Register the bots the guard watches (called once after they're built)."""
        self._bots = bots

    def _aggregate(self) -> tuple[float, float]:
        """Sum (equity, unrealised) across all attached bots. Pure read."""
        equity = 0.0
        unrealised = 0.0
        for bot in self._bots:
            e, u = bot.portfolio_snapshot()
            equity += e
            unrealised += u
        return equity, unrealised

    def _decide(self, equity: float, unrealised: float) -> str | None:
        """Return the close-all reason if a threshold is crossed, else None."""
        if equity <= 0.0:
            return None
        ratio = unrealised / equity
        if self.tp_pct > 0.0 and ratio >= self.tp_pct:
            return "portfolio_take_profit"
        if self.dd_pct > 0.0 and ratio <= -self.dd_pct:
            return "portfolio_drawdown_stop"
        return None

    async def _fire(self, reason: str, equity: float, unrealised: float) -> None:
        """Direct every bot to close its open positions and log one aggregate event."""
        await asyncio.gather(
            *[bot.force_close_all(reason) for bot in self._bots],
            return_exceptions=True,
        )
        level = "INFO" if reason == "portfolio_take_profit" else "WARN"
        await db.write_event(
            self.cfg.bot_id,
            self.session_id,
            self.cfg.env.value,
            level,
            "risk",
            f"portfolio_guard:{reason}",
            {
                "reason": reason,
                "equity_usdt": round(equity, 4),
                "unrealised_usdt": round(unrealised, 4),
                "unrealised_pct": round(unrealised / equity * 100.0, 3) if equity else None,
                "tp_pct": self.tp_pct,
                "dd_pct": self.dd_pct,
                "bots": len(self._bots),
            },
        )
        try:
            await self.notifier.send(
                f"PORTFOLIO GUARD {reason}: closed all positions at "
                f"{unrealised / equity * 100.0:+.2f}% unrealised (${unrealised:+.2f} / ${equity:.2f})",
                level,
            )
        except Exception:  # noqa: BLE001 — a failed alert must never crash the guard
            pass

    async def run(self) -> None:
        """Poll the aggregate every check_interval; fire + cool down on a crossing.

        Disabled (no threshold set) ⇒ returns immediately so it costs nothing.
        """
        if not self.enabled:
            return
        while True:
            await asyncio.sleep(self.check_interval_s)
            if not self._bots:
                continue
            equity, unrealised = self._aggregate()
            reason = self._decide(equity, unrealised)
            if reason is not None:
                await self._fire(reason, equity, unrealised)
                # Cool off so closing positions / stale prices don't immediately re-fire.
                await asyncio.sleep(self.cooldown_s)
