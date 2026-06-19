"""Unit tests for the classify-labels *system* automation: the threshold / top-K
selection in _classify_photos and the handler's enqueue/no-op behaviour. The CLIP
encoders, image loading, and repositories are stubbed so the test stays fast and
DB-free; what's under test is the labeling policy and the handler wiring.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from yaffo.background_tasks.tasks import classify_labels_automation as mod

pytestmark = pytest.mark.unit


def _patch(monkeypatch, *, labels, label_vectors, image_vectors):
    """Stub the repo + encoders so _classify_photos runs against fixture vectors, and
    record every replace_photo_labels(photo_id, assignments) call."""
    written: dict[int, list[tuple[int, float]]] = {}

    label_objs = [SimpleNamespace(id=lid, name=name, effective_prompt=name) for lid, name in labels]
    monkeypatch.setattr(mod.classification_repository, "get_enabled_labels", lambda session: label_objs)
    monkeypatch.setattr(mod, "embed_texts", lambda prompts: np.asarray(label_vectors, dtype=np.float32))
    monkeypatch.setattr(
        mod.photos_repository, "get_paths_by_ids",
        lambda session, photo_ids: {pid: f"/photos/{pid}.jpg" for pid in photo_ids},
    )
    monkeypatch.setattr(mod, "image_from_path", lambda path: path)
    monkeypatch.setattr(mod, "image_to_numpy", lambda image: image)
    monkeypatch.setattr(mod, "embed_image", lambda image: np.asarray(image_vectors[str(image)], dtype=np.float32))

    def _replace(session, photo_id, assignments):
        written[photo_id] = assignments

    monkeypatch.setattr(mod.classification_repository, "replace_photo_labels", _replace)
    return written


def test_keeps_only_labels_at_or_above_threshold(monkeypatch):
    # label 1 ~ photo (cos 1.0), label 2 orthogonal (cos 0.0)
    written = _patch(
        monkeypatch,
        labels=[(1, "dog"), (2, "beach")],
        label_vectors=[[1.0, 0.0], [0.0, 1.0]],
        image_vectors={"/photos/5.jpg": [1.0, 0.0]},
    )
    labeled = mod._classify_photos(session=None, photo_ids=[5], threshold=0.5, max_labels=5)
    assert labeled == [5]
    assert written[5] == [(1, pytest.approx(1.0))]


def test_caps_at_max_labels_keeping_highest(monkeypatch):
    # three labels all clear the threshold; max_labels=2 keeps the two strongest
    v = lambda a, b: [a, b]
    written = _patch(
        monkeypatch,
        labels=[(1, "a"), (2, "b"), (3, "c")],
        label_vectors=[v(1.0, 0.0), v(0.96, 0.28), v(0.92, 0.39)],
        image_vectors={"/photos/1.jpg": v(1.0, 0.0)},
    )
    mod._classify_photos(session=None, photo_ids=[1], threshold=0.5, max_labels=2)
    kept = [lid for lid, _ in written[1]]
    assert kept == [1, 2]  # highest two cosines, in order


def test_no_match_clears_labels(monkeypatch):
    written = _patch(
        monkeypatch,
        labels=[(1, "dog")],
        label_vectors=[[1.0, 0.0]],
        image_vectors={"/photos/9.jpg": [0.0, 1.0]},  # orthogonal -> cos 0
    )
    labeled = mod._classify_photos(session=None, photo_ids=[9], threshold=0.23, max_labels=5)
    assert labeled == []
    assert written[9] == []  # still replaced (wipes any stale labels)


def test_no_enabled_labels_is_noop(monkeypatch):
    written = _patch(monkeypatch, labels=[], label_vectors=[], image_vectors={})
    assert mod._classify_photos(session=None, photo_ids=[1], threshold=0.23, max_labels=5) == []
    assert written == {}


def test_unreadable_image_is_skipped(monkeypatch):
    written = _patch(
        monkeypatch,
        labels=[(1, "dog")],
        label_vectors=[[1.0, 0.0]],
        image_vectors={"/photos/2.jpg": [1.0, 0.0]},
    )

    def _boom(path):
        raise OSError("corrupt")

    monkeypatch.setattr(mod, "image_from_path", _boom)
    assert mod._classify_photos(session=None, photo_ids=[2], threshold=0.5, max_labels=5) == []
    assert written == {}  # skipped entirely, not replaced


def test_handler_enqueues_for_event_photos(monkeypatch):
    calls: list[tuple[int, list[int], list[int]]] = []
    monkeypatch.setattr(
        mod, "classify_labels_automation_task",
        lambda automation_id, photo_ids, origin: calls.append((automation_id, photo_ids, origin)),
    )
    mod.enqueue_classify_labels(
        SimpleNamespace(id=3),
        SimpleNamespace(photo_ids=[11, 12], origin_automation_ids=[9]),
    )
    assert calls == [(3, [11, 12], [9])]


def test_handler_noop_without_context(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        mod, "classify_labels_automation_task",
        lambda automation_id, photo_ids: calls.append((automation_id, photo_ids)),
    )
    mod.enqueue_classify_labels(SimpleNamespace(id=3), None)
    assert calls == []
