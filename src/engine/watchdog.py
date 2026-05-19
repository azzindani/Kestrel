"""
Layer 1 — watchdog process supervisor.

Monitors the main daemon process and restarts it on unexpected exit.
Also performs heartbeat checks every 60 seconds (CLAUDE.md §16).

The watchdog runs as a separate OS process, started by start.sh.

Multi-bot: if bots.json exists in the working directory, the watchdog checks
heartbeats for ALL bots listed there.  If any bot's heartbeat is stale the
whole process is killed and restarted (the daemon process owns all bots).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time

_RESTART_DELAY = 10  # seconds after crash before restart (CLAUDE.md §16)
_HEARTBEAT_INTERVAL = 60  # seconds
_HEARTBEAT_TIMEOUT = 90  # seconds since last heartbeat before considered dead


def _load_bot_ids(fallback_bot_id: str) -> list[str]:
    """Return list of bot IDs to monitor.

    Reads bots.json from the working directory when present; falls back to
    the single bot_id passed on the CLI so start.sh needs no changes.
    """
    try:
        with open("bots.json") as fh:
            entries = json.load(fh)
        ids = [e["bot_id"] for e in entries if "bot_id" in e]
        return ids if ids else [fallback_bot_id]
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return [fallback_bot_id]


def run_watchdog(main_cmd: list[str], db_url: str, bot_id: str) -> None:
    """
    Launch and supervise the main process.

    Args:
        main_cmd: Command to launch the daemon (e.g. ['python', '-m', 'src.engine.daemon'])
        db_url:   PostgreSQL DSN for heartbeat checks
        bot_id:   Fallback bot identifier used when bots.json is absent
    """
    import asyncpg

    child: subprocess.Popen | None = None

    def _start_child() -> subprocess.Popen:
        return subprocess.Popen(main_cmd, env=os.environ.copy())

    async def _check_heartbeats() -> tuple[bool, list[str]]:
        """Return (all_fresh, list_of_stale_bot_ids).

        Returns (True, []) when all monitored bots have fresh heartbeats or
        when the DB is unreachable (don't kill on transient DB issues).
        """
        bot_ids = _load_bot_ids(bot_id)
        stale: list[str] = []
        try:
            conn = await asyncpg.connect(dsn=db_url, command_timeout=5)
            try:
                for bid in bot_ids:
                    row = await conn.fetchrow("SELECT ts FROM heartbeats WHERE bot_id = $1", bid)
                    if row is None:
                        stale.append(bid)
                        continue
                    elapsed = time.time() - row["ts"] / 1000.0
                    if elapsed >= _HEARTBEAT_TIMEOUT:
                        stale.append(bid)
            finally:
                await conn.close()
        except Exception:
            return True, []  # DB unreachable — don't kill on DB issues

        return len(stale) == 0, stale

    def _handle_sigterm(signum, frame):
        """Forward SIGTERM to child and exit cleanly."""
        if child and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=30)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    while True:
        child = _start_child()
        start_time = time.time()

        while True:
            time.sleep(_HEARTBEAT_INTERVAL)

            rc = child.poll()
            if rc is not None:
                if rc == 0:
                    sys.exit(0)
                elapsed = time.time() - start_time
                print(
                    f"[watchdog] main process exited (rc={rc}) after {elapsed:.0f}s. Restarting in {_RESTART_DELAY}s.",
                    flush=True,
                )
                time.sleep(_RESTART_DELAY)
                break

            all_fresh, stale_ids = asyncio.run(_check_heartbeats())
            if not all_fresh:
                print(
                    f"[watchdog] Stale heartbeat(s): {stale_ids}. Killing and restarting.",
                    flush=True,
                )
                child.kill()
                child.wait()
                time.sleep(_RESTART_DELAY)
                break


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Kestrel watchdog")
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    run_watchdog(args.cmd, args.db_url, args.bot_id)
