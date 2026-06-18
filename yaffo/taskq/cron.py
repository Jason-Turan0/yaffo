"""Minimal cron support. We only need minute-granular periodic dispatch (the one
periodic task fires every minute), so this is intentionally tiny -- not a full
crontab engine. `crontab(minute='*')` means "fire on every new minute"."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CronSpec:
    minute: str = "*"

    def due_minute(self, now: datetime) -> int:
        """The epoch-minute slot `now` falls in; the host fires a periodic task at
        most once per slot (single-fire guarantee)."""
        return int(now.timestamp()) // 60


def crontab(minute: str = "*") -> CronSpec:
    return CronSpec(minute=minute)
