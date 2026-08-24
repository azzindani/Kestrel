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

FLEET LIVENESS (added 2026-08-24, after the disk-full recurrence): the disk half
of this watchdog worked perfectly on 2026-08-09 — it alerted, repeatedly, for
two weeks — and the fleet still stayed dead for 15 days, because *nobody was
watching the thing the disk alarm was a proxy for*. Docker could not remount the
containers, marked them `exited`, and (worse) kept reporting them as "Up" to
`docker ps`, so every surface a human would check looked healthy. The disk alarm
answers "is the disk about to kill the fleet?"; it never answers "is the fleet
alive?". This second probe answers that directly and cheaply, off the one source
that cannot lie about it: the heartbeats table (§19 — one row per bot_id, written
by each daemon's heartbeat task, so it goes stale the instant a daemon stops).

Baseline is the count of REGISTERED bot_ids (every row in heartbeats) rather than
a rolling recent window, because a recent-window baseline decays to zero once the
fleet has been down longer than the window — which would have re-silenced the
alarm on day two of the very outage it exists to catch. Retired bot_ids linger in
that count, so the degraded line is a fraction, not equality.
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

# Fleet-liveness probe. A bot is "beating" if its heartbeat row was written within
# _STALE_AFTER_S; the daemon's heartbeat task fires every 30s (§16), so a 15-minute
# window is ~30 missed beats — long past any transient DB or feed hiccup.
_STALE_AFTER_S = int(os.environ.get("FLEET_STALE_AFTER_S", "900"))
_FLEET_DEGRADED_RATIO = float(os.environ.get("FLEET_DEGRADED_RATIO", "0.5"))

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


def classify_fleet(beating: int, registered: int) -> str:
    """Pure: map heartbeat counts to an alert level.

    `registered` == 0 means no bot has ever checked in (a fresh DB), which is not
    an outage — there is nothing to be down.
    """
    if registered <= 0:
        return "OK"
    if beating == 0:
        return "CRITICAL"
    if beating < registered * _FLEET_DEGRADED_RATIO:
        return "WARN"
    return "OK"


async def fleet_counts() -> tuple[int, int]:
    """I/O shell: (bots beating within the stale window, bots ever registered)."""
    cutoff_ms = int((time.time() - _STALE_AFTER_S) * 1000)
    async with db_conn.acquire() as conn:
        beating = int(await conn.fetchval("SELECT count(*) FROM heartbeats WHERE ts > $1", cutoff_ms))
        registered = int(await conn.fetchval("SELECT count(*) FROM heartbeats"))
    return beating, registered


async def check_fleet(
    cfg: AppConfig,
    notifier: TelegramNotifier,
    alert_ts: dict[str, float],
    last_level: str,
) -> str:
    """I/O shell: probe fleet liveness, alert on transition or re-alert interval.

    Returns the level to carry into the next tick. Never raises — a DB blip must
    not take down the watchdog that exists to report DB-adjacent outages.
    """
    try:
        beating, registered = await fleet_counts()
    except Exception as exc:  # noqa: BLE001 — watchdog must survive any probe failure
        await notifier.send(f"[WARN] fleet liveness probe failed: {type(exc).__name__}", "WARN")
        return last_level

    level = classify_fleet(beating, registered)
    now = time.time()
    payload = {"beating": beating, "registered": registered, "stale_after_s": _STALE_AFTER_S}

    if level != "OK" and (level != last_level or now - alert_ts.get(level, 0.0) >= _REALERT_S):
        alert_ts[level] = now
        detail = (
            "NO bots are beating — the fleet is down" if beating == 0 else f"only {beating}/{registered} bots beating"
        )
        await db.write_event(_BOT_ID, _SESSION, cfg.env.value, level, "system", "fleet_liveness_alarm", payload)
        await notifier.send(
            f"[{level}] {detail} (heartbeats stale >{_STALE_AFTER_S // 60}m) — "
            "check `docker inspect` state, not `docker ps` (it reports stale 'Up')",
            level,
        )
    elif level == "OK" and last_level != "OK":
        await db.write_event(_BOT_ID, _SESSION, cfg.env.value, "INFO", "system", "fleet_liveness_recovered", payload)
        await notifier.send(f"fleet recovered: {beating}/{registered} bots beating", "INFO")
    return level


async def run() -> None:
    load_dotenv()
    cfg = AppConfig.from_mapping(os.environ)
    await db_conn.init_pool(cfg)
    notifier = TelegramNotifier(cfg)
    await notifier.start()

    last_alert_ts: dict[str, float] = {}
    last_level = "OK"
    fleet_alert_ts: dict[str, float] = {}
    fleet_last_level = "OK"
    try:
        while True:
            fleet_last_level = await check_fleet(cfg, notifier, fleet_alert_ts, fleet_last_level)
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
