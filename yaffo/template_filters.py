from datetime import datetime, timezone
from enum import Enum
from typing import Union, Optional


class DateFormat(Enum):
    DATE = "%b %d, %Y"
    DATETIME = "%b %d, %Y, %I:%M %p"
    TIME = "%I:%M %p"


def format_date(
    value: Union[datetime, str, None],
    format_type: DateFormat = DateFormat.DATETIME,
    utc: bool = False,
) -> str:
    """
    Format a datetime object or ISO string for display in templates.

    Args:
        value: A datetime object, ISO date string, or None
        format_type: DateFormat enum value (DATE, DATETIME, or TIME)
        utc: Set True when `value` is a naive UTC timestamp (e.g. an app-generated
            `datetime.utcnow()` like a Job's started_at/completed_at) so it's shown in
            the user's local time. Leave False for `date_taken`, which is already the
            camera's local wall-clock and must be formatted as-is (see Photo.date_taken).

    Returns:
        Formatted date string, or empty string if value is None

    Usage in templates:
        {{ photo.date_taken | format_date('date') }}   # local capture time, as-is
        {{ job.started_at | format_date(utc=True) }}    # UTC timestamp → local
    """
    if value is None:
        return ""

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return value

    if isinstance(value, datetime):
        if utc:
            # Treat a naive value as UTC, then convert to the local timezone (the
            # app runs on the user's own machine, so local == the user's clock).
            aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
            value = aware.astimezone()
        return value.strftime(format_type.value)

    return str(value)


def format_duration(seconds: Union[int, float, None]) -> str:
    """Format a video length in seconds as a compact clock string: "0:42",
    "3:07", "1:02:09". Returns "" for a missing value."""
    if seconds is None:
        return ""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def init_template_filters(app):
    """Register custom template filters with the Flask app."""

    app.add_template_filter(format_duration, "format_duration")

    @app.template_filter('format_date')
    def format_date_filter(value, format_type='datetime', utc=False):
        """
        Template filter version that accepts string format type for easier use in templates.

        Usage:
            {{ photo.date_taken | format_date('date') }}   # local capture time, as-is
            {{ job.started_at | format_date(utc=True) }}    # UTC timestamp → local
        """
        format_map = {
            'date': DateFormat.DATE,
            'datetime': DateFormat.DATETIME,
            'time': DateFormat.TIME,
        }
        fmt = format_map.get(format_type.lower(), DateFormat.DATETIME)
        return format_date(value, fmt, utc=utc)

    # Also make DateFormat enum available in templates
    app.jinja_env.globals['DateFormat'] = DateFormat