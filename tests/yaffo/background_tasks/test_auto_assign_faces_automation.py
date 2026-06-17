"""Unit tests for the auto-assign-faces *system* automation: the unique-strong-match
logic in _assign_faces and the handler's enqueue/no-op behaviour. The face-similarity
math and repositories are stubbed so the test stays fast and DB-free; what's under
test is the threshold + ambiguity policy and the handler wiring.
"""
from types import SimpleNamespace

import pytest

from yaffo.background_tasks.tasks import auto_assign_faces_automation as mod

pytestmark = pytest.mark.unit


def _patch(monkeypatch, *, faces_by_photo, scores_by_face, people=("p",)):
    """Stub the repos + similarity so _assign_faces runs against fixture data, and
    record every (person_id, face_id) the code tries to link."""
    linked: list[tuple[int, int]] = []

    monkeypatch.setattr(mod.person_repository, "get_people_with_embeddings", lambda session: list(people))
    monkeypatch.setattr(
        mod.photos_repository, "get_faces_for_photo",
        lambda session, photo_id: faces_by_photo.get(photo_id, []),
    )
    monkeypatch.setattr(mod, "calculate_face_similarity", lambda face, people: scores_by_face[face.id])

    def _link(session, person_id, face_id):
        linked.append((person_id, face_id))
        return True

    monkeypatch.setattr(mod.person_repository, "link_face_to_person", _link)
    return linked


def test_assigns_unique_strong_match(monkeypatch):
    face = SimpleNamespace(id=10)
    linked = _patch(
        monkeypatch,
        faces_by_photo={1: [face]},
        scores_by_face={10: {7: 0.97, 8: 0.40}},  # only person 7 clears 0.95
    )
    assigned = mod._assign_faces(session=None, photo_ids=[1], threshold=0.95)
    assert assigned == 1
    assert linked == [(7, 10)]


def test_ambiguous_match_is_skipped(monkeypatch):
    face = SimpleNamespace(id=10)
    linked = _patch(
        monkeypatch,
        faces_by_photo={1: [face]},
        scores_by_face={10: {7: 0.97, 8: 0.96}},  # two people clear the bar -> ambiguous
    )
    assert mod._assign_faces(session=None, photo_ids=[1], threshold=0.95) == 0
    assert linked == []


def test_no_match_above_threshold_is_skipped(monkeypatch):
    face = SimpleNamespace(id=10)
    _patch(monkeypatch, faces_by_photo={1: [face]}, scores_by_face={10: {7: 0.5}})
    assert mod._assign_faces(session=None, photo_ids=[1], threshold=0.95) == 0


def test_threshold_is_respected(monkeypatch):
    face = SimpleNamespace(id=10)
    linked = _patch(
        monkeypatch,
        faces_by_photo={1: [face]},
        scores_by_face={10: {7: 0.90}},  # below 0.95 but above a relaxed 0.85
    )
    assert mod._assign_faces(session=None, photo_ids=[1], threshold=0.85) == 1
    assert linked == [(7, 10)]


def test_no_people_is_noop(monkeypatch):
    face = SimpleNamespace(id=10)
    _patch(monkeypatch, faces_by_photo={1: [face]}, scores_by_face={10: {}}, people=())
    assert mod._assign_faces(session=None, photo_ids=[1], threshold=0.95) == 0


def test_handler_enqueues_for_event_photos(monkeypatch):
    calls: list[tuple[int, list[int]]] = []
    monkeypatch.setattr(
        mod, "auto_assign_faces_automation_task",
        lambda automation_id, photo_ids: calls.append((automation_id, photo_ids)),
    )
    automation = SimpleNamespace(id=3)
    context = SimpleNamespace(photo_ids=[11, 12])
    mod.enqueue_auto_assign_faces(automation, context)
    assert calls == [(3, [11, 12])]


def test_handler_noop_without_context_or_photos(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        mod, "auto_assign_faces_automation_task",
        lambda automation_id, photo_ids: calls.append((automation_id, photo_ids)),
    )
    automation = SimpleNamespace(id=3)
    mod.enqueue_auto_assign_faces(automation, None)
    mod.enqueue_auto_assign_faces(automation, SimpleNamespace(photo_ids=[]))
    assert calls == []
