from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import re


def parse_int(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return default


def parse_time_hhmm(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return (9, 0)
    hour = max(0, min(23, int(match.group(1))))
    minute = max(0, min(59, int(match.group(2))))
    return (hour, minute)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def days_ago(days: int) -> datetime:
    return utc_now() - timedelta(days=days)
