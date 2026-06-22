"""Unit tests for the mutating move_photo host action and data_query row
enrichment, with the filesystem + repositories monkeypatched."""
from pathlib import Path

import pytest

from yaffo.background_tasks.automation_sandbox import automation_actions, media_dirs
from yaffo.db.repositories.media_dir_repository import MediaDir

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def emitted_events(monkeypatch):
    """Capture (and neutralise) emit_event so the host actions don't enqueue a real
    dispatch task during unit tests. Returns the list of (event_type, payload)."""
    events: list[tuple] = []
    monkeypatch.setattr(
        automation_actions, "emit_event",
        lambda event_type, payload: events.append((event_type, payload)),
    )
    return events


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


def test_move_photo_noop_when_destination_equals_source(monkeypatch, tmp_path):
    media_root = tmp_path / "lib"
    src = media_root / "2024" / "IMG.jpg"
    src.parent.mkdir(parents=True)
    src.write_text("x")
    saved = {}

    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.automation_actions."
           "photos_repository.get_photo_path", lambda s, pid: str(src))
    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.automation_actions."
           "photos_repository.update_photo_path", lambda s, pid, path: saved.update(path=path))
    _patch(monkeypatch, "yaffo.background_tasks.automation_sandbox.automation_actions.media_dir_by_id",
           lambda s, mid: MediaDir(id=mid, path=media_root))

    # Target resolves to where the file already is (re-running an organize) -> no-op.
    automation_actions.move_photo(object(), 1, "GUID", "2024")

    assert src.exists() and not saved  # left in place, no path update


_ACTIONS = "yaffo.background_tasks.automation_sandbox.automation_actions."


def test_tag_photos_batches_and_drops_incomplete(monkeypatch, emitted_events):
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
    # announces photo_modified for the tagged photos so export_photo_tag reacts
    assert emitted_events == [("photo_modified", {"media_ids": [1, 2]})]


def test_tag_photos_with_nothing_to_write_does_not_emit(monkeypatch, emitted_events):
    _patch(monkeypatch, _ACTIONS + "photos_repository.add_tags", lambda s, items: None)
    automation_actions.tag_photos(object(), [{"name": "no_photo"}])  # all dropped
    assert emitted_events == []


def test_assign_faces_filters_unknown_persons(monkeypatch):
    captured = {}
    _patch(monkeypatch, _ACTIONS + "person_repository.existing_person_ids", lambda s, ids: {7})
    _patch(monkeypatch, _ACTIONS + "person_repository.bulk_link_faces_to_people",
           lambda s, links: captured.update(links=links))


def test_assign_faces_filters_unknown_persons(monkeypatch, emitted_events):
    captured = {}
    _patch(monkeypatch, _ACTIONS + "person_repository.existing_person_ids", lambda s, ids: {7})

    def _bulk_link(s, links):
        captured["links"] = links
        return len(links)  # number newly linked

    _patch(monkeypatch, _ACTIONS + "person_repository.bulk_link_faces_to_people", _bulk_link)
    _patch(monkeypatch, _ACTIONS + "photos_repository.get_photo_ids_for_faces",
           lambda s, face_ids: [100] if face_ids == [10] else [])

    automation_actions.assign_faces(object(), [
        {"face_id": 10, "person_id": 7},
        {"face_id": 11, "person_id": 99},   # dropped: unknown person
        {"face_id": None, "person_id": 7},  # dropped: no face_id
    ])

    assert captured["links"] == [(7, 10)]  # (person_id, face_id) for the known person
    # announces photo_modified for the assigned faces' photo
    assert emitted_events == [("photo_modified", {"media_ids": [100]})]


def test_assign_faces_emits_nothing_when_no_links_made(monkeypatch, emitted_events):
    _patch(monkeypatch, _ACTIONS + "person_repository.existing_person_ids", lambda s, ids: {7})
    _patch(monkeypatch, _ACTIONS + "person_repository.bulk_link_faces_to_people", lambda s, links: 0)
    automation_actions.assign_faces(object(), [{"face_id": 10, "person_id": 7}])
    assert emitted_events == []


def test_delete_photos_trashes_files_then_removes_rows(monkeypatch, tmp_path, emitted_events):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_text("x")
    b.write_text("y")
    trashed = []
    captured = {}

    def _capture_delete(s, ids):
        captured["ids"] = ids
        return []

    _patch(monkeypatch, _ACTIONS + "photos_repository.get_paths_by_ids",
           lambda s, ids: {1: str(a), 2: str(b)})
    monkeypatch.setattr(automation_actions.send2trash, "send2trash", lambda p: trashed.append(p))
    _patch(monkeypatch, _ACTIONS + "photos_repository.delete_photos", _capture_delete)

    automation_actions.delete_photos(object(), [1, 2])

    assert sorted(trashed) == sorted([str(a), str(b)])
    assert captured["ids"] == [1, 2]  # both rows removed after their files were trashed
    assert emitted_events == []  # the photos are gone -> no photo_modified for them


def test_delete_photos_keeps_row_when_trash_fails(monkeypatch, tmp_path):
    a = tmp_path / "a.jpg"
    a.write_text("x")
    captured = {}

    _patch(monkeypatch, _ACTIONS + "photos_repository.get_paths_by_ids", lambda s, ids: {1: str(a)})

    def _boom(_path):
        raise OSError("locked")

    def _capture_delete(s, ids):
        captured["ids"] = ids
        return []

    monkeypatch.setattr(automation_actions.send2trash, "send2trash", _boom)
    _patch(monkeypatch, _ACTIONS + "photos_repository.delete_photos", _capture_delete)

    automation_actions.delete_photos(object(), [1])

    assert captured["ids"] == []  # file couldn't be trashed -> index row left intact


def test_delete_photos_unlinks_returned_face_thumbnails(monkeypatch, tmp_path):
    thumb = tmp_path / "face.jpg"
    thumb.write_text("t")
    _patch(monkeypatch, _ACTIONS + "photos_repository.get_paths_by_ids", lambda s, ids: {})
    _patch(monkeypatch, _ACTIONS + "photos_repository.delete_photos", lambda s, ids: [str(thumb)])

    automation_actions.delete_photos(object(), [1])

    assert not thumb.exists()  # face thumbnail cleaned up


class _CommitCountingSession:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def test_move_photos_moves_all_and_commits_once(monkeypatch, tmp_path, emitted_events):
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
    assert emitted_events == []  # a move changes no exported metadata -> no photo_modified


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