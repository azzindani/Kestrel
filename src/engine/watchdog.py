"""
Layer 1 — watchdog process supervisor.

Monitors the main daemon process and restarts it on unexpected exit.
Also performs heartbeat checks every 60 seconds (CLAUDE.md §16).

The watchdog runs as a separate OS process, started by start.sh.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time


_RESTART_DELAY = 10  # seconds after crash before restart (CLAUDE.md §16)
_HEARTBEAT_INTERVAL = 60  # seconds
_HEARTBEAT_TIMEOUT = 90   # seconds since last heartbeat before considered dead


def run_watchdog(main_cmd: list[str], db_url: str, bot_id: str) -> None:
    """
    Launch and supervise the main process.

    Args:
        main_cmd: Command to launch the daemon (e.g. ['python', '-m', 'src.engine.daemon'])
        db_url:   PostgreSQL DSN for heartbeat checks
        bot_id:   Bot identifier for heartbeat lookups
    """
    import asyncpg

    child: subprocess.Popen | None = None

    def _start_child() -> subprocess.Popen:
        proc = subprocess.Popen(main_cmd, env=os.environ.copy())
        return proc

    async def _check_heartbeat() -> bool:
        """Return True if heartbeat is fresh, False if stale or absent."""
        try:
            conn = await asyncpg.connect(dsn=db_url, command_timeout=5)
            try:
                row = await conn.fetchrow(
                    "SELECT ts FROM heartbeats WHERE bot_id = $1", bot_id
                )
                if row is None:
                    return False
                elapsed = time.time() - row["ts"] / 1000.0
                return elapsed < _HEARTBEAT_TIMEOUT
            finally:
                await conn.close()
        except Exception:
            return True  # DB unreachable — don't kill the child on DB issues

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

        # Monitor loop
        while True:
            time.sleep(_HEARTBEAT_INTERVAL)

            rc = child.poll()
            if rc is not None:
                # Child exited
                if rc == 0:
                    # Graceful shutdown — watchdog exits too
                    sys.exit(0)
                # Crash — restart
                elapsed = time.time() - start_time
                print(  # watchdog itself may print — not the daemon
                    f"[watchdog] main process exited (rc={rc}) after {elapsed:.0f}s. "
                    f"Restarting in {_RESTART_DELAY}s.",
                    flush=True,
                )
                time.sleep(_RESTART_DELAY)
                break  # break inner loop → restart child

            # Check heartbeat
            fresh = asyncio.run(_check_heartbeat())
            if not fresh:
                print(
                    f"[watchdog] Heartbeat stale for {bot_id}. Killing and restarting.",
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
