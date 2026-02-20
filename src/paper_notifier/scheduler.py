from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import RUN_TIME, SCHEDULER_MISFIRE_GRACE_SECONDS, TIMEZONE
from .utils import parse_time_hhmm


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


def schedule_daily(job_func) -> None:
    hour, minute = parse_time_hhmm(RUN_TIME)
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    trigger = CronTrigger(hour=hour, minute=minute)
    scheduler.add_job(
        job_func,
        trigger=trigger,
        name="daily-paper-notifier",
        misfire_grace_time=SCHEDULER_MISFIRE_GRACE_SECONDS,
    )
    now = datetime.now(scheduler.timezone)
    next_run = trigger.get_next_fire_time(None, now)
    print(
        "[paper-notifier] scheduler started "
        f"(timezone={TIMEZONE}, run_time={hour:02d}:{minute:02d}, "
        f"misfire_grace={SCHEDULER_MISFIRE_GRACE_SECONDS}s)"
    )
    if next_run is not None:
        seconds_until = int((next_run - now).total_seconds())
        pretty_next_run = next_run.strftime("%Y-%m-%d %H:%M:%S %Z")
        print(
            f"[paper-notifier] next run at {pretty_next_run} "
            f"({TIMEZONE}) "
            f"(in {_format_countdown(seconds_until)})"
        )
    scheduler.start()
