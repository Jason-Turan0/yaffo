from datetime import datetime

from croniter import croniter


def is_valid_cron(cron: str) -> bool:
    """Whether `cron` is a schedule croniter can parse (use before persisting a row)."""
    return croniter.is_valid(cron)


def compute_next_run(cron: str, after: datetime) -> datetime:
    """Next datetime matching the 5-field `cron` expression strictly after `after`.

    Used by the dispatcher to advance a schedule's next_run_at. Raises if `cron`
    is malformed (croniter.CroniterBadCronError / ValueError)."""
    return croniter(cron, after).get_next(datetime)