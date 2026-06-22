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


def _photo(tmp_path, *, location, people, labels=(), tags=(), favorite=None, name="p.jpg"):
    f = tmp_path / name
    f.write_bytes(b"x")
    faces = [SimpleNamespace(people=[SimpleNamespace(name=n) for n in group]) for group in people]
    label_rows = [SimpleNamespace(label=SimpleNamespace(name=n)) for n in labels]
    tag_rows = [SimpleNamespace(tag_name=tn, tag_value=tv) for tn, tv in tags]
    return SimpleNamespace(
        full_file_path=str(f), location_name=location, faces=faces,
        labels=label_rows, tags=tag_rows, favorite=favorite,
    )


def _patch_writes(monkeypatch):
    """Stub the file write to record (location_name, people_names, keywords) per call."""
    calls: list[tuple] = []

    def _write(photo_path, location_name=None, people_names=None, keywords=None):
        calls.append((location_name, people_names, keywords))
        return True, None

    monkeypatch.setattr(mod, "write_photo_metadata", _write)
    return calls


def test_exports_both_tags(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location="Paris", people=[["Alice"], ["Bob"]])
    session = _FakeSession([photo])
    written = mod._export_tags(session, progress_reporter, [1], export_location=True, export_people=True)
    assert written == 1
    assert calls == [("Paris", ["Alice", "Bob"], None)]
    assert progress_reporter.run_with_progress_calls == [[photo]]


def test_location_only(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location="Paris", people=[["Alice"]])
    written = mod._export_tags(_FakeSession([photo]), progress_reporter, [1], export_location=True, export_people=False)
    assert written == 1
    assert calls == [("Paris", None, None)]
    assert progress_reporter.run_with_progress_calls == [[photo]]


def test_people_only(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location="Paris", people=[["Alice"]])
    written = mod._export_tags(_FakeSession([photo]), progress_reporter, [1], export_location=False, export_people=True)
    assert written == 1
    assert calls == [(None, ["Alice"], None)]
    assert progress_reporter.run_with_progress_calls == [[photo]]


def test_neither_enabled_is_noop(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location="Paris", people=[["Alice"]])
    assert mod._export_tags(_FakeSession([photo]), progress_reporter, [1], export_location=False, export_people=False) == 0
    assert calls == []
    # Nothing enabled to export — the function returns before reporting any progress.
    assert progress_reporter.run_with_progress_calls == []


def test_no_photo_ids_is_noop(monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    assert mod._export_tags(_FakeSession([]), progress_reporter, [], export_location=True, export_people=True) == 0
    assert calls == []
    # No photo ids — the function returns before reporting any progress.
    assert progress_reporter.run_with_progress_calls == []


def test_missing_file_is_skipped(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = SimpleNamespace(full_file_path=str(tmp_path / "gone.jpg"), location_name="X", faces=[])
    assert mod._export_tags(_FakeSession([photo]), progress_reporter, [1], export_location=True, export_people=True) == 0
    assert calls == []
    assert progress_reporter.run_with_progress_calls == [[photo]]


def test_people_names_deduped_and_sorted(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location=None, people=[["Bob", "Alice"], ["Alice"]])
    mod._export_tags(_FakeSession([photo]), progress_reporter, [1], export_location=False, export_people=True)
    assert calls == [(None, ["Alice", "Bob"], None)]
    assert progress_reporter.run_with_progress_calls == [[photo]]


def test_exports_labels_as_keywords(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location="Paris", people=[["Alice"]], labels=["beach", "sunset"])
    written = mod._export_tags(_FakeSession([photo]), progress_reporter, [1], False, False, export_labels=True)
    assert written == 1
    # only labels go out (location/people off); labels become keywords, sorted+deduped
    assert calls == [(None, None, ["beach", "sunset"])]
    assert progress_reporter.run_with_progress_calls == [[photo]]


def test_exports_custom_tags_as_keywords(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location=None, people=[], tags=[("rating", "5"), ("favorite", None)])
    mod._export_tags(_FakeSession([photo]), progress_reporter, [1], False, False, export_custom_tags=True)
    # 'name: value' when a value is present, bare 'name' otherwise; sorted
    assert calls == [(None, None, ["favorite", "rating: 5"])]
    assert progress_reporter.run_with_progress_calls == [[photo]]


def test_labels_and_custom_tags_combine_into_keywords(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location=None, people=[], labels=["beach"], tags=[("trip", "maui")])
    mod._export_tags(_FakeSession([photo]), progress_reporter, [1], False, False, export_labels=True, export_custom_tags=True)
    assert calls == [(None, None, ["beach", "trip: maui"])]
    assert progress_reporter.run_with_progress_calls == [[photo]]


def test_labels_off_means_no_keywords(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location="Paris", people=[["Alice"]], labels=["beach"], tags=[("x", "y")])
    mod._export_tags(_FakeSession([photo]), progress_reporter, [1], export_location=True, export_people=True)
    # labels/custom-tags toggles default off, so keywords stay None even when present
    assert calls == [("Paris", ["Alice"], None)]
    assert progress_reporter.run_with_progress_calls == [[photo]]


def test_exports_favorite_keyword_when_set(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    photo = _photo(tmp_path, location=None, people=[], favorite=True)
    written = mod._export_tags(_FakeSession([photo]), progress_reporter, [1], False, False, export_favorite=True)
    assert written == 1
    assert calls == [(None, None, ["Favorite"])]


def test_favorite_unset_writes_no_keyword(tmp_path, monkeypatch, progress_reporter):
    calls = _patch_writes(monkeypatch)
    # favorite is NULL (default): even with the toggle on, no "Favorite" keyword is
    # emitted (keywords stay None) — Null does nothing.
    photo = _photo(tmp_path, location=None, people=[], favorite=None)
    mod._export_tags(_FakeSession([photo]), progress_reporter, [1], False, False, export_favorite=True)
    assert calls == [(None, None, None)]


def test_handler_enqueues_for_event_photos(monkeypatch):
    calls: list[tuple[int, list[int]]] = []
    monkeypatch.setattr(
        mod, "export_photo_tag_task",
        lambda automation_id, photo_ids: calls.append((automation_id, photo_ids)),
    )
    mod.enqueue_export_photo_tag(SimpleNamespace(id=3), SimpleNamespace(media_ids=[11, 12]))
    assert calls == [(3, [11, 12])]


def test_handler_noop_without_context_or_photos(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        mod, "export_photo_tag_task",
        lambda automation_id, photo_ids: calls.append((automation_id, photo_ids)),
    )
    mod.enqueue_export_photo_tag(SimpleNamespace(id=3), None)
    mod.enqueue_export_photo_tag(SimpleNamespace(id=3), SimpleNamespace(media_ids=[]))
    assert calls == []
