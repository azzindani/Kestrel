"""
Layer 3 boundary — Telegram notification adapter.

Sends structured alerts to the configured Telegram chat.
Events that trigger alerts are defined in CLAUDE.md §27.

All messages use pre-formatted text (MarkdownV2 escaping).
Uses httpx async client; never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

import httpx

from src.config import AppConfig

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 10  # seconds per request
_MAX_RETRIES = 3


def _escape(text: str) -> str:
    """Escape special chars for Telegram MarkdownV2."""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", str(text))


class TelegramNotifier:
    """Async Telegram notification client."""

    def __init__(self, cfg: AppConfig) -> None:
        self._token = cfg.telegram_token
        self._chat_id = cfg.telegram_chat_id
        self._url = _TELEGRAM_API.format(token=self._token)
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -----------------------------------------------------------------------
    # Core send
    # -----------------------------------------------------------------------

    async def send(self, text: str, level: str = "INFO") -> None:
        """Send a plain text message. Silently swallows errors after retries
        so Telegram issues never crash the daemon."""
        if not self._client:
            return

        prefix = {
            "INFO": "ℹ",
            "WARN": "⚠️",
            "ERROR": "🔴",
            "CRITICAL": "🚨",
        }.get(level, "")

        payload = {
            "chat_id": self._chat_id,
            "text": f"{prefix} {text}",
            "parse_mode": "HTML",
        }
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._client.post(self._url, json=payload)
                resp.raise_for_status()
                return
            except Exception:
                if attempt == _MAX_RETRIES - 1:
                    return  # silently fail — Telegram must never crash daemon
                await asyncio.sleep(2**attempt)

    # -----------------------------------------------------------------------
    # Structured alert helpers (CLAUDE.md §27)
    # -----------------------------------------------------------------------

    async def signal_fired(self, signal_data: dict[str, Any]) -> None:
        side = "🟩 LONG" if str(signal_data["direction"]).lower() == "long" else "🟥 SHORT"
        msg = (
            f"🎯 <b>SIGNAL FIRED</b>\n"
            f"{signal_data['pair']} · {side} · conf {signal_data['confidence']:.2f}\n"
            f"{signal_data['pattern']} · {signal_data['regime']}\n"
            f"Entry {signal_data['entry_price']} | TP {signal_data['tp_price']} | SL {signal_data['sl_price']}"
        )
        await self.send(msg, "INFO")

    @staticmethod
    def _format_close(t: dict[str, Any], win: bool) -> str:
        """Build a colour-coded trade-close message (🟢 profit · 🔴 loss)."""
        dot = "🟢" if win else "🔴"
        arrow = "📈" if win else "📉"
        head = "PROFIT" if win else "LOSS"
        pnl = t["pnl_net_usdt"]
        pnl_str = f"+${pnl:.4f}" if pnl >= 0 else f"-${abs(pnl):.4f}"

        lev = t.get("leverage")
        lev_str = f" {lev}x" if lev else ""
        entry, pts = t.get("entry_price"), t.get("points")
        px_line = f"Entry {entry} → Exit {t['exit_price']}" if entry is not None else f"Exit {t['exit_price']}"
        pts_str = f"  ({pts:+.6g} pts)" if pts is not None else ""
        dirn = str(t.get("direction", "—")).upper()
        bal = t.get("bucket_balance_after", "?")
        hold = t.get("hold_candles", "?")

        return (
            f"{dot} <b>{head}</b> {dot}\n"
            f"{t['pair']} · {dirn}{lev_str}\n"
            f"{px_line}{pts_str}\n"
            f"{arrow} <b>{pnl_str}</b>  ({t['pnl_pct']:+.2f}%)\n"
            f"{t['close_reason']} · {hold}c · bal ${bal}"
        )

    async def trade_closed_profit(self, trade_data: dict[str, Any]) -> None:
        await self.send(self._format_close(trade_data, win=True), "INFO")

    async def trade_closed_loss(self, trade_data: dict[str, Any]) -> None:
        await self.send(self._format_close(trade_data, win=False), "WARN")

    async def liquidation(self, trade_data: dict[str, Any]) -> None:
        msg = (
            f"<b>🚨 LIQUIDATION</b>\n"
            f"Pair: {trade_data['pair']} | Loss: ${trade_data['pnl_net_usdt']:.4f}\n"
            f"Bucket balance remaining: ${trade_data.get('bucket_balance_after', '?')}\n"
            f"Bot: {trade_data.get('bot_id', '-')}"
        )
        await self.send(msg, "CRITICAL")

    async def ws_reconnect(self, exchange: str, attempt: int) -> None:
        msg = f"<b>WS RECONNECT</b> — {exchange} attempt {attempt}/{5}"
        await self.send(msg, "WARN")

    async def regime_change(self, regime: str, pairs: list[str]) -> None:
        msg = f"<b>REGIME CHANGE</b> → {regime}\nPairs: {', '.join(pairs)}"
        await self.send(msg, "INFO")

    async def daily_summary(self, summary: dict[str, Any]) -> None:
        total = summary["total_trades"]
        wins = summary.get("wins", 0)
        losses = summary.get("losses", max(total - wins, 0))
        net = summary["net_pnl_usdt"]
        dot = "🟢" if net >= 0 else "🔴"
        net_str = f"+${net:.2f}" if net >= 0 else f"-${abs(net):.2f}"
        msg = (
            f"📊 <b>DAILY SUMMARY</b> (fleet)\n"
            f"Trades: {total} ({wins}W / {losses}L) · Win {summary['win_rate'] * 100:.1f}%\n"
            f"{dot} Net PnL: {net_str}\n"
            f"{summary.get('bucket_states', '-')}"
        )
        await self.send(msg, "INFO")

    async def system_error(self, error: str, bot_id: str, ts: int) -> None:
        msg = f"<b>🚨 SYSTEM ERROR</b>\n{error}\nBot: {bot_id} | ts: {ts}"
        await self.send(msg, "CRITICAL")
