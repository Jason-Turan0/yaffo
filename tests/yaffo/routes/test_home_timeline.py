"""The home page's timeline view: same route, filters, and cards as the grid,
switched by ?view=timeline (persisted as the preferred view). The page's items
group under day headers with month dividers; the scrubber rail maps each month
of the filtered library to the page where it starts (offset // page_size)."""
import json

import pytest

from yaffo.db import db
from yaffo.db.models import ApplicationSettings, MediaItem


@pytest.fixture
def dated_library(app):
    """31 dated photos (30 in July 2025 across two days, 1 in June 2024) plus one
    undated — enough to spread months across pages at page-size 25."""
    with app.app_context():
        items = []
        for i in range(20):
            items.append(MediaItem(
                full_file_path=f"/media/2025/07/a_{i}.jpg",
                date_taken=f"2025-07-20T10:{i:02d}:00", year=2025, month=7,
            ))
        for i in range(10):
            items.append(MediaItem(
                full_file_path=f"/media/2025/07/b_{i}.jpg",
                date_taken=f"2025-07-03T09:{i:02d}:00", year=2025, month=7,
            ))
        items.append(MediaItem(
            full_file_path="/media/2024/06/old.jpg",
            date_taken="2024-06-15T12:00:00", year=2024, month=6,
        ))
        items.append(MediaItem(full_file_path="/media/undated.jpg"))
        db.session.add_all(items)
        db.session.commit()


def test_default_view_is_grid(client, dated_library):
    body = client.get("/").data.decode()

    assert 'class="photo-grid"' in body
    assert "timeline-day-header" not in body
    assert "timeline-scrubber" not in body


def test_timeline_groups_by_day_with_month_dividers(client, dated_library):
    body = client.get("/?view=timeline&page-size=250").data.decode()

    # Two July days + one June day + the undated group.
    assert body.count("timeline-day-header") == 4
    assert body.count("timeline-month-divider") >= 2  # July 2025 and June 2024
    assert "Unknown date" in body
    # Cards render through the shared partial (same markup as the grid).
    assert body.count('class="photo-card"') == 32
    # The undated group is last.
    assert body.rindex("timeline-day-label") < body.rindex("Unknown date")


def test_timeline_scrubber_marks_and_index(client, dated_library):
    body = client.get("/?view=timeline").data.decode()

    # Month index: newest first with cumulative offsets over the filtered library.
    payload = body.split('id="timeline-index">')[1].split("</script>")[0]
    months = json.loads(payload)
    assert months == [
        {"year": 2025, "month": 7, "count": 30, "offset": 0},
        {"year": 2024, "month": 6, "count": 1, "offset": 30},
    ]
    # Year marks link to the page where the year starts: 2024's first item is
    # offset 30, which lands on page 2 at the default page size of 25.
    assert "timeline-scrubber-year" in body
    assert "page=2" in body
    # The rail is a time axis: July 2025 → June 2024 spans 14 calendar months,
    # so the 2024 mark (top of that year = Dec 2024) sits at 7/14 of the rail.
    assert "top: 50.0%" in body


def test_timeline_scrubber_density_bars(client, dated_library):
    body = client.get("/?view=timeline").data.decode()

    # One bar per month with photos; length is the month's share of the busiest
    # month (July 2025: 30 photos = 100%; June 2024: 1 photo = the minimum tick).
    assert body.count("timeline-scrubber-bar") == 2
    assert "width: 100%" in body
    assert "width: 18%" in body  # 15 + 85 * 1/30, rounded


def test_timeline_view_is_persisted_as_preference(client, dated_library):
    client.get("/?view=timeline")

    assert "timeline-day-header" in client.get("/").data.decode()

    client.get("/?view=grid")

    assert "timeline-day-header" not in client.get("/").data.decode()


def test_invalid_view_falls_back_to_saved_preference(client, dated_library):
    body = client.get("/?view=bogus").data.decode()

    assert "timeline-day-header" not in body
    # An invalid value must not overwrite the stored preference.
    setting = db.session.query(ApplicationSettings).filter_by(name="library_view").first()
    assert setting is None


def test_view_rides_filter_form_and_pagination(client, dated_library):
    body = client.get("/?view=timeline").data.decode()

    assert '<input type="hidden" name="view" value="timeline">' in body
    assert "view=timeline" in body.split('class="pagination-container"')[1]


def test_scrubber_respects_filters(client, dated_library):
    body = client.get("/?view=timeline&year=2024").data.decode()

    payload = body.split('id="timeline-index">')[1].split("</script>")[0]
    assert json.loads(payload) == [{"year": 2024, "month": 6, "count": 1, "offset": 0}]
