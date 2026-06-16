"""Unit tests for the mutating move_photo host action and data_query row
enrichment, with the filesystem + repositories monkeypatched."""
from pathlib import Path

import pytest

from yaffo.background_tasks.automation_sandbox import automation_actions, media_dirs
from yaffo.utils.settings import MediaDir

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