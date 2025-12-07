from datetime import datetime
from enum import Enum
from typing import Union, Optional


class DateFormat(Enum):
    DATE = "%b %d, %Y"
    DATETIME = "%b %d, %Y, %I:%M %p"
    TIME = "%I:%M %p"


def format_date(
    value: Union[datetime, str, None],
    format_type: DateFormat = DateFormat.DATETIME
) -> str:
    """
    Format a datetime object or ISO string for display in templates.

    Args:
        value: A datetime object, ISO date string, or None
        format_type: DateFormat enum value (DATE, DATETIME, or TIME)

    Returns:
        Formatted date string, or empty string if value is None

    Usage in templates:
        {{ job.started_at | format_date }}
        {{ job.started_at | format_date(DateFormat.DATE) }}
    """
    if value is None:
        return ""

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return value

    if isinstance(value, datetime):
        return value.strftime(format_type.value)

    return str(value)


def init_template_filters(app):
    """Register custom template filters with the Flask app."""

    @app.template_filter('format_date')
    def format_date_filter(value, format_type='datetime'):
        """
        Template filter version that accepts string format type for easier use in templates.

        Usage:
            {{ job.started_at | format_date }}
            {{ job.started_at | format_date('date') }}
        """
        format_map = {
            'date': DateFormat.DATE,
            'datetime': DateFormat.DATETIME,
            'time': DateFormat.TIME,
        }
        fmt = format_map.get(format_type.lower(), DateFormat.DATETIME)
        return format_date(value, fmt)

    # Also make DateFormat enum available in templates
    app.jinja_env.globals['DateFormat'] = DateFormat