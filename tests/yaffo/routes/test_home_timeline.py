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
    # offset 30, which lands on page 2 at the default page size of 25 — plus the
    # month anchor, since that item sits mid-page rather than at its top.
    assert "timeline-scrubber-year" in body
    assert 'data-year="2025"' in body
    assert "timeline-scrubber-marker" in body
    assert "page=2#month-2024-06" in body
    # The anchor's target renders on that month's divider.
    assert 'id="month-2025-07"' in body
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


def test_view_rides_filter_form(client, dated_library):
    body = client.get("/?view=timeline").data.decode()

    assert '<input type="hidden" name="view" value="timeline">' in body


class TestInfiniteScroll:
    """The timeline streams batches through an htmx sentinel instead of
    paginating; the grid keeps its pagination bar."""

    def test_timeline_has_sentinel_not_pagination(self, client, dated_library):
        body = client.get("/?view=timeline").data.decode()

        assert "pagination-container" not in body
        sentinel = body.split('class="timeline-sentinel"')[1].split(">")[0]
        assert "fragment=sections" in sentinel
        assert "page=2" in sentinel
        assert "view=timeline" in sentinel

    def test_grid_keeps_pagination(self, client, dated_library):
        body = client.get("/?view=grid").data.decode()

        assert "pagination-container" in body
        assert "timeline-sentinel" not in body

    def test_last_batch_has_no_sentinel(self, client, dated_library):
        body = client.get("/?view=timeline&page=2").data.decode()

        assert "timeline-sentinel" not in body

    def test_fragment_returns_sections_only(self, client, dated_library):
        # 32 items at page size 25: the batch boundary splits July 3 (items
        # 21-25 on page 1, 26-30 on page 2).
        body = client.get("/?view=timeline&page=2&fragment=sections").data.decode()

        assert "<html" not in body and "timeline-scrubber" not in body
        # The split day continues: headerless, marked for the client-side merge,
        # and no repeated "July 2025" divider.
        first_section = body.split("<section", 2)[1]
        assert "is-continuation" in first_section
        assert "timeline-day-header" not in first_section
        assert "July 2025" not in body
        assert "June 2024" in body  # the month divider that genuinely starts here
        assert "timeline-sentinel" not in body  # nothing beyond this batch

    def test_full_page_jump_keeps_first_day_header(self, client, dated_library):
        # A scrubber jump lands on page 2 as a full render: the landing must
        # show its day header even though the day started on page 1.
        body = client.get("/?view=timeline&page=2").data.decode()

        first_section = body.split("<section", 2)[1]
        assert "is-continuation" not in first_section
        assert "timeline-day-header" in first_section

    def test_undated_tail_continues_across_batches(self, client, app):
        # A long undated tail spans several batches; each batch after the first
        # must continue the "Unknown date" section, not open a new header.
        with app.app_context():
            items = [MediaItem(full_file_path=f"/media/d_{i}.jpg",
                               date_taken=f"2025-07-20T10:{i:02d}:00", year=2025, month=7)
                     for i in range(20)]
            items += [MediaItem(full_file_path=f"/media/u_{i}.jpg") for i in range(40)]
            db.session.add_all(items)
            db.session.commit()

        # Page 2 (items 25-49) is entirely inside the undated tail.
        body = client.get("/?view=timeline&page=2&fragment=sections").data.decode()

        first_section = body.split("<section", 2)[1]
        assert 'data-date="unknown"' in first_section
        assert "is-continuation" in first_section
        # No day header anywhere in the batch — it's all one continued section.
        assert "timeline-day-header" not in body

    def test_filters_ride_the_sentinel(self, client, dated_library):
        body = client.get("/?view=timeline&year=2025").data.decode()

        assert "timeline-sentinel" in body
        sentinel = body.split('class="timeline-sentinel"')[1].split(">")[0]
        assert "year=2025" in sentinel


def test_scrubber_indexes_by_date_taken_not_columns(client, app):
    # The index follows date_taken (what the gallery sorts by), so a row whose
    # year/month columns are missing or wrong still lands in its true month.
    with app.app_context():
        db.session.add(MediaItem(full_file_path="/media/legacy.jpg",
                                 date_taken="2020-05-01T00:00:00", year=2020, month=None))
        db.session.commit()

    response = client.get("/?view=timeline")

    assert response.status_code == 200
    assert "#month-2020-05" in response.data.decode()


def test_scrubber_excludes_dateless_rows_with_year(client, dated_library, app):
    # Rows with a year column but no date_taken sort into the undated tail, so
    # counting them in their year slot would inflate every older month's offset
    # and land jumps on the wrong page. They must not appear in the index.
    with app.app_context():
        for i in range(5):
            db.session.add(MediaItem(full_file_path=f"/media/phantom_{i}.jpg",
                                     date_taken=None, year=2026, month=1))
        db.session.commit()

    body = client.get("/?view=timeline").data.decode()

    payload = body.split('id="timeline-index">')[1].split("</script>")[0]
    assert json.loads(payload) == [
        {"year": 2025, "month": 7, "count": 30, "offset": 0},
        {"year": 2024, "month": 6, "count": 1, "offset": 30},
    ]


def test_scrubber_respects_filters(client, dated_library):
    body = client.get("/?view=timeline&year=2024").data.decode()

    payload = body.split('id="timeline-index">')[1].split("</script>")[0]
    assert json.loads(payload) == [{"year": 2024, "month": 6, "count": 1, "offset": 0}]
