"""Tests for the format_date template filter — specifically the `utc` flag that
shows app-generated UTC timestamps (Job started_at/completed_at) in local time while
leaving naive-local date_taken values formatted as-is.
"""
import os
import time
from datetime import datetime, timezone

import pytest
from flask import Flask
from flask_babel import Babel

from yaffo.template_filters import (
    DateFormat,
    format_coordinate,
    format_date,
    format_decimal,
    format_duration,
    format_integer,
    format_percent,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def tz_new_york():
    """Pin the process timezone to America/New_York so local-time assertions are
    deterministic. (Unix-only via tzset; tests run on macOS.)"""
    original = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


def test_utc_false_formats_naive_value_as_is(tz_new_york):
    # date_taken is local wall-clock; no shift regardless of the process timezone.
    dt = datetime(2022, 7, 3, 17, 28, 22)
    assert format_date(dt, DateFormat.DATETIME, utc=False) == "Jul 3, 2022, 5:28:22\u202fPM"


def test_utc_true_converts_to_local(tz_new_york):
    # A naive UTC timestamp shown in local time: 17:28 UTC → 13:28 EDT (UTC-4 in July).
    dt = datetime(2022, 7, 3, 17, 28, 22)
    assert format_date(dt, DateFormat.DATETIME, utc=True) == "Jul 3, 2022, 1:28:22\u202fPM"


def test_utc_true_handles_aware_value(tz_new_york):
    # An already-aware UTC datetime is converted to local too.
    dt = datetime(2022, 7, 3, 17, 28, 22, tzinfo=timezone.utc)
    assert format_date(dt, DateFormat.DATETIME, utc=True) == "Jul 3, 2022, 1:28:22\u202fPM"


def test_none_returns_empty():
    assert format_date(None, utc=True) == ""


class TestFormatDuration:
    def test_none_returns_empty(self):
        assert format_duration(None) == ""

    def test_seconds_only(self):
        assert format_duration(42) == "0:42"

    def test_minutes_zero_pads_seconds(self):
        assert format_duration(187) == "3:07"

    def test_hours(self):
        assert format_duration(3729) == "1:02:09"

    def test_truncates_fractional_seconds(self):
        assert format_duration(42.9) == "0:42"


def test_number_filters_use_active_locale():
    app = Flask(__name__)
    app.config["BABEL_DEFAULT_LOCALE"] = "de"
    Babel(app)

    with app.app_context():
        assert format_integer(1234567) == "1.234.567"
        assert format_decimal(1234.567, 2) == "1.234,57"
        assert format_percent(0.956, 2) == "95,60%"
        assert format_coordinate(-90.482583) == "-90,482583°"


def test_named_date_width_uses_active_locale():
    app = Flask(__name__)
    app.config["BABEL_DEFAULT_LOCALE"] = "de"
    Babel(app)

    with app.app_context():
        assert format_date(datetime(2022, 7, 3), DateFormat.DATE) == "03.07.2022"
