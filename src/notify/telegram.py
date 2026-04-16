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
                await asyncio.sleep(2 ** attempt)

    # -----------------------------------------------------------------------
    # Structured alert helpers (CLAUDE.md §27)
    # -----------------------------------------------------------------------

    async def signal_fired(self, signal_data: dict[str, Any]) -> None:
        msg = (
            f"<b>SIGNAL FIRED</b>\n"
            f"Pattern: {signal_data['pattern']} | Dir: {signal_data['direction'].upper()}\n"
            f"Pair: {signal_data['pair']} | Conf: {signal_data['confidence']:.2f}\n"
            f"Entry: {signal_data['entry_price']} | TP: {signal_data['tp_price']} | SL: {signal_data['sl_price']}\n"
            f"Regime: {signal_data['regime']} | Session: {signal_data.get('session', '-')}"
        )
        await self.send(msg, "INFO")

    async def trade_closed_profit(self, trade_data: dict[str, Any]) -> None:
        msg = (
            f"<b>TRADE CLOSED — PROFIT</b>\n"
            f"Pair: {trade_data['pair']} | {trade_data['direction'].upper()}\n"
            f"Exit: {trade_data['exit_price']} | PnL: +${trade_data['pnl_net_usdt']:.4f} "
            f"({trade_data['pnl_pct']:+.2f}%)\n"
            f"Reason: {trade_data['close_reason']}"
        )
        await self.send(msg, "INFO")

    async def trade_closed_loss(self, trade_data: dict[str, Any]) -> None:
        msg = (
            f"<b>TRADE CLOSED — LOSS</b>\n"
            f"Pair: {trade_data['pair']} | {trade_data['direction'].upper()}\n"
            f"Exit: {trade_data['exit_price']} | PnL: ${trade_data['pnl_net_usdt']:.4f} "
            f"({trade_data['pnl_pct']:+.2f}%)\n"
            f"Reason: {trade_data['close_reason']} | Bucket balance: ${trade_data.get('bucket_balance_after', '?')}"
        )
        await self.send(msg, "WARN")

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
        msg = (
            f"<b>REGIME CHANGE</b> → {regime}\n"
            f"Pairs: {', '.join(pairs)}"
        )
        await self.send(msg, "INFO")

    async def daily_summary(self, summary: dict[str, Any]) -> None:
        msg = (
            f"<b>DAILY SUMMARY</b>\n"
            f"Trades: {summary['total_trades']} | Win rate: {summary['win_rate']*100:.1f}%\n"
            f"Net PnL: ${summary['net_pnl_usdt']:.4f}\n"
            f"Bucket states: {summary.get('bucket_states', '-')}"
        )
        await self.send(msg, "INFO")

    async def system_error(self, error: str, bot_id: str, ts: int) -> None:
        msg = (
            f"<b>🚨 SYSTEM ERROR</b>\n"
            f"{error}\n"
            f"Bot: {bot_id} | ts: {ts}"
        )
        await self.send(msg, "CRITICAL")
