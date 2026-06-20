"""Unit tests for the mutating move_photo host action and data_query row
enrichment, with the filesystem + repositories monkeypatched."""
from pathlib import Path

import pytest

from yaffo.background_tasks.automation_sandbox import automation_actions, media_dirs
from yaffo.db.repositories.media_dir_repository import MediaDir

pytestmark = pytest.mark.unit


def _patch(monkeypatch, target, fn):
    monkeypatch.setattr(target, fn)


def test_move_photo_moves_within_media_dir(monkeypatch, tmp_path):
    media_root = tmp_path / "lib"
    src = media_root / "IMG.jpg"
    src.parent.mkdir(parents=True)
    src.write_text("x")
    saved = {}

    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.automation_actions."
           "photos_repository.get_photo_path", lambda s, pid: str(src))
    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.automation_actions."
           "photos_repository.update_photo_path", lambda s, pid, path: saved.update(path=path))
    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.automation_actions.media_dir_by_id",
           lambda s, mid: MediaDir(id=mid, path=media_root))

    automation_actions.move_photo(object(), 1, "GUID", "2024/06")

    moved = media_root / "2024" / "06" / "IMG.jpg"
    assert moved.exists() and not src.exists()
    assert saved["path"] == str(moved)


def test_move_photo_refuses_escape(monkeypatch, tmp_path):
    media_root = tmp_path / "lib"
    src = media_root / "IMG.jpg"
    src.parent.mkdir(parents=True)
    src.write_text("x")
    saved = {}

    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.automation_actions."
           "photos_repository.get_photo_path", lambda s, pid: str(src))
    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.automation_actions."
           "photos_repository.update_photo_path", lambda s, pid, path: saved.update(path=path))
    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.automation_actions.media_dir_by_id",
           lambda s, mid: MediaDir(id=mid, path=media_root))

    automation_actions.move_photo(object(), 1, "GUID", "../../etc")

    assert src.exists() and not saved  # escaping target refused, nothing moved


_ACTIONS = "yaffo.background_tasks.automation_sandbox.automation_actions."


def test_tag_photos_batches_and_drops_incomplete(monkeypatch):
    captured = {}
    _patch(monkeypatch, _ACTIONS + "photos_repository.add_tags",
           lambda s, items: captured.update(items=items))

    automation_actions.tag_photos(object(), [
        {"photo_id": 1, "name": "beach"},
        {"photo_id": 2, "name": "rating", "value": 5},
        {"name": "no_photo"},   # dropped: no photo_id
        {"photo_id": 3},        # dropped: no name
    ])

    assert captured["items"] == [(1, "beach", None), (2, "rating", 5)]


def test_assign_faces_filters_unknown_persons(monkeypatch):
    captured = {}
    _patch(monkeypatch, _ACTIONS + "person_repository.existing_person_ids", lambda s, ids: {7})
    _patch(monkeypatch, _ACTIONS + "person_repository.bulk_link_faces_to_people",
           lambda s, links: captured.update(links=links))

    automation_actions.assign_faces(object(), [
        {"face_id": 10, "person_id": 7},
        {"face_id": 11, "person_id": 99},   # dropped: unknown person
        {"face_id": None, "person_id": 7},  # dropped: no face_id
    ])

    assert captured["links"] == [(7, 10)]  # (person_id, face_id) for the known person


class _CommitCountingSession:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def test_move_photos_moves_all_and_commits_once(monkeypatch, tmp_path):
    media_root = tmp_path / "lib"
    media_root.mkdir(parents=True)
    srcs = {}
    for pid, name in ((1, "a.jpg"), (2, "b.jpg")):
        p = media_root / name
        p.write_text("x")
        srcs[pid] = p
    saved = []

    _patch(monkeypatch, _ACTIONS + "photos_repository.get_photo_path", lambda s, pid: str(srcs[pid]))
    _patch(monkeypatch, _ACTIONS + "photos_repository.update_photo_path",
           lambda s, pid, path: saved.append(path))
    _patch(monkeypatch, _ACTIONS + "media_dir_by_id", lambda s, mid: MediaDir(id=mid, path=media_root))

    session = _CommitCountingSession()
    automation_actions.move_photos(session, [
        {"photo_id": 1, "media_dir_id": "G", "target_path": "2024"},
        {"photo_id": 2, "media_dir_id": "G", "target_path": "2024"},
    ])

    assert (media_root / "2024" / "a.jpg").exists()
    assert (media_root / "2024" / "b.jpg").exists()
    assert session.commits == 1  # one transaction for the whole batch


def test_rename_files_renames_all_and_commits_once(monkeypatch, tmp_path):
    src = tmp_path / "old.jpg"
    src.write_text("x")
    saved = []

    _patch(monkeypatch, _ACTIONS + "photos_repository.get_photo_path", lambda s, pid: str(src))
    _patch(monkeypatch, _ACTIONS + "photos_repository.update_photo_path",
           lambda s, pid, path: saved.append(path))

    session = _CommitCountingSession()
    automation_actions.rename_files(session, [{"photo_id": 1, "new_name": "new.jpg"}])

    assert (tmp_path / "new.jpg").exists() and not src.exists()
    assert session.commits == 1


class _RecordingReporter:
    def __init__(self):
        self.updates = []

    def progress_update(self, task_count, completed, cancelled, errors):
        self.updates.append((task_count, completed, cancelled, errors))


def test_report_progress_updates_the_injected_reporter():
    reporter = _RecordingReporter()
    automation_actions.report_progress(reporter, 3, 10)
    # (task_count=total, completed, cancelled, errors)
    assert reporter.updates == [(10, 3, 0, 0)]


def test_report_progress_noop_without_a_reporter():
    # In a test/preview no Job exists, so the injected reporter is None — harmless.
    automation_actions.report_progress(None, 3, 10)  # must not raise


def test_enrich_photo_rows_adds_media_dir_id_and_relative_path(monkeypatch):
    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.media_dirs."
           "photos_repository.get_paths_by_ids",
           lambda s, ids: {1: "/lib/2024/IMG.jpg", 2: "/elsewhere/x.jpg"})
    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.media_dirs.get_media_dir_entries",
           lambda s: [MediaDir(id="GUID", path=Path("/lib"))])

    rows = media_dirs.enrich_photo_rows(object(), [{"id": 1, "year": 2024}, {"id": 2}])

    assert rows[0]["media_dir_id"] == "GUID"
    assert rows[0]["relative_path"] == "2024/IMG.jpg"
    assert rows[1]["media_dir_id"] is None  # not under any media dir
    assert rows[1]["relative_path"] is None