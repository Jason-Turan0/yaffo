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
    record every (person_id, face_id) the code flushes via bulk_link_faces_to_people."""
    linked: list[tuple[int, int]] = []

    monkeypatch.setattr(mod.person_repository, "get_people_with_embeddings", lambda session: list(people))
    monkeypatch.setattr(
        mod.media_repository, "get_faces_for_media_item",
        lambda session, media_item_id: faces_by_photo.get(media_item_id, []),
    )
    monkeypatch.setattr(mod, "calculate_face_similarity", lambda face, people: scores_by_face[face.id])

    def _bulk_link(session, links):
        linked.extend(links)
        return len(links)

    monkeypatch.setattr(mod.person_repository, "bulk_link_faces_to_people", _bulk_link)
    return linked


def test_assigns_unique_strong_match(monkeypatch, progress_reporter):
    face = SimpleNamespace(id=10)
    linked = _patch(
        monkeypatch,
        faces_by_photo={1: [face]},
        scores_by_face={10: {7: 0.97, 8: 0.40}},  # only person 7 clears 0.95
    )
    assigned = mod._assign_faces(None, progress_reporter, media_item_ids=[1], threshold=0.95)
    assert assigned == 1
    assert linked == [(7, 10)]
    assert progress_reporter.run_with_progress_calls == [[1]]


def test_ambiguous_match_is_skipped(monkeypatch, progress_reporter):
    face = SimpleNamespace(id=10)
    linked = _patch(
        monkeypatch,
        faces_by_photo={1: [face]},
        scores_by_face={10: {7: 0.97, 8: 0.96}},  # two people clear the bar -> ambiguous
    )
    assert mod._assign_faces(None, progress_reporter, media_item_ids=[1], threshold=0.95) == 0
    assert linked == []
    assert progress_reporter.run_with_progress_calls == [[1]]


def test_ambiguous_match_can_assign_highest_score(monkeypatch, progress_reporter):
    face = SimpleNamespace(id=10)
    linked = _patch(
        monkeypatch,
        faces_by_photo={1: [face]},
        scores_by_face={10: {7: 0.96, 8: 0.98}},  # two people clear the bar -> pick best
    )
    assert mod._assign_faces(
        None,
        progress_reporter,
        media_item_ids=[1],
        threshold=0.95,
        assign_multiple_matches=True,
    ) == 1
    assert linked == [(8, 10)]
    assert progress_reporter.run_with_progress_calls == [[1]]


def test_no_match_above_threshold_is_skipped(monkeypatch, progress_reporter):
    face = SimpleNamespace(id=10)
    _patch(monkeypatch, faces_by_photo={1: [face]}, scores_by_face={10: {7: 0.5}})
    assert mod._assign_faces(None, progress_reporter, media_item_ids=[1], threshold=0.95) == 0
    assert progress_reporter.run_with_progress_calls == [[1]]


def test_threshold_is_respected(monkeypatch, progress_reporter):
    face = SimpleNamespace(id=10)
    linked = _patch(
        monkeypatch,
        faces_by_photo={1: [face]},
        scores_by_face={10: {7: 0.90}},  # below 0.95 but above a relaxed 0.85
    )
    assert mod._assign_faces(None, progress_reporter, media_item_ids=[1], threshold=0.85) == 1
    assert linked == [(7, 10)]
    assert progress_reporter.run_with_progress_calls == [[1]]


def test_no_people_is_noop(monkeypatch, progress_reporter):
    face = SimpleNamespace(id=10)
    _patch(monkeypatch, faces_by_photo={1: [face]}, scores_by_face={10: {}}, people=())
    assert mod._assign_faces(None, progress_reporter, media_item_ids=[1], threshold=0.95) == 0
    # No people to match against — the function returns before reporting any progress.
    assert progress_reporter.run_with_progress_calls == []


def test_task_scales_ui_threshold_to_cosine_against_the_band(monkeypatch, progress_reporter):
    """The stored config value is a 0-100 UI threshold; the task must scale it to a
    cosine cutoff against the live similarity band before matching -- not pass the
    raw UI number to _assign_faces."""
    captured = {}

    class FakeFactory:
        def __call__(self):
            return SimpleNamespace(get=lambda model, _id: SimpleNamespace(id=_id), close=lambda: None)

        def remove(self):
            pass

    monkeypatch.setattr(mod, "SessionFactory", FakeFactory())
    monkeypatch.setattr(
        mod,
        "config_value",
        lambda automation, field: 50 if field.key == "threshold" else True,
    )  # UI midpoint + assign multiple matches enabled
    monkeypatch.setattr(mod, "get_similarity_bounds", lambda session: (0.40, 0.80))
    monkeypatch.setattr(mod, "record_run", lambda session, automation, work: work(progress_reporter))

    def _capture(session, reporter, media_item_ids, threshold, assign_multiple_matches=False):
        captured["threshold"] = threshold
        captured["reporter"] = reporter
        captured["assign_multiple_matches"] = assign_multiple_matches
        return 0

    monkeypatch.setattr(mod, "_assign_faces", _capture)

    # .fn is the undecorated function -- run it inline instead of enqueueing.
    mod.auto_assign_faces_automation_task.fn(automation_id=1, media_item_ids=[1])

    # ui_threshold_to_similarity(50, 0.40, 0.80) == midpoint == 0.60
    assert captured["threshold"] == pytest.approx(0.60)
    # record_run hands work the ProgressReporter, which is threaded through to _assign_faces.
    assert captured["reporter"] is progress_reporter
    assert captured["assign_multiple_matches"] is True


def test_handler_enqueues_for_event_photos(monkeypatch):
    calls: list[tuple[int, list[int]]] = []
    monkeypatch.setattr(
        mod, "auto_assign_faces_automation_task",
        lambda automation_id, media_item_ids: calls.append((automation_id, media_item_ids)),
    )
    automation = SimpleNamespace(id=3)
    context = SimpleNamespace(media_item_ids=[11, 12])
    mod.enqueue_auto_assign_faces(automation, context)
    assert calls == [(3, [11, 12])]


def test_handler_noop_without_context_or_photos(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        mod, "auto_assign_faces_automation_task",
        lambda automation_id, media_item_ids: calls.append((automation_id, media_item_ids)),
    )
    automation = SimpleNamespace(id=3)
    mod.enqueue_auto_assign_faces(automation, None)
    mod.enqueue_auto_assign_faces(automation, SimpleNamespace(media_item_ids=[]))
    assert calls == []
