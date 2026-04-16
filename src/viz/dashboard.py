"""
Layer 3 boundary — terminal dashboard.

Renders a rich live display (CLAUDE.md §28).
Updates on candle close; all data sourced from the events table + live state.
Uses rich.Live for in-place terminal rendering.

Layout:
    ┌ Header: bot_id · session · uptime · regime ─────────────────┐
    │ Market: pair · price · EMA9/21 · RSI · ATR · Vol            │
    │ Bucket: status · session PnL · trade count · win rate       │
    │ Events: rolling 20 from events table                        │
    └─────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.config import AppConfig


def _utc_now_str() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _uptime_str(start_ts: int) -> str:
    elapsed = int(time.time()) - start_ts // 1000
    h, rem = divmod(elapsed, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class Dashboard:
    """Rich terminal dashboard. Updated by calling refresh()."""

    def __init__(self, cfg: AppConfig, start_ts: int) -> None:
        self.cfg = cfg
        self.start_ts = start_ts
        self._console = Console()
        self._live: Optional[Live] = None
        self._state: dict[str, Any] = {
            "price": None,
            "ema9": None,
            "ema21": None,
            "rsi14": None,
            "atr14": None,
            "vol_ratio": None,
            "regime": "—",
            "position": None,
            "session_pnl": 0.0,
            "trade_count": 0,
            "win_count": 0,
            "events": [],
        }

    def start(self) -> None:
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=1,
            screen=False,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()

    def update(self, state: dict[str, Any]) -> None:
        """Push new state and re-render."""
        self._state.update(state)
        if self._live:
            self._live.update(self._render())

    # -----------------------------------------------------------------------
    # Rendering
    # -----------------------------------------------------------------------

    def _render(self) -> Panel:
        s = self._state
        cfg = self.cfg

        # Header line
        header = Text()
        header.append("KESTREL", style="bold cyan")
        header.append(f"  │  {cfg.bot_id}", style="white")
        header.append(f"  │  {_utc_now_str()}", style="dim")
        header.append(f"\nSession: {cfg.bot_id}", style="dim")
        header.append(f"  │  Uptime: {_uptime_str(self.start_ts)}", style="dim")
        header.append(f"  │  Regime: {s['regime']}", style="yellow bold")

        # Market panel
        price_str = f"{s['price']:,.2f}" if s["price"] else "—"
        ema9_str = f"{s['ema9']:,.0f}" if s["ema9"] else "—"
        ema21_str = f"{s['ema21']:,.0f}" if s["ema21"] else "—"
        rsi_str = f"{s['rsi14']:.1f}" if s["rsi14"] else "—"
        atr_str = f"{s['atr14']:.2f}" if s["atr14"] else "—"
        vol_str = f"{s['vol_ratio']:.2f}x" if s["vol_ratio"] else "—"

        market = (
            f"[bold]{cfg.pair}  {cfg.timeframe_entry}[/bold]\n"
            f"Price: [green]{price_str}[/green]  │  EMA9/21: {ema9_str}/{ema21_str}\n"
            f"RSI14: {rsi_str}  │  ATR14: {atr_str}  │  Vol: {vol_str}"
        )

        # Bucket / session panel
        pos = s["position"]
        if pos:
            bucket_str = (
                f"[yellow]OPEN[/yellow] {pos['direction'].upper()} "
                f"@ {pos['entry_price']:,.2f}  TP: {pos['tp_price']:,.2f}  SL: {pos['sl_price']:,.2f}"
            )
        else:
            bucket_str = "[dim]No open position[/dim]"

        win_pct = (s["win_count"] / s["trade_count"] * 100) if s["trade_count"] else 0
        pnl_color = "green" if s["session_pnl"] >= 0 else "red"
        session_str = (
            f"PnL: [{pnl_color}]${s['session_pnl']:+.4f}[/{pnl_color}]  │  "
            f"Trades: {s['win_count']}W {s['trade_count']-s['win_count']}L  │  "
            f"Win: {win_pct:.0f}%"
        )

        # Events table
        events_table = Table(show_header=False, box=None, padding=(0, 1))
        events_table.add_column("ts", style="dim", width=8)
        events_table.add_column("cat", width=5)
        events_table.add_column("msg")

        for ev in s["events"][-20:]:
            ts_str = datetime.fromtimestamp(ev["ts"] / 1000, tz=timezone.utc).strftime("%H:%M:%S")
            cat_abbr = {
                "signal": "SIG", "order": "ORD", "position": "POS",
                "risk": "RSK", "connection": "CON", "system": "SYS",
            }.get(ev.get("category", ""), "---")
            level_color = {"INFO": "white", "WARN": "yellow", "ERROR": "red", "CRITICAL": "red bold"}.get(
                ev.get("level", "INFO"), "white"
            )
            events_table.add_row(
                ts_str,
                f"[{level_color}][{cat_abbr}][/{level_color}]",
                ev.get("message", ""),
            )

        body = (
            f"{market}\n"
            f"{'─'*60}\n"
            f"BUCKET 1  │  $10.00  │  {bucket_str}\n"
            f"SESSION   │  {session_str}\n"
            f"{'─'*60}\n"
            f"RECENT EVENTS (last 20 from events table)"
        )

        content = Text.from_markup(body)

        return Panel(
            content,
            title=header,
            border_style="blue",
            subtitle=events_table,
        )
