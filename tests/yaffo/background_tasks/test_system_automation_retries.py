"""Exercise system tasks with real run recording and locks after durable work."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from yaffo.background_tasks import automation_runs
from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.background_tasks.tasks import (
    assign_location_name_automation as naming,
    auto_assign_faces_automation as faces,
    classify_labels_automation as labels,
    duplicate_scan as duplicates,
    export_photo_tag as export,
    geotag_from_neighbors_automation as geotag,
)
from yaffo.db import db
from yaffo.db.models import (
    Automation, ClassificationLabel, Face, Job, MediaItem, MediaLabel, Person, PersonFace,
    FACE_STATUS_ASSIGNED, FACE_STATUS_UNASSIGNED, JOB_STATUS_COMPLETED, JOB_STATUS_FAILED,
)

pytestmark = pytest.mark.unit
RunFixture = tuple[scoped_session[Session], list[float]]


@pytest.fixture
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RunFixture]:
    engine = create_engine(f"sqlite:///{tmp_path / 'runs.db'}")
    db.metadata.create_all(engine)
    factory = scoped_session(sessionmaker(bind=engine))
    for mod in (naming, faces, labels, duplicates, export, geotag):
        monkeypatch.setattr(mod, "SessionFactory", factory)
    sleeps: list[float] = []
    monkeypatch.setattr(automation_runs.time, "sleep", sleeps.append)
    yield factory, sleeps
    factory.remove()
    engine.dispose()


def _seed(factory: scoped_session[Session], slug: str, tmp_path: Path, **config: Any) -> Session:
    session = factory()
    session.add(Automation(id=1, slug=slug, name=slug, handler=slug, is_system=True,
                           enabled=True, config=config))
    for media_id in (1, 2, 3):
        path = tmp_path / f"{media_id}.jpg"
        path.write_bytes(b"photo")
        session.add(MediaItem(id=media_id, full_file_path=str(path)))
    session.commit()
    return session


def _job(factory: scoped_session[Session], count_key: str, expected: int) -> Job:
    jobs = factory().query(Job).all()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "retry-run"
    assert job.status == JOB_STATUS_COMPLETED
    assert json.loads(job.job_data)[count_key] == expected
    return job


def _lock_second_call(monkeypatch: pytest.MonkeyPatch, owner: Any, name: str, *, exhaust: bool = False) -> list[int]:
    original = getattr(owner, name)
    calls: list[int] = []

    def locked(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 2 or (exhaust and len(calls) > 2):
            raise OperationalError("UPDATE", {}, sqlite3.OperationalError("database is locked"))
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, locked)
    return calls


def _event_capture(monkeypatch: pytest.MonkeyPatch, mod: Any, factory: scoped_session[Session]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []

    def emit(event_type: str, payload: dict) -> None:
        # Subscribers must see committed data and a final run state.
        with sessionmaker(bind=factory().get_bind())() as other:
            assert other.get(Job, "retry-run").completed_at is not None
        events.append((event_type, payload))

    monkeypatch.setattr(mod, "emit_event", emit)
    return events


def test_face_assignment_counts_committed_chunks_without_duplicate_links(
    runs: RunFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, sleeps = runs
    session = _seed(factory, "auto_assign_faces", tmp_path)
    session.add(Person(id=7, name="Jason"))
    session.add_all(Face(id=i, media_item_id=i, status=FACE_STATUS_UNASSIGNED) for i in (1, 2, 3))
    session.commit()
    monkeypatch.setattr(faces, "_FLUSH_SIZE", 1)
    monkeypatch.setattr(faces, "get_similarity_bounds", lambda s: (0.4, 0.8))
    monkeypatch.setattr(faces.person_repository, "get_people_with_embeddings", lambda s: [7])
    monkeypatch.setattr(faces, "calculate_face_similarity", lambda face, people: {7: 1.0})
    _lock_second_call(monkeypatch, faces.person_repository, "bulk_link_faces_to_people")

    faces.auto_assign_faces_automation_task.fn(1, [1, 2, 3], task=SimpleNamespace(id="retry-run"))

    _job(factory, "faces", 3)
    assert sleeps == [15]
    assert factory().query(PersonFace).count() == 3
    assert {f.status for f in factory().query(Face)} == {FACE_STATUS_ASSIGNED}


@pytest.mark.parametrize("exhaust", [False, True])
@pytest.mark.parametrize("overwrite", [False, True])
def test_naming_retains_committed_ids_and_emits_once_after_retries(
    runs: RunFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exhaust: bool, overwrite: bool,
) -> None:
    factory, sleeps = runs
    session = _seed(factory, "assign_location_name", tmp_path,
                    reuse_nearby_enabled=False, reverse_geocode_enabled=True, overwrite_existing=overwrite)
    for item in session.query(MediaItem):
        item.latitude, item.longitude = 40.0, -90.0
    session.commit()
    monkeypatch.setattr(naming, "_FLUSH_SIZE", 1)
    geocodes: list[tuple[float, float]] = []

    def geocode(lat: float, lon: float) -> str:
        geocodes.append((lat, lon))
        return "Chicago"

    monkeypatch.setattr(naming, "_throttled_geocoder", lambda: geocode)
    _lock_second_call(monkeypatch, naming.media_repository, "bulk_set_location_names", exhaust=exhaust)
    events = _event_capture(monkeypatch, naming, factory)

    naming.assign_location_name_automation_task.fn(1, [1, 2, 3], task=SimpleNamespace(id="retry-run"))

    expected = [1] if exhaust else [1, 2, 3]
    assert events == [(naming.EVENT_MEDIA_MODIFIED, {"media_item_ids": expected})]
    assert sleeps == ([15, 60] if exhaust else [15])
    assert len(geocodes) == 4  # The failed target is retried; the committed target is skipped.
    assert [p.id for p in factory().query(MediaItem).filter_by(location_name="Chicago")] == expected
    if exhaust:
        assert factory().get(Job, "retry-run").status == JOB_STATUS_FAILED
    else:
        _job(factory, "named", 3)


@pytest.mark.parametrize("exhaust", [False, True])
def test_classification_retains_committed_ids_and_emits_once_after_retries(
    runs: RunFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exhaust: bool,
) -> None:
    factory, sleeps = runs
    session = _seed(factory, "classify_labels", tmp_path)
    session.add(ClassificationLabel(id=1, name="dog", enabled=True))
    session.commit()
    monkeypatch.setattr(labels, "_FLUSH_SIZE", 1)
    monkeypatch.setattr(labels, "get_clip_threshold", lambda value: 0.5)
    monkeypatch.setattr(labels, "embed_texts", lambda prompts: np.array([[1.0, 0.0]]))
    monkeypatch.setattr(labels, "image_from_path", lambda path: path)
    monkeypatch.setattr(labels, "image_to_numpy", lambda image: image)
    monkeypatch.setattr(labels, "embed_image", lambda image: np.array([1.0, 0.0]))
    _lock_second_call(monkeypatch, labels.classification_repository, "bulk_replace_media_labels", exhaust=exhaust)
    events = _event_capture(monkeypatch, labels, factory)

    labels.classify_labels_automation_task.fn(1, [1, 2, 3], task=SimpleNamespace(id="retry-run"))

    expected = [1] if exhaust else [1, 2, 3]
    assert events == [(labels.EVENT_MEDIA_LABELED, {"media_item_ids": expected})]
    assert sleeps == ([15, 60] if exhaust else [15])
    assert [row.media_item_id for row in factory().query(MediaLabel)] == expected
    if exhaust:
        assert factory().get(Job, "retry-run").status == JOB_STATUS_FAILED
    else:
        _job(factory, "labeled", 3)


def test_geotagging_counts_commits_and_keeps_original_sources_on_retry(
    runs: RunFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, sleeps = runs
    session = _seed(factory, "geotag_from_neighbors", tmp_path, max_minutes=5)
    for media_id, minute in ((1, 4), (2, 5), (3, 8)):
        session.get(MediaItem, media_id).date_taken = f"2022-07-03T10:0{minute}:00"
    session.add(MediaItem(id=4, full_file_path="/source.jpg", date_taken="2022-07-03T10:00:00",
                          latitude=40.0, longitude=-90.0, location_name="Chicago"))
    session.commit()
    monkeypatch.setattr(geotag, "_FLUSH_SIZE", 1)
    # The second coordinate commit fails, after the first target became a GPS row.
    original_commit = session.commit
    commits = []

    def commit() -> None:
        if any(isinstance(item, MediaItem) for item in session.dirty):
            commits.append(1)
            if len(commits) == 2:
                raise sqlite3.OperationalError("database is locked")
        original_commit()

    monkeypatch.setattr(session, "commit", commit)
    original_sources = geotag.media_repository.get_gps_timestamps
    loads = []

    def sources(s: Session) -> list[tuple[str, float, float, str | None]]:
        loads.append(1)
        return original_sources(s)

    monkeypatch.setattr(geotag.media_repository, "get_gps_timestamps", sources)
    geotag.geotag_from_neighbors_automation_task.fn(1, [1, 2, 3], task=SimpleNamespace(id="retry-run"))

    _job(factory, "geotagged", 2)
    assert sleeps == [15]
    assert loads == [1]
    assert factory().get(MediaItem, 1).latitude == 40.0
    assert factory().get(MediaItem, 2).location_name == "Chicago"
    assert factory().get(MediaItem, 3).latitude is None  # Eight minutes from the original source.


@pytest.mark.parametrize("lock_at", ["progress", "suppression"])
def test_tag_export_retries_without_rewriting_files_and_preserves_suppression(
    runs: RunFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_at: str,
) -> None:
    factory, sleeps = runs
    _seed(factory, "export_photo_tag", tmp_path, export_location_tag_enabled=True)
    writes = []
    suppressions = []

    def write(photo_path: Path, **kwargs: Any) -> tuple[bool, None]:
        writes.append(photo_path)
        return True, None

    def suppress(photo_path: Path) -> None:
        suppressions.append(photo_path)
        if lock_at == "suppression" and len(suppressions) == 1:
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(export, "write_photo_metadata", write)
    monkeypatch.setattr(export, "record_self_write", suppress)
    if lock_at == "progress":
        original = ProgressReporter.progress_update
        failed = []

        def progress(self: ProgressReporter, task_count: int, completed_count: int, cancelled: int, error_count: int) -> None:
            if completed_count == 1 and not failed:
                failed.append(1)
                raise sqlite3.OperationalError("database is locked")
            original(self, task_count, completed_count, cancelled, error_count)

        monkeypatch.setattr(ProgressReporter, "progress_update", progress)

    export.export_photo_tag_task.fn(1, [1, 2, 3], task=SimpleNamespace(id="retry-run"))

    _job(factory, "written", 3)
    assert sleeps == [15]
    assert writes == [tmp_path / f"{i}.jpg" for i in (1, 2, 3)]
    assert suppressions == ([tmp_path / "1.jpg"] if lock_at == "suppression" else []) + writes


def test_empty_duplicate_scan_records_once_without_running_work(
    runs: RunFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, sleeps = runs
    _seed(factory, "duplicate_scan", tmp_path)
    scans = []
    monkeypatch.setattr(duplicates, "find_duplicates_task", lambda *args: scans.append(args))
    context = SimpleNamespace(id="retry-run")

    duplicates.duplicate_scan_task.fn(1, [], task=context)
    duplicates.duplicate_scan_task.fn(1, [], task=context)

    job = factory().get(Job, "retry-run")
    assert job.status == JOB_STATUS_COMPLETED
    assert json.loads(job.job_data)["outcome"] == "no_media"
    assert factory().query(Job).count() == 1
    assert scans == []
    assert sleeps == []


@pytest.mark.parametrize("lock_error", [
    sqlite3.OperationalError("database is locked"),
    OperationalError("UPDATE", {}, sqlite3.OperationalError("database is locked")),
])
def test_progress_reporter_propagates_item_lock_errors(lock_error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    reporter = ProgressReporter(None, "run")
    monkeypatch.setattr(reporter, "progress_update", lambda *args: None)

    def process(item: int) -> None:
        raise lock_error

    with pytest.raises(type(lock_error), match="database is locked"):
        reporter.run_with_progress([1], process)


def test_progress_reporter_keeps_counting_other_item_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    reporter = ProgressReporter(None, "run")
    updates: list[tuple[int, int, int, int]] = []
    monkeypatch.setattr(reporter, "progress_update", lambda *args: updates.append(args))

    def process(item: int) -> None:
        if item == 1:
            raise ValueError("invalid photo")

    assert reporter.run_with_progress([1, 2], process)
    assert updates[-1] == (2, 1, 0, 1)
