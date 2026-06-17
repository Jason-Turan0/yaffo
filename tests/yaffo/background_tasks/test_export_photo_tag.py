"""Unit tests for the export-photo-tag *system* automation: the config-gated
metadata-writing in _export_tags and the handler's enqueue/no-op behaviour. The
file write (utils.write_metadata) and the DB query are stubbed; what's under test
is which tags get written for each config combination and the handler wiring.
"""
from types import SimpleNamespace

import pytest

from yaffo.background_tasks.tasks import export_photo_tag as mod

pytestmark = pytest.mark.unit


class _FakeQuery:
    def __init__(self, photos):
        self._photos = photos

    def options(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._photos


class _FakeSession:
    def __init__(self, photos):
        self._photos = photos

    def query(self, *a, **k):
        return _FakeQuery(self._photos)


def _photo(tmp_path, *, location, people, name="p.jpg"):
    f = tmp_path / name
    f.write_bytes(b"x")
    faces = [SimpleNamespace(people=[SimpleNamespace(name=n) for n in group]) for group in people]
    return SimpleNamespace(full_file_path=str(f), location_name=location, faces=faces)


def _patch_writes(monkeypatch):
    """Stub the file write to record (location_name, people_names) per call."""
    calls: list[tuple] = []

    def _write(photo_path, location_name=None, people_names=None):
        calls.append((location_name, people_names))
        return True, None

    monkeypatch.setattr(mod, "write_photo_metadata", _write)
    return calls


def test_exports_both_tags(tmp_path, monkeypatch):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location="Paris", people=[["Alice"], ["Bob"]])
    session = _FakeSession([photo])
    written = mod._export_tags(session, [1], export_location=True, export_people=True)
    assert written == 1
    assert calls == [("Paris", ["Alice", "Bob"])]


def test_location_only(tmp_path, monkeypatch):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location="Paris", people=[["Alice"]])
    written = mod._export_tags(_FakeSession([photo]), [1], export_location=True, export_people=False)
    assert written == 1
    assert calls == [("Paris", None)]


def test_people_only(tmp_path, monkeypatch):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location="Paris", people=[["Alice"]])
    written = mod._export_tags(_FakeSession([photo]), [1], export_location=False, export_people=True)
    assert written == 1
    assert calls == [(None, ["Alice"])]


def test_neither_enabled_is_noop(tmp_path, monkeypatch):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location="Paris", people=[["Alice"]])
    assert mod._export_tags(_FakeSession([photo]), [1], export_location=False, export_people=False) == 0
    assert calls == []


def test_no_photo_ids_is_noop(monkeypatch):
    calls = _patch_writes(monkeypatch)
    assert mod._export_tags(_FakeSession([]), [], export_location=True, export_people=True) == 0
    assert calls == []


def test_missing_file_is_skipped(tmp_path, monkeypatch):
    calls = _patch_writes(monkeypatch)
    photo = SimpleNamespace(full_file_path=str(tmp_path / "gone.jpg"), location_name="X", faces=[])
    assert mod._export_tags(_FakeSession([photo]), [1], export_location=True, export_people=True) == 0
    assert calls == []


def test_people_names_deduped_and_sorted(tmp_path, monkeypatch):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location=None, people=[["Bob", "Alice"], ["Alice"]])
    mod._export_tags(_FakeSession([photo]), [1], export_location=False, export_people=True)
    assert calls == [(None, ["Alice", "Bob"])]


def test_handler_enqueues_for_event_photos(monkeypatch):
    calls: list[tuple[int, list[int]]] = []
    monkeypatch.setattr(
        mod, "export_photo_tag_task",
        lambda automation_id, photo_ids: calls.append((automation_id, photo_ids)),
    )
    mod.enqueue_export_photo_tag(SimpleNamespace(id=3), SimpleNamespace(photo_ids=[11, 12]))
    assert calls == [(3, [11, 12])]


def test_handler_noop_without_context_or_photos(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        mod, "export_photo_tag_task",
        lambda automation_id, photo_ids: calls.append((automation_id, photo_ids)),
    )
    mod.enqueue_export_photo_tag(SimpleNamespace(id=3), None)
    mod.enqueue_export_photo_tag(SimpleNamespace(id=3), SimpleNamespace(photo_ids=[]))
    assert calls == []
