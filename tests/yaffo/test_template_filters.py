"""Tests for the format_date template filter — specifically the `utc` flag that
shows app-generated UTC timestamps (Job started_at/completed_at) in local time while
leaving naive-local date_taken values formatted as-is.
"""
import os
import time
from datetime import datetime, timezone

import pytest

from yaffo.template_filters import DateFormat, format_date

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
    assert format_date(dt, DateFormat.DATETIME, utc=False) == "Jul 03, 2022, 05:28 PM"


def test_utc_true_converts_to_local(tz_new_york):
    # A naive UTC timestamp shown in local time: 17:28 UTC → 13:28 EDT (UTC-4 in July).
    dt = datetime(2022, 7, 3, 17, 28, 22)
    assert format_date(dt, DateFormat.DATETIME, utc=True) == "Jul 03, 2022, 01:28 PM"


def test_utc_true_handles_aware_value(tz_new_york):
    # An already-aware UTC datetime is converted to local too.
    dt = datetime(2022, 7, 3, 17, 28, 22, tzinfo=timezone.utc)
    assert format_date(dt, DateFormat.DATETIME, utc=True) == "Jul 03, 2022, 01:28 PM"


def test_none_returns_empty():
    assert format_date(None, utc=True) == ""
