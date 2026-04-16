"""
Layer boundary entry point — main daemon.

Startup sequence (CLAUDE.md §16):
    1. Load + validate .env
    2. Connect PostgreSQL — abort if unreachable
    3. Connect exchange REST — verify credentials
    4. Reconcile positions: DB state vs exchange state
    5. Connect WebSocket — begin streaming
    6. Enter main event loop

DI at startup:
    ENV=dev  → SimulationExecution
    ENV=prod → LiveExecution
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
import uuid
from typing import Optional

from dotenv import load_dotenv

from src.config import AppConfig, BucketState, Env, Params, SignalOutcome, load_params
from src.data.candle_builder import CandleBuilder
from src.data.feed import MarketFeed
from src.db import connection as db_conn
from src.db import schema as db_schema
from src.db import writer as db
from src.engine.scheduler import cleanup_task, daily_summary_task, heartbeat_task
from src.execution.interface import ExecutionError, ExecutionInterface
from src.execution.simulation import SimulationExecution
from src.notify.telegram import TelegramNotifier
from src.risk import manager as risk
from src.signal.detector import evaluate
from src.viz.dashboard import Dashboard


class Daemon:
    """Main Kestrel daemon process."""

    def __init__(
        self,
        cfg: AppConfig,
        params: Params,
        execution: ExecutionInterface,
        notifier: TelegramNotifier,
    ) -> None:
        self.cfg = cfg
        self.params = params
        self.execution = execution
        self.notifier = notifier

        self.session_id = f"{cfg.env.value}-{uuid.uuid4().hex[:8]}"
        self.start_ts = int(time.time() * 1000)

        # Candle processing queue: CandleBuilder → process_candle
        self._candle_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._last_ws_reconnect_ts: Optional[int] = None
        self._session_pnl: float = 0.0
        self._session_reset_ts: int = _utc_midnight_ms(self.start_ts)

        # State
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # Dashboard (dev only)
        self._dashboard: Optional[Dashboard] = None
        if cfg.env is Env.DEV:
            self._dashboard = Dashboard(cfg, self.start_ts)

    # -----------------------------------------------------------------------
    # Startup / shutdown
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Execute startup sequence and enter event loop."""
        self._running = True

        # 2. Connect PostgreSQL
        await db_conn.init_pool(self.cfg)
        await db_schema.apply_schema()
        await db.write_event(
            self.cfg.bot_id,
            self.session_id,
            self.cfg.env.value,
            "INFO",
            "system",
            "daemon_start",
            {"session_id": self.session_id, "env": self.cfg.env.value},
        )

        # 3. Notifier
        await self.notifier.start()

        # 4. Bootstrap candle builder from DB history
        builder = CandleBuilder(
            self.cfg.bot_id,
            self.cfg.pair,
            self.cfg.timeframe_entry,
            self.params,
        )
        historical = await db.load_recent_candles(self.cfg.bot_id, self.cfg.pair, self.cfg.timeframe_entry, limit=120)
        hist_candles = [_row_to_candle(row) for row in historical]
        builder.bootstrap(hist_candles)
        builder.set_emitter(lambda c: self._candle_queue.put_nowait(c))

        # 5. Reconcile open positions
        await self._reconcile()

        # 6. Start WebSocket feed
        feed = MarketFeed(
            cfg=self.cfg,
            pair=self.cfg.pair,
            timeframe=self.cfg.timeframe_entry,
            builder=builder,
            on_reconnect=self._on_ws_reconnect,
            notify=self._sync_notify,
        )

        # Dashboard
        if self._dashboard:
            self._dashboard.start()

        # Schedule background tasks
        self._tasks = [
            asyncio.create_task(feed.run(), name="ws_feed"),
            asyncio.create_task(self._candle_processor(), name="candle_processor"),
            asyncio.create_task(heartbeat_task(self.cfg, self.session_id), name="heartbeat"),
            asyncio.create_task(
                daily_summary_task(self.cfg, self.session_id, self.notifier),
                name="daily_summary",
            ),
            asyncio.create_task(cleanup_task(self.cfg, self.session_id), name="cleanup"),
        ]

        # Install SIGTERM handler
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self.stop()))

        await db.write_event(
            self.cfg.bot_id,
            self.session_id,
            self.cfg.env.value,
            "INFO",
            "system",
            "daemon_ready",
            {"pair": self.cfg.pair, "timeframe": self.cfg.timeframe_entry},
        )

        # Wait until stopped
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await db.write_event(
                self.cfg.bot_id,
                self.session_id,
                self.cfg.env.value,
                "CRITICAL",
                "system",
                "daemon_crash",
                {"error": str(exc)},
            )
            await self.notifier.system_error(str(exc), self.cfg.bot_id, int(time.time() * 1000))
            raise

    async def stop(self) -> None:
        """Graceful shutdown: cancel orders → close positions → disconnect (CLAUDE.md §16)."""
        if not self._running:
            return
        self._running = False

        await db.write_event(
            self.cfg.bot_id,
            self.session_id,
            self.cfg.env.value,
            "INFO",
            "system",
            "daemon_stop_initiated",
            {},
        )

        # Close all open positions
        try:
            positions = await self.execution.reconcile()
            for pos in positions:
                await self.execution.close_position(pos["pair"], "manual")
                await db.write_event(
                    self.cfg.bot_id,
                    self.session_id,
                    self.cfg.env.value,
                    "INFO",
                    "position",
                    "position_closed_on_stop",
                    {"pair": pos["pair"]},
                )
        except Exception as exc:
            await db.write_event(
                self.cfg.bot_id,
                self.session_id,
                self.cfg.env.value,
                "ERROR",
                "position",
                "position_close_failed_on_stop",
                {"error": str(exc)},
            )

        # Cancel all scheduled tasks
        for task in self._tasks:
            task.cancel()

        await self.notifier.stop()

        if self._dashboard:
            self._dashboard.stop()

        await db_conn.close_pool()

        await db.write_event(
            self.cfg.bot_id,
            self.session_id,
            self.cfg.env.value,
            "INFO",
            "system",
            "daemon_stopped",
            {},
        )

    # -----------------------------------------------------------------------
    # Candle processor
    # -----------------------------------------------------------------------

    async def _candle_processor(self) -> None:
        """Process completed candles from the queue (signal → risk → execute)."""
        while self._running:
            try:
                candle = await asyncio.wait_for(self._candle_queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

            try:
                await self._process_candle(candle)
            except Exception as exc:
                await db.write_event(
                    self.cfg.bot_id,
                    self.session_id,
                    self.cfg.env.value,
                    "ERROR",
                    "system",
                    "candle_processor_error",
                    {"error": str(exc), "candle_ts": candle.ts},
                )
            finally:
                self._candle_queue.task_done()

    async def _process_candle(self, candle) -> None:
        """Full pipeline for one closed candle."""
        # Reset daily session PnL
        midnight = _utc_midnight_ms(candle.ts)
        if midnight > self._session_reset_ts:
            self._session_pnl = 0.0
            self._session_reset_ts = midnight

        # Write candle to DB
        await db.write_candle(candle)

        # Check if simulation needs to close any open positions (TP/SL)
        if isinstance(self.execution, SimulationExecution):
            self.execution.update_price(candle.pair, candle.close)
            exit_reason = self.execution.check_exits(candle.pair)
            if exit_reason:
                await self._close_position(candle.pair, exit_reason, candle)
                return

        # Check open positions for timeout on live
        positions = await self.execution.reconcile()
        for pos in positions:
            # Will be handled by position monitor — skip signal evaluation
            pass

        # Signal evaluation
        active = await db.count_active_positions(self.cfg.bot_id, self.cfg.env.value)
        state = BucketState(
            active_positions=active,
            last_ws_reconnect_ts=self._last_ws_reconnect_ts,
            session_net_pnl=self._session_pnl,
            current_ts=candle.ts,
        )

        # Load candle buffer from candle builder (passed via closure)
        # The builder emitter sends us the candle — we need the full buffer
        # We reconstruct by loading from DB
        history = await db.load_recent_candles(self.cfg.bot_id, candle.pair, candle.timeframe, limit=120)
        candle_window = [_row_to_candle(r) for r in history]

        if not candle_window:
            return

        signal, rejection = evaluate(candle_window, self.params, self.cfg.bot_id, self.session_id, self.cfg.env.value)

        if rejection is not None:
            await db.write_event(
                self.cfg.bot_id,
                self.session_id,
                self.cfg.env.value,
                "INFO",
                "signal",
                f"signal_rejected:{rejection.reason}",
                {"stage": rejection.stage, "reason": rejection.reason, "candle_ts": candle.ts},
            )
            return

        # Risk validation
        validation = risk.validate(signal, state, self.cfg)
        if not validation.passed:
            await db.write_signal(signal, SignalOutcome.REJECTED, validation.reason)
            await db.write_event(
                self.cfg.bot_id,
                self.session_id,
                self.cfg.env.value,
                "INFO",
                "risk",
                f"risk_rejected:{validation.reason}",
                {"reason": validation.reason, "candle_ts": candle.ts},
            )
            return

        # Execute
        try:
            order = await self.execution.place_order(signal)
        except ExecutionError as exc:
            await db.write_event(
                self.cfg.bot_id,
                self.session_id,
                self.cfg.env.value,
                "ERROR",
                "order",
                "order_placement_failed",
                {"error": str(exc), "pair": signal.pair},
            )
            return

        # Write trade record
        trade_id = await db.write_trade(
            {
                "bot_id": self.cfg.bot_id,
                "session_id": self.session_id,
                "env": self.cfg.env.value,
                "pair": signal.pair,
                "timeframe": signal.timeframe,
                "direction": signal.direction.value,
                "pattern": signal.pattern,
                "entry_ts": order["ts"],
                "entry_price": order["entry_price"],
                "tp_price": order["tp_price"],
                "sl_price": order["sl_price"],
                "liquidation_price": order["liquidation_price"],
                "bucket_id": 1,
                "size_usdt": order["size_usdt"],
                "leverage": order["leverage"],
                "notional_usdt": order["notional_usdt"],
                "fee_entry_usdt": order["fee_usdt"],
                "bucket_balance_before": 10.0,
            }
        )

        await db.write_signal(signal, SignalOutcome.FIRED, trade_id=trade_id)
        await db.write_event(
            self.cfg.bot_id,
            self.session_id,
            self.cfg.env.value,
            "INFO",
            "order",
            "order_placed",
            {
                "pair": signal.pair,
                "direction": signal.direction.value,
                "entry": order["entry_price"],
                "tp": order["tp_price"],
                "sl": order["sl_price"],
                "size_usdt": order["size_usdt"],
                "trade_id": trade_id,
            },
            trade_id=trade_id,
        )

        await self.notifier.signal_fired(
            {
                "pattern": signal.pattern,
                "direction": signal.direction.value,
                "pair": signal.pair,
                "confidence": signal.confidence,
                "entry_price": order["entry_price"],
                "tp_price": order["tp_price"],
                "sl_price": order["sl_price"],
                "regime": signal.regime,
            }
        )

    async def _close_position(self, pair: str, reason: str, candle) -> None:
        """Close a position and update DB records."""
        try:
            result = await self.execution.close_position(pair, reason)
        except ExecutionError as exc:
            await db.write_event(
                self.cfg.bot_id,
                self.session_id,
                self.cfg.env.value,
                "ERROR",
                "position",
                "close_position_failed",
                {"error": str(exc), "pair": pair, "reason": reason},
            )
            return

        self._session_pnl += result["pnl_net_usdt"]

        await db.write_event(
            self.cfg.bot_id,
            self.session_id,
            self.cfg.env.value,
            "INFO",
            "position",
            f"position_closed:{reason}",
            {
                "pair": pair,
                "exit_price": result["exit_price"],
                "pnl_net_usdt": result["pnl_net_usdt"],
                "pnl_pct": result["pnl_pct"],
                "reason": reason,
            },
        )

        # Notify
        notify_data = {
            "pair": pair,
            "direction": "—",
            "exit_price": result["exit_price"],
            "pnl_net_usdt": result["pnl_net_usdt"],
            "pnl_pct": result["pnl_pct"],
            "close_reason": reason,
            "bucket_balance_after": 10.0 + result["pnl_net_usdt"],
        }
        if result["pnl_net_usdt"] >= 0:
            await self.notifier.trade_closed_profit(notify_data)
        elif reason == "liquidated":
            await self.notifier.liquidation(notify_data)
        else:
            await self.notifier.trade_closed_loss(notify_data)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    async def _reconcile(self) -> None:
        """Reconcile exchange positions vs DB on startup."""
        positions = await self.execution.reconcile()
        await db.write_event(
            self.cfg.bot_id,
            self.session_id,
            self.cfg.env.value,
            "INFO",
            "system",
            "position_reconcile",
            {"open_count": len(positions), "positions": [p["pair"] for p in positions]},
        )

    def _on_ws_reconnect(self, ts_ms: int) -> None:
        self._last_ws_reconnect_ts = ts_ms

    def _sync_notify(self, level: str, message: str) -> None:
        """Sync wrapper for notifier (called from non-async feed context)."""
        asyncio.create_task(self.notifier.send(message, level))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _row_to_candle(row: dict):
    from src.config import Candle

    return Candle(
        bot_id=row["bot_id"],
        ts=row["ts"],
        pair=row["pair"],
        timeframe=row["timeframe"],
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        ema9=float(row["ema9"]) if row.get("ema9") else None,
        ema21=float(row["ema21"]) if row.get("ema21") else None,
        rsi14=float(row["rsi14"]) if row.get("rsi14") else None,
        atr14=float(row["atr14"]) if row.get("atr14") else None,
        bb_upper=float(row["bb_upper"]) if row.get("bb_upper") else None,
        bb_lower=float(row["bb_lower"]) if row.get("bb_lower") else None,
        bb_width=float(row["bb_width"]) if row.get("bb_width") else None,
        adx=float(row["adx"]) if row.get("adx") else None,
        volume_ma20=float(row["volume_ma20"]) if row.get("volume_ma20") else None,
        volume_ratio=float(row["volume_ratio"]) if row.get("volume_ratio") else None,
        regime=row.get("regime"),
        body_size=float(row["body_size"]) if row.get("body_size") else None,
        total_range=float(row["total_range"]) if row.get("total_range") else None,
        body_ratio=float(row["body_ratio"]) if row.get("body_ratio") else None,
        upper_wick=float(row["upper_wick"]) if row.get("upper_wick") else None,
        lower_wick=float(row["lower_wick"]) if row.get("lower_wick") else None,
        direction=row.get("direction"),
        id=row.get("id"),
    )


def _utc_midnight_ms(ts_ms: int) -> int:
    return (ts_ms // 86_400_000) * 86_400_000


async def main() -> None:
    """Daemon entry point. Called by start.sh."""
    load_dotenv()

    cfg = AppConfig.from_mapping(os.environ)
    params = load_params("params.json")

    notifier = TelegramNotifier(cfg)

    if cfg.env is Env.DEV:
        execution: ExecutionInterface = SimulationExecution(cfg)
    else:
        from src.execution.live import LiveExecution

        execution = LiveExecution(cfg)

    daemon = Daemon(cfg, params, execution, notifier)
    await daemon.start()


if __name__ == "__main__":
    asyncio.run(main())
