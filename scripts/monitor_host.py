#!/usr/bin/env python3
"""Host-resource watchdog — alerts BEFORE the disk kills the fleet again.

Motivation (RESEARCH_LOOP iter 63, 2026-07-20 incident): a co-tenant project
filled the host disk to 100%; docker could not remount the kestrel containers
and the whole fleet died SILENTLY for ~2.5 days — no Telegram, no event row,
nothing. This watchdog is the missing alarm, not a fixer: it only observes and
alerts (no docker socket, no privileged mounts — the recovery action stays a
human/agent decision).

Runs as its own aux compose service (like the microstructure recorder): the
host filesystem is bind-mounted READ-ONLY at /host and probed with statvfs, so
the numbers are the HOST disk, not this container's overlay. Alerts go to the
events table (level WARN/CRITICAL, category system) and Telegram. Re-alerts at
most once per _REALERT_S per severity while the condition persists; recovery
back below the warn line emits one INFO all-clear.

Thresholds: >= _WARN_PCT → WARN · >= _CRIT_PCT → CRITICAL. The gap between the
crit line and 100% is the reaction window — at the incident's fill rate
(~10GB/day of image rebuilds) 94% on this 193GB disk leaves roughly a day.
"""

from __future__ import annotations

import asyncio
import os
import time

from dotenv import load_dotenv

from src.config import AppConfig
from src.db import connection as db_conn
from src.db import writer as db
from src.notify.telegram import TelegramNotifier

_HOST_ROOT = os.environ.get("HOST_ROOT_MOUNT", "/host")
_CHECK_INTERVAL_S = 300
_REALERT_S = 6 * 3600
_WARN_PCT = 88.0
_CRIT_PCT = 94.0

_BOT_ID = "aux-host-monitor"
_SESSION = "host-monitor"


def disk_used_pct(path: str) -> float:
    """Pure: percent of the filesystem at `path` in use (0-100)."""
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if total <= 0:
        return 0.0
    return (total - free) / total * 100.0


def classify(used_pct: float) -> str:
    """Pure: map a usage percentage to an alert level."""
    if used_pct >= _CRIT_PCT:
        return "CRITICAL"
    if used_pct >= _WARN_PCT:
        return "WARN"
    return "OK"


async def run() -> None:
    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    await db_conn.init_pool(cfg)
    notifier = TelegramNotifier(cfg)
    await notifier.start()

    last_alert_ts: dict[str, float] = {}
    last_level = "OK"
    try:
        while True:
            used = disk_used_pct(_HOST_ROOT)
            level = classify(used)
            now = time.time()
            if level != "OK" and (level != last_level or now - last_alert_ts.get(level, 0.0) >= _REALERT_S):
                last_alert_ts[level] = now
                msg = (
                    f"host disk {used:.1f}% used (threshold {'CRIT ' + str(_CRIT_PCT) if level == 'CRITICAL' else 'WARN ' + str(_WARN_PCT)}%) — "
                    "at 100% docker kills the fleet silently (iter-63 incident); reclaim space now"
                )
                await db.write_event(
                    _BOT_ID,
                    _SESSION,
                    cfg.env.value,
                    level,
                    "system",
                    "host_disk_pressure",
                    {"used_pct": round(used, 1)},
                )
                await notifier.send(f"[{level}] {msg}", level)
            elif level == "OK" and last_level != "OK":
                await db.write_event(
                    _BOT_ID,
                    _SESSION,
                    cfg.env.value,
                    "INFO",
                    "system",
                    "host_disk_recovered",
                    {"used_pct": round(used, 1)},
                )
                await notifier.send(f"host disk recovered: {used:.1f}% used", "INFO")
            last_level = level
            await asyncio.sleep(_CHECK_INTERVAL_S)
    finally:
        await notifier.stop()
        await db_conn.close_pool()


if __name__ == "__main__":
    asyncio.run(run())
