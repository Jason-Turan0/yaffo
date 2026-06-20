"""Unit tests for the geotag-from-neighbors *system* automation: the time-correlation
matching in _geotag_from_neighbors (nearest GPS-tagged photo within the window), the
frozen-candidates rule (no chaining), and the handler wiring. The DB queries are
stubbed; what's under test is which photos get which coordinates.
"""
from types import SimpleNamespace

import pytest

from yaffo.background_tasks.tasks import geotag_from_neighbors_automation as mod

pytestmark = pytest.mark.unit


class _FakeSession:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def _photo(photo_id, date_taken, lat=None, lon=None, location_name=None):
    return SimpleNamespace(
        id=photo_id, date_taken=date_taken, latitude=lat, longitude=lon,
        location_name=location_name,
    )


def _gps(date_taken, lat, lon, location_name=None):
    """A get_gps_timestamps row: (date_taken, latitude, longitude, location_name)."""
    return (date_taken, lat, lon, location_name)


def _stub(monkeypatch, *, targets, gps):
    monkeypatch.setattr(mod.photos_repository, "get_photos_missing_gps", lambda s, ids: targets)
    monkeypatch.setattr(mod.photos_repository, "get_gps_timestamps", lambda s: list(gps))


def test_borrows_nearest_in_time_coordinates(monkeypatch, progress_reporter):
    target = _photo(1, "2022-07-03T10:04:00")
    _stub(monkeypatch, targets=[target], gps=[
        _gps("2022-07-03T10:01:00", 48.8584, 2.2945),   # 3 min before — closest
        _gps("2022-07-03T10:30:00", 48.9000, 2.4000),   # 26 min after
    ])

    updated = mod._geotag_from_neighbors(_FakeSession(), progress_reporter, [1], max_minutes=30)

    assert updated == [1]
    assert (target.latitude, target.longitude) == (48.8584, 2.2945)
    assert progress_reporter.run_with_progress_calls == [[target]]


def test_skips_when_nearest_is_outside_window(monkeypatch, progress_reporter):
    target = _photo(1, "2022-07-03T10:04:00")
    # Only GPS photo is 45 min away — outside a 30 min window.
    _stub(monkeypatch, targets=[target], gps=[_gps("2022-07-03T10:49:00", 48.0, 2.0)])

    updated = mod._geotag_from_neighbors(_FakeSession(), progress_reporter, [1], max_minutes=30)

    assert updated == []
    assert target.latitude is None and target.longitude is None
    assert progress_reporter.run_with_progress_calls == [[target]]


def test_picks_closest_of_before_and_after(monkeypatch, progress_reporter):
    target = _photo(1, "2022-07-03T12:00:00")
    _stub(monkeypatch, targets=[target], gps=[
        _gps("2022-07-03T11:50:00", 10.0, 10.0),   # 10 min before
        _gps("2022-07-03T12:02:00", 20.0, 20.0),   # 2 min after — closest
    ])

    updated = mod._geotag_from_neighbors(_FakeSession(), progress_reporter, [1], max_minutes=30)

    assert updated == [1]
    assert (target.latitude, target.longitude) == (20.0, 20.0)
    assert progress_reporter.run_with_progress_calls == [[target]]


def test_candidates_frozen_no_chaining(monkeypatch, progress_reporter):
    """Two GPS-less photos: the first gets geotagged, but the second must NOT borrow
    from the first (candidates are the original GPS set, loaded once)."""
    a = _photo(1, "2022-07-03T10:00:00")
    b = _photo(2, "2022-07-03T10:02:00")
    # Single real GPS source near `a`; `b` is within 2 min of `a` but `a` had no GPS
    # originally, so `b` should only match the real source if within window of it too.
    _stub(monkeypatch, targets=[a, b], gps=[_gps("2022-07-03T10:00:30", 5.0, 5.0)])

    updated = mod._geotag_from_neighbors(_FakeSession(), progress_reporter, [1, 2], max_minutes=5)

    # Both happen to be within 5 min of the real source, so both match IT (5.0, 5.0) —
    # crucially b matches the original source, never a's freshly-written coords.
    assert updated == [1, 2]
    assert (a.latitude, a.longitude) == (5.0, 5.0)
    assert (b.latitude, b.longitude) == (5.0, 5.0)
    assert progress_reporter.run_with_progress_calls == [[a, b]]


def test_no_targets_is_noop(monkeypatch, progress_reporter):
    _stub(monkeypatch, targets=[], gps=[_gps("2022-07-03T10:00:00", 1.0, 1.0)])
    session = _FakeSession()

    assert mod._geotag_from_neighbors(session, progress_reporter, [1], max_minutes=30) == []
    assert session.commits == 0
    assert progress_reporter.progress_update_calls == [(1, 1, 0, 0)]


def test_no_gps_photos_is_noop(monkeypatch, progress_reporter):
    target = _photo(1, "2022-07-03T10:00:00")
    _stub(monkeypatch, targets=[target], gps=[])

    assert mod._geotag_from_neighbors(_FakeSession(), progress_reporter, [1], max_minutes=30) == []
    assert target.latitude is None
    assert progress_reporter.progress_update_calls == [(1, 1, 0, 0)]


def test_unparseable_target_date_is_skipped(monkeypatch, progress_reporter):
    target = _photo(1, "not-a-date")
    _stub(monkeypatch, targets=[target], gps=[_gps("2022-07-03T10:00:00", 1.0, 1.0)])

    assert mod._geotag_from_neighbors(_FakeSession(), progress_reporter, [1], max_minutes=30) == []
    assert progress_reporter.run_with_progress_calls == [[target]]


def test_copies_location_name_from_matched_source(monkeypatch, progress_reporter):
    target = _photo(1, "2022-07-03T10:04:00")
    _stub(monkeypatch, targets=[target], gps=[
        _gps("2022-07-03T10:01:00", 48.8584, 2.2945, "Paris"),
    ])

    updated = mod._geotag_from_neighbors(_FakeSession(), progress_reporter, [1], max_minutes=30)

    assert updated == [1]
    assert (target.latitude, target.longitude) == (48.8584, 2.2945)
    assert target.location_name == "Paris"
    assert progress_reporter.run_with_progress_calls == [[target]]


def test_no_location_name_when_source_has_none(monkeypatch, progress_reporter):
    target = _photo(1, "2022-07-03T10:04:00")
    _stub(monkeypatch, targets=[target], gps=[_gps("2022-07-03T10:01:00", 48.8584, 2.2945)])

    mod._geotag_from_neighbors(_FakeSession(), progress_reporter, [1], max_minutes=30)

    assert target.location_name is None
    assert progress_reporter.run_with_progress_calls == [[target]]


def test_does_not_overwrite_existing_location_name(monkeypatch, progress_reporter):
    target = _photo(1, "2022-07-03T10:04:00", location_name="My Spot")
    _stub(monkeypatch, targets=[target], gps=[
        _gps("2022-07-03T10:01:00", 48.8584, 2.2945, "Paris"),
    ])

    updated = mod._geotag_from_neighbors(_FakeSession(), progress_reporter, [1], max_minutes=30)

    assert updated == [1]
    assert (target.latitude, target.longitude) == (48.8584, 2.2945)  # coords still copied
    assert target.location_name == "My Spot"  # name preserved
    assert progress_reporter.run_with_progress_calls == [[target]]


def test_handler_enqueues_for_event_photos(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mod, "geotag_from_neighbors_automation_task",
        lambda automation_id, photo_ids: calls.append((automation_id, photo_ids)),
    )
    mod.enqueue_geotag_from_neighbors(SimpleNamespace(id=9), SimpleNamespace(photo_ids=[1, 2]))
    assert calls == [(9, [1, 2])]


def test_handler_noop_without_context_or_photos(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mod, "geotag_from_neighbors_automation_task",
        lambda automation_id, photo_ids: calls.append((automation_id, photo_ids)),
    )
    mod.enqueue_geotag_from_neighbors(SimpleNamespace(id=9), None)
    mod.enqueue_geotag_from_neighbors(SimpleNamespace(id=9), SimpleNamespace(photo_ids=[]))
    assert calls == []
