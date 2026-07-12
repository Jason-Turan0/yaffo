from datetime import date, datetime, timezone
from enum import Enum
from numbers import Number
from typing import Union

from babel.dates import format_date as babel_format_date
from babel.dates import format_datetime as babel_format_datetime
from babel.dates import format_time as babel_format_time
from babel.numbers import format_decimal as babel_format_decimal
from babel.numbers import format_percent as babel_format_percent
from flask import has_app_context
from flask_babel import get_locale

from yaffo.common import is_browser_playable_video
from yaffo.i18n import DEFAULT_LOCALE


class DateFormat(Enum):
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"


DateValue = Union[date, datetime, str, None]
NumericValue = Union[Number, str, None]


def _locale() -> str:
    return str(get_locale()) if has_app_context() else DEFAULT_LOCALE


def _parse_datetime(value: DateValue) -> datetime | str | None:
    if not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return value


def format_date(
    value: DateValue,
    format_type: DateFormat = DateFormat.DATETIME,
    utc: bool = False,
) -> str:
    """Format camera-local values as-is and UTC application timestamps locally."""
    parsed = _parse_datetime(value)
    if parsed is None:
        return ""
    if isinstance(parsed, date) and not isinstance(parsed, datetime):
        return babel_format_date(parsed, format="medium", locale=_locale())
    if not isinstance(parsed, datetime):
        return str(parsed)

    display_value = parsed
    if utc:
        aware = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        display_value = aware.astimezone()

    if format_type is DateFormat.DATE:
        return babel_format_date(display_value.date(), format="medium", locale=_locale())
    if format_type is DateFormat.TIME:
        return babel_format_time(display_value, format="short", locale=_locale())
    return babel_format_datetime(display_value, format="medium", locale=_locale())


def utc_iso(value: DateValue) -> str:
    """Serialize a UTC application timestamp with an explicit offset for browser display."""
    parsed = _parse_datetime(value)
    if not isinstance(parsed, datetime):
        return ""
    aware = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return aware.isoformat()


def format_integer(value: NumericValue) -> str:
    if value is None:
        return ""
    return babel_format_decimal(
        value,
        format="#,##0",
        locale=_locale(),
        decimal_quantization=True,
    )


def format_decimal(value: NumericValue, digits: int = 2) -> str:
    if value is None:
        return ""
    safe_digits = max(0, int(digits))
    pattern = "#,##0" if safe_digits == 0 else f"#,##0.{('#' * safe_digits)}"
    return babel_format_decimal(
        value,
        format=pattern,
        locale=_locale(),
        decimal_quantization=True,
    )


def format_percent(value: NumericValue, digits: int = 0) -> str:
    if value is None:
        return ""
    safe_digits = max(0, int(digits))
    pattern = "#,##0%" if safe_digits == 0 else f"#,##0.{('0' * safe_digits)}%"
    return babel_format_percent(
        value,
        format=pattern,
        locale=_locale(),
        decimal_quantization=True,
    )


def format_coordinate(value: NumericValue, digits: int = 6) -> str:
    if value is None:
        return ""
    return f"{format_decimal(value, digits)}°"


def format_duration(seconds: Union[int, float, None]) -> str:
    """Format elapsed video time as M:SS or H:MM:SS."""
    if seconds is None:
        return ""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{format_integer(hours)}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def init_template_filters(app) -> None:
    app.add_template_filter(format_duration, "format_duration")
    app.add_template_filter(format_integer, "format_integer")
    app.add_template_filter(format_decimal, "format_decimal")
    app.add_template_filter(format_percent, "format_percent")
    app.add_template_filter(format_coordinate, "format_coordinate")
    app.add_template_filter(utc_iso, "utc_iso")

    @app.template_filter("format_date")
    def format_date_filter(
        value: DateValue,
        format_type: str = "datetime",
        utc: bool = False,
    ) -> str:
        format_map = {
            "date": DateFormat.DATE,
            "datetime": DateFormat.DATETIME,
            "time": DateFormat.TIME,
        }
        fmt = format_map.get(format_type.lower(), DateFormat.DATETIME)
        return format_date(value, fmt, utc=utc)

    app.jinja_env.globals["DateFormat"] = DateFormat
    app.jinja_env.globals["is_browser_playable_video"] = is_browser_playable_video
