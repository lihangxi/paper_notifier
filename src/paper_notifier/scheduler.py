from __future__ import annotations

from datetime import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import RUN_TIME, TIMEZONE
from .utils import parse_time_hhmm


def schedule_daily(job_func) -> None:
    hour, minute = parse_time_hhmm(RUN_TIME)
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    trigger = CronTrigger(hour=hour, minute=minute)
    scheduler.add_job(job_func, trigger=trigger, name="daily-paper-notifier")
    scheduler.start()
