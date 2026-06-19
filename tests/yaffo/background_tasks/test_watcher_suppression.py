"""Tests for the automation loop guard, Mechanism 2 (watcher self-write suppression).

Two layers:
- the queue Store's suppression methods: an exact (path, signature) match is consumed
  once; a different signature is not; expired rows don't match and are swept.
- the watcher_suppression helpers + watcher._filter_self_writes: a recorded self-write
  is suppressed (and consumed), while a genuine external edit (different bytes) is not.
"""
from pathlib import Path

import pytest

from yaffo.background_tasks import watcher, watcher_suppression as ws
from yaffo.background_tasks.config import task_queue
from yaffo.taskq.store import Store

pytestmark = pytest.mark.unit


# ---- Store-level --------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "queue.db"))


def test_match_is_consumed_once(store):
    store.add_suppression("/a/b.jpg", "100:5")
    assert store.consume_suppression("/a/b.jpg", "100:5", 120.0) is True
    assert store.consume_suppression("/a/b.jpg", "100:5", 120.0) is False  # consumed


def test_different_signature_not_consumed(store):
    store.add_suppression("/a/b.jpg", "100:5")
    assert store.consume_suppression("/a/b.jpg", "200:9", 120.0) is False  # left in place
    assert store.consume_suppression("/a/b.jpg", "100:5", 120.0) is True


def test_expired_not_matched_and_swept(store):
    store.add_suppression("/a/b.jpg", "100:5")
    assert store.consume_suppression("/a/b.jpg", "100:5", 0.0) is False  # past TTL
    assert store.sweep_suppressions(0.0) == 1
    assert store.sweep_suppressions(0.0) == 0


# ---- Helper + watcher filter -------------------------------------------

@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """Point task_queue.store (used by the watcher_suppression helpers) at a throwaway
    queue DB so these don't touch the real one."""
    monkeypatch.setattr(task_queue, "_store", Store(str(tmp_path / "queue.db")))
    return task_queue.store


def _photo(tmp_path: Path, name: str, content: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_record_then_suppress_consumes(temp_store, tmp_path):
    photo = _photo(tmp_path, "x.jpg", b"\xff\xd8\xff")
    ws.record_self_write(photo)

    assert ws.should_suppress(photo) is True   # our own write, ignored
    assert ws.should_suppress(photo) is False  # entry consumed -> not suppressed again


def test_external_edit_not_suppressed(temp_store, tmp_path):
    photo = _photo(tmp_path, "x.jpg", b"\xff\xd8\xff")
    ws.record_self_write(photo)
    photo.write_bytes(b"\xff\xd8\xff\x00\x11\x22")  # different size -> different signature

    assert ws.should_suppress(photo) is False  # a real external change still indexes


def test_missing_file_is_not_suppressed(temp_store, tmp_path):
    assert ws.should_suppress(tmp_path / "gone.jpg") is False


def test_filter_self_writes_drops_only_matches(monkeypatch):
    kept = Path("/m/keep.jpg")
    dropped = Path("/m/self.jpg")
    monkeypatch.setattr(watcher, "should_suppress", lambda p: p == dropped)

    assert watcher._filter_self_writes([kept, dropped]) == [kept]
