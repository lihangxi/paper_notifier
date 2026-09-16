from __future__ import annotations

import os
import socket
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import RUN_TIME, SCHEDULER_MISFIRE_GRACE_SECONDS, TIMEZONE
from .utils import parse_time_hhmm

# Loopback port used as a best-effort single-instance lock for the scheduler.
_SINGLE_INSTANCE_PORT = 47811


class _SingleInstanceLock:
    """Keep a loopback socket bound while a scheduler instance runs.

    A second scheduler fails fast with a clear message instead of silently
    posting duplicate Slack notifications.
    """

    def __init__(self, port: int = _SINGLE_INSTANCE_PORT) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._socket.bind(("127.0.0.1", port))
            self._socket.listen(1)
        except OSError as exc:
            self._socket.close()
            raise SystemExit(
                "[paper-notifier] another scheduler is already running "
                f"(localhost port {port} is in use); exiting."
            ) from exc

    def close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass


def _format_countdown(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _print_next_run(trigger: CronTrigger, now: datetime, *, prefix: str = "next run at") -> None:
    next_run = trigger.get_next_fire_time(None, now)
    if next_run is None:
        return

    seconds_until = int((next_run - now).total_seconds())
    pretty_next_run = next_run.strftime("%Y-%m-%d %H:%M:%S %Z")
    print(
        f"[paper-notifier] {prefix} {pretty_next_run} "
        f"({TIMEZONE}) "
        f"(in {_format_countdown(seconds_until)})"
    )


def schedule_daily(job_func) -> None:
    guard = _SingleInstanceLock()

    hour, minute = parse_time_hhmm(RUN_TIME)
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    trigger = CronTrigger(hour=hour, minute=minute)

    def _scheduled_job_wrapper() -> None:
        try:
            job_func()
        except Exception:
            _print_next_run(
                trigger,
                datetime.now(scheduler.timezone),
                prefix="run failed; next run at",
            )
            raise
        else:
            _print_next_run(
                trigger,
                datetime.now(scheduler.timezone),
                prefix="run completed; next run at",
            )

    scheduler.add_job(
        _scheduled_job_wrapper,
        trigger=trigger,
        name="daily-paper-notifier",
        misfire_grace_time=SCHEDULER_MISFIRE_GRACE_SECONDS,
    )
    now = datetime.now(scheduler.timezone)
    next_run = trigger.get_next_fire_time(None, now)
    print(
        "[paper-notifier] scheduler started "
        f"(pid={os.getpid()}, timezone={TIMEZONE}, run_time={hour:02d}:{minute:02d}, "
        f"misfire_grace={SCHEDULER_MISFIRE_GRACE_SECONDS}s)"
    )
    if next_run is not None:
        _print_next_run(trigger, now)
    try:
        scheduler.start()
    finally:
        guard.close()
