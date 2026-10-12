"""Unit tests for the assign-location-name *system* automation: the strategy
ordering in _assign_location_names (reuse-nearby then geocode), the radius cutoff,
the overwrite gate, within-batch reuse, and the handler wiring. The DB reads and
writes and the Nominatim lookup are stubbed; what's under test is which name each
photo gets written. One test runs against a real database to prove the write lock
isn't held between writes.
"""
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.background_tasks.tasks import assign_location_name_automation as mod
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_RUNNING, MediaItem

pytestmark = pytest.mark.unit


def _photo(media_item_id, lat, lon, location_name=None):
    return SimpleNamespace(id=media_item_id, latitude=lat, longitude=lon, location_name=location_name)


def _stub_repo(monkeypatch, *, batch, named):
    """Stub the reads; returns the {id: name} writes, merged across flushes."""
    written: dict[int, str] = {}
    monkeypatch.setattr(mod.media_repository, "get_coords_for_media_items", lambda s, ids: batch)
    monkeypatch.setattr(mod.media_repository, "get_named_coordinates", lambda s: list(named))
    monkeypatch.setattr(mod.media_repository, "bulk_set_location_names", lambda s, names: written.update(names))
    return written


def _counting_geocoder(name="Geocoded City"):
    calls = []

    def geocode(lat, lon):
        calls.append((lat, lon))
        return name

    geocode.calls = calls
    return geocode


def test_reuses_nearby_name_without_geocoding(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6000, -90.2000)
    written = _stub_repo(monkeypatch, batch=[media_item], named=[(38.6005, -90.2005, "Tower Grove")])
    geocode = _counting_geocoder()

    updated = mod._assign_location_names(
        None, progress_reporter, [1], reuse_enabled=True, radius_km=1, overwrite=False, geocode=geocode
    )

    assert updated == [1]
    assert written == {1: "Tower Grove"}
    assert geocode.calls == []
    assert progress_reporter.run_with_progress_calls == [[media_item]]


def test_geocodes_when_no_named_photo_in_radius(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6000, -90.2000)
    # Named photo is ~1.2 km away — outside a 1 km radius.
    written = _stub_repo(monkeypatch, batch=[media_item], named=[(38.6100, -90.2050, "Far Place")])
    geocode = _counting_geocoder("Nominatim Name")

    updated = mod._assign_location_names(
        None, progress_reporter, [1], reuse_enabled=True, radius_km=1, overwrite=False, geocode=geocode
    )

    assert updated == [1]
    assert written == {1: "Nominatim Name"}
    assert len(geocode.calls) == 1


def test_skips_already_named_unless_overwrite(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6, -90.2, location_name="My Name")
    written = _stub_repo(monkeypatch, batch=[media_item], named=[])
    geocode = _counting_geocoder()

    updated = mod._assign_location_names(
        None, progress_reporter, [1], reuse_enabled=True, radius_km=1, overwrite=False, geocode=geocode
    )

    assert updated == []
    assert written == {}
    assert geocode.calls == []
    assert progress_reporter.run_with_progress_calls == [[media_item]]


def test_overwrite_renames_already_named(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6, -90.2, location_name="Old")
    written = _stub_repo(monkeypatch, batch=[media_item], named=[])

    updated = mod._assign_location_names(
        None, progress_reporter, [1], reuse_enabled=True, radius_km=1, overwrite=True,
        geocode=_counting_geocoder("New"),
    )

    assert updated == [1]
    assert written == {1: "New"}


def test_overwrite_same_name_is_noop(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6, -90.2, location_name="Same")
    written = _stub_repo(monkeypatch, batch=[media_item], named=[])

    updated = mod._assign_location_names(
        None, progress_reporter, [1], reuse_enabled=False, radius_km=1,
        overwrite=True, geocode=_counting_geocoder("Same"),
    )

    assert updated == []
    assert written == {}


def test_within_batch_reuse_geocodes_once_per_cluster(monkeypatch, progress_reporter):
    # Two unnamed photos ~55 m apart: the first geocodes, the second reuses it.
    a = _photo(1, 38.6000, -90.2000)
    b = _photo(2, 38.6005, -90.2000)
    written = _stub_repo(monkeypatch, batch=[a, b], named=[])
    geocode = _counting_geocoder("Cluster City")

    updated = mod._assign_location_names(
        None, progress_reporter, [1, 2], reuse_enabled=True, radius_km=1, overwrite=False, geocode=geocode
    )

    assert updated == [1, 2]
    assert written == {1: "Cluster City", 2: "Cluster City"}
    assert len(geocode.calls) == 1
    assert progress_reporter.run_with_progress_calls == [[a, b]]


def test_no_geocoder_leaves_unmatched_photo_unnamed(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6, -90.2)
    written = _stub_repo(monkeypatch, batch=[media_item], named=[])

    updated = mod._assign_location_names(
        None, progress_reporter, [1], reuse_enabled=True, radius_km=1, overwrite=False, geocode=None
    )

    assert updated == []
    assert written == {}


def test_reuse_disabled_goes_straight_to_geocode(monkeypatch, progress_reporter):
    media_item = _photo(1, 38.6000, -90.2000)
    written = _stub_repo(monkeypatch, batch=[media_item], named=[])
    # A name sits right on top, but reuse is off, so it must geocode instead.
    named_calls = []
    monkeypatch.setattr(
        mod.media_repository, "get_named_coordinates",
        lambda s: named_calls.append(True) or [(38.6, -90.2, "Right Here")],
    )

    updated = mod._assign_location_names(
        None, progress_reporter, [1], reuse_enabled=False, radius_km=1, overwrite=False,
        geocode=_counting_geocoder("Geo"),
    )

    assert updated == [1]
    assert written == {1: "Geo"}
    assert named_calls == []  # candidates not even loaded when reuse is off


def test_no_photos_with_coords_is_noop(monkeypatch, progress_reporter):
    written = _stub_repo(monkeypatch, batch=[], named=[])

    updated = mod._assign_location_names(
        None, progress_reporter, [1, 2], reuse_enabled=True, radius_km=1, overwrite=False,
        geocode=_counting_geocoder(),
    )

    assert updated == []
    assert written == {}
    # No photos with coordinates — the function returns before reporting any progress.
    assert progress_reporter.run_with_progress_calls == []


def test_naming_holds_no_write_lock_between_writes(tmp_path, monkeypatch):
    """The progress reporter queries the DB mid-run (cancel checks, progress commits).
    When names were set on ORM photos, those queries autoflushed the UPDATE and the
    write lock was held across the matching and geocoding until the next commit,
    starving other writers into "database is locked". Now another writer can take
    the lock at any point between writes."""
    path = tmp_path / "app.db"
    engine = create_engine(f"sqlite:///{path}")
    db.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([MediaItem(id=i, full_file_path=f"/p/{i}.jpg", latitude=38.6, longitude=-90.2)
                     for i in range(1, 301)])
    session.add(Job(id="run", name="assign_location_name", status=JOB_STATUS_RUNNING, task_count=300))
    session.commit()

    lock_free_at_each_geocode = []

    def geocode(lat, lon):
        other = sqlite3.connect(path, timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
            lock_free_at_each_geocode.append(True)
        except sqlite3.OperationalError:
            lock_free_at_each_geocode.append(False)
        finally:
            other.close()
        return f"Place {len(lock_free_at_each_geocode)}"

    # 300 photos: progress commits every 15, cancel checks every 10, so a check lands
    # between commits with renamed photos pending -- the window the old code locked.
    try:
        updated = mod._assign_location_names(
            session, ProgressReporter(session, "run"), list(range(1, 301)),
            reuse_enabled=False, radius_km=1, overwrite=False, geocode=geocode,
        )
        names = dict(session.query(MediaItem.id, MediaItem.location_name))
    finally:
        session.close()
        engine.dispose()

    assert len(updated) == 300 and all(lock_free_at_each_geocode)
    assert names[1] == "Place 1" and names[300] == "Place 300"


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


def test_handler_enqueues_even_without_indexed_photos(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        mod, "assign_location_name_automation_task",
        lambda automation_id, media_item_ids, origin: calls.append((automation_id, media_item_ids, origin)),
    )
    mod.enqueue_assign_location_name(SimpleNamespace(id=7), None)
    mod.enqueue_assign_location_name(
        SimpleNamespace(id=7), SimpleNamespace(media_item_ids=[], origin_automation_ids=[])
    )
    assert calls == [(7, [], []), (7, [], [])]
