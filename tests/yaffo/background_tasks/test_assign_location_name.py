"""Unit tests for the assign-location-name *system* automation: the strategy
ordering in _assign_location_names (reuse-nearby then geocode), the radius cutoff,
the overwrite gate, within-batch reuse, and the handler wiring. The DB query and
the Nominatim lookup are stubbed; what's under test is which name each photo gets.
"""
from types import SimpleNamespace

import pytest

from yaffo.background_tasks.tasks import assign_location_name_automation as mod

pytestmark = pytest.mark.unit


class _FakeSession:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def _photo(media_item_id, lat, lon, location_name=None):
    return SimpleNamespace(id=media_item_id, latitude=lat, longitude=lon, location_name=location_name)


def _stub_repo(monkeypatch, *, batch, named):
    monkeypatch.setattr(mod.media_repository, "get_media_items_with_coords", lambda s, ids: batch)
    monkeypatch.setattr(mod.media_repository, "get_named_coordinates", lambda s: list(named))


def _counting_geocoder(name="Geocoded City"):
    calls = []

    def geocode(lat, lon):
        calls.append((lat, lon))
        return name

    geocode.calls = calls
    return geocode


def test_reuses_nearby_name_without_geocoding(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6000, -90.2000)
    _stub_repo(monkeypatch, batch=[media_item], named=[(38.6005, -90.2005, "Tower Grove")])
    geocode = _counting_geocoder()

    updated = mod._assign_location_names(
        _FakeSession(), progress_reporter, [1], reuse_enabled=True, radius_km=1, overwrite=False, geocode=geocode
    )

    assert updated == [1]
    assert media_item.location_name == "Tower Grove"
    assert geocode.calls == []
    assert progress_reporter.run_with_progress_calls == [[media_item]]


def test_geocodes_when_no_named_photo_in_radius(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6000, -90.2000)
    # Named photo is ~1.2 km away — outside a 1 km radius.
    _stub_repo(monkeypatch, batch=[media_item], named=[(38.6100, -90.2050, "Far Place")])
    geocode = _counting_geocoder("Nominatim Name")

    updated = mod._assign_location_names(
        _FakeSession(), progress_reporter, [1], reuse_enabled=True, radius_km=1, overwrite=False, geocode=geocode
    )

    assert updated == [1]
    assert media_item.location_name == "Nominatim Name"
    assert len(geocode.calls) == 1
    assert progress_reporter.run_with_progress_calls == [[media_item]]


def test_skips_already_named_unless_overwrite(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6, -90.2, location_name="My Name")
    _stub_repo(monkeypatch, batch=[media_item], named=[])
    geocode = _counting_geocoder()

    updated = mod._assign_location_names(
        _FakeSession(), progress_reporter, [1], reuse_enabled=True, radius_km=1, overwrite=False, geocode=geocode
    )

    assert updated == []
    assert media_item.location_name == "My Name"
    assert geocode.calls == []
    assert progress_reporter.run_with_progress_calls == [[media_item]]


def test_overwrite_renames_already_named(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6, -90.2, location_name="Old")
    _stub_repo(monkeypatch, batch=[media_item], named=[])
    geocode = _counting_geocoder("New")

    updated = mod._assign_location_names(
        _FakeSession(), progress_reporter, [1], reuse_enabled=True, radius_km=1, overwrite=True, geocode=geocode
    )

    assert updated == [1]
    assert media_item.location_name == "New"
    assert progress_reporter.run_with_progress_calls == [[media_item]]


def test_within_batch_reuse_geocodes_once_per_cluster(monkeypatch, progress_reporter):
    # Two unnamed photos ~55 m apart: the first geocodes, the second reuses it.
    a = _photo(1, 38.6000, -90.2000)
    b = _photo(2, 38.6005, -90.2000)
    _stub_repo(monkeypatch, batch=[a, b], named=[])
    geocode = _counting_geocoder("Cluster City")

    updated = mod._assign_location_names(
        _FakeSession(), progress_reporter, [1, 2], reuse_enabled=True, radius_km=1, overwrite=False, geocode=geocode
    )

    assert updated == [1, 2]
    assert a.location_name == b.location_name == "Cluster City"
    assert len(geocode.calls) == 1
    assert progress_reporter.run_with_progress_calls == [[a, b]]


def test_no_geocoder_leaves_unmatched_photo_unnamed(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6, -90.2)
    _stub_repo(monkeypatch, batch=[media_item], named=[])

    updated = mod._assign_location_names(
        _FakeSession(), progress_reporter, [1], reuse_enabled=True, radius_km=1, overwrite=False, geocode=None
    )

    assert updated == []
    assert media_item.location_name is None
    assert progress_reporter.run_with_progress_calls == [[media_item]]


def test_reuse_disabled_goes_straight_to_geocode(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6000, -90.2000)
    # A name sits right on top, but reuse is off, so it must geocode instead.
    named_calls = []
    monkeypatch.setattr(mod.media_repository, "get_media_items_with_coords", lambda s, ids: [media_item])
    monkeypatch.setattr(
        mod.media_repository, "get_named_coordinates",
        lambda s: named_calls.append(True) or [(38.6, -90.2, "Right Here")],
    )
    geocode = _counting_geocoder("Geo")

    updated = mod._assign_location_names(
        _FakeSession(), progress_reporter, [1], reuse_enabled=False, radius_km=1, overwrite=False, geocode=geocode
    )

    assert updated == [1]
    assert media_item.location_name == "Geo"
    assert named_calls == []  # candidates not even loaded when reuse is off
    assert progress_reporter.run_with_progress_calls == [[media_item]]


def test_no_photos_with_coords_is_noop(monkeypatch, progress_reporter):
    _stub_repo(monkeypatch, batch=[], named=[])
    session = _FakeSession()

    updated = mod._assign_location_names(
        session, progress_reporter, [1, 2], reuse_enabled=True, radius_km=1, overwrite=False, geocode=_counting_geocoder()
    )

    assert updated == []
    assert session.commits == 0
    # No photos with coordinates — the function returns before reporting any progress.
    assert progress_reporter.run_with_progress_calls == []


def test_handler_enqueues_for_event_photos(monkeypatch):
    calls: list[tuple[int, list[int], list[int]]] = []
    monkeypatch.setattr(
        mod, "assign_location_name_automation_task",
        lambda automation_id, media_item_ids, origin: calls.append((automation_id, media_item_ids, origin)),
    )
    mod.enqueue_assign_location_name(
        SimpleNamespace(id=7),
        SimpleNamespace(media_item_ids=[11, 12], origin_automation_ids=[9]),
    )
    assert calls == [(7, [11, 12], [9])]


def test_handler_noop_without_context_or_photos(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        mod, "assign_location_name_automation_task",
        lambda automation_id, media_item_ids, origin: calls.append((automation_id, media_item_ids, origin)),
    )
    mod.enqueue_assign_location_name(SimpleNamespace(id=7), None)
    mod.enqueue_assign_location_name(
        SimpleNamespace(id=7), SimpleNamespace(media_item_ids=[], origin_automation_ids=[])
    )
    assert calls == []
