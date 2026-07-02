"""Unit tests for the photo file-system watcher.

These exercise the real handler against a real temporary filesystem: events are
constructed with watchdog's actual event classes and the handler's existence
checks / globbing run against files that genuinely exist (or don't) on disk. No
mocking of the watcher or the filesystem. The OS event *delivery* is driven
directly (rather than waited on) so the directory-move assertions are
deterministic across platforms, except for one end-to-end test that uses a live
Observer to prove the wiring.
"""
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from watchdog.events import (
    DirDeletedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileMovedEvent,
)
from watchdog.observers import Observer

from yaffo.background_tasks import watcher
from yaffo.background_tasks.watcher import (
    DirOp,
    Drained,
    FileMove,
    _DebouncedHandler,
    _is_indexable,
    _resolve_dir_ops,
    _under_watched,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_settle(monkeypatch):
    """Drain immediately; the debounce timer isn't what these tests assert."""
    monkeypatch.setattr(watcher, "SETTLE_SECONDS", 0.0)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff")  # a real file on disk; contents irrelevant
    return path


def _wait_until(predicate, timeout=5.0, interval=0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _Collector:
    """Accumulates drained results from a live observer and reports the net effect.

    Directory operations don't arrive as one deterministic event across platforms
    (a real delete/rename may surface as a dir event, per-child file events, or
    both), so assertions are made on the convergent outcome: is a path ultimately
    going to be indexed / removed, however the OS reported it.
    """

    def __init__(self, handler: _DebouncedHandler, watched: set[Path]) -> None:
        self._handler = handler
        self._watched = watched
        self._adds: list[Path] = []
        self._deletes: list[Path] = []
        self._dir_ops: list[DirOp] = []
        self._file_moves: list[FileMove] = []

    def poll(self) -> None:
        result = self._handler.drain_settled()
        self._adds += result.adds
        self._deletes += result.deletes
        self._dir_ops += result.dir_ops
        self._file_moves += result.file_moves

    def _resolved(self) -> tuple[list[Path], list[Path]]:
        return _resolve_dir_ops(self._dir_ops, self._watched)

    def _resolved_moves(self) -> tuple[list[Path], list[Path]]:
        """A file move's convergent outcome: the destination ends up indexed (when it
        landed in the watched tree), and the source path is no longer indexed either
        way — whether the index updated it in place or removed it."""
        to_index: list[Path] = []
        to_remove: list[Path] = []
        for move in self._file_moves:
            to_remove.append(move.src)
            if _is_indexable(move.dest) and move.dest.exists() and _under_watched(move.dest, self._watched):
                to_index.append(move.dest)
        return to_index, to_remove

    def indexes(self, path: Path) -> bool:
        dir_index, _ = self._resolved()
        move_index, _ = self._resolved_moves()
        return path in self._adds or path in dir_index or path in move_index

    def removes(self, path: Path) -> bool:
        _, removed_dirs = self._resolved()
        _, move_removes = self._resolved_moves()
        if path in self._deletes or path in move_removes:
            return True
        return any(path == d or d in path.parents for d in removed_dirs)


def _eventually(collector: _Collector, check, timeout=15.0) -> bool:
    return _wait_until(lambda: (collector.poll() or True) and check(), timeout=timeout)


def _stays_false(collector: _Collector, check, window=1.5) -> bool:
    """Confirm `check` never becomes True across the window (asserting absence).

    Inherently costs wall-clock time and can only ever show that nothing happened
    *within the window* — the right tool for "this must NOT be indexed/removed".
    """
    deadline = time.monotonic() + window
    while time.monotonic() < deadline:
        collector.poll()
        if check():
            return False
        time.sleep(0.05)
    return True


@contextmanager
def _watching(media: Path):
    """Run a live recursive Observer over `media`, yielding a result Collector.

    Create any pre-existing fixture files BEFORE entering this block so the
    observer doesn't see their creation as new-photo events.
    """
    media.mkdir(parents=True, exist_ok=True)
    handler = _DebouncedHandler()
    observer = Observer()
    observer.schedule(handler, str(media), recursive=True)
    observer.start()
    assert _wait_until(
        lambda: observer.is_alive() and all(emitter.is_alive() for emitter in observer.emitters),
        timeout=2.0,
        interval=0.01,
    )
    time.sleep(0.25)
    try:
        yield _Collector(handler, {media})
    finally:
        observer.stop()
        observer.join()


class TestFileEvents:
    def test_created_photo_is_added(self, tmp_path):
        media_item = _touch(tmp_path / "a.jpg")
        handler = _DebouncedHandler()

        handler.on_created(FileCreatedEvent(str(media_item)))

        assert handler.drain_settled() == Drained(adds=[media_item], deletes=[], dir_ops=[], file_moves=[])

    def test_deleted_photo_is_removed(self, tmp_path):
        gone = tmp_path / "gone.jpg"  # never created on disk
        handler = _DebouncedHandler()

        handler.on_deleted(FileDeletedEvent(str(gone)))

        assert handler.drain_settled() == Drained(adds=[], deletes=[gone], dir_ops=[], file_moves=[])

    def test_non_photo_is_ignored(self, tmp_path):
        txt = _touch(tmp_path / "note.txt")
        handler = _DebouncedHandler()

        handler.on_created(FileCreatedEvent(str(txt)))
        handler.on_deleted(FileDeletedEvent(str(tmp_path / "other.txt")))

        assert handler.drain_settled() == Drained(adds=[], deletes=[], dir_ops=[], file_moves=[])

    def test_create_then_delete_of_missing_file_nets_to_delete(self, tmp_path):
        gone = tmp_path / "a.jpg"  # not on disk
        handler = _DebouncedHandler()

        handler.on_created(FileCreatedEvent(str(gone)))
        handler.on_deleted(FileDeletedEvent(str(gone)))

        # the delete pops the pending add; the file is gone, so a delete remains
        assert handler.drain_settled() == Drained(adds=[], deletes=[gone], dir_ops=[], file_moves=[])

    def test_delete_then_recreate_nets_to_add(self, tmp_path):
        media_item = _touch(tmp_path / "a.jpg")
        handler = _DebouncedHandler()

        handler.on_deleted(FileDeletedEvent(str(media_item)))
        handler.on_created(FileCreatedEvent(str(media_item)))

        assert handler.drain_settled() == Drained(adds=[media_item], deletes=[], dir_ops=[], file_moves=[])

    def test_file_move_is_recorded_as_a_file_move(self, tmp_path):
        # A moved file is no longer a delete+add: it's a (src, dest) pair so the
        # flusher can update the photo in place.
        old = tmp_path / "old.jpg"          # moved away, no longer on disk
        new = _touch(tmp_path / "new.jpg")
        handler = _DebouncedHandler()

        handler.on_moved(FileMovedEvent(str(old), str(new)))

        assert handler.drain_settled() == Drained(
            adds=[], deletes=[], dir_ops=[], file_moves=[FileMove(src=old, dest=new)]
        )


class TestDirectoryEvents:
    def test_directory_renamed_within_media_dir(self, tmp_path):
        media = tmp_path / "organized"
        old_dir = media / "2020"            # renamed away (gone from disk)
        new_dir = media / "2020-vacation"   # the renamed dir, now holding the files
        img1 = _touch(new_dir / "img1.jpg")
        img2 = _touch(new_dir / "trip" / "img2.jpg")
        _touch(new_dir / "notes.txt")       # non-photo, must be ignored

        handler = _DebouncedHandler()
        handler.on_moved(DirMovedEvent(str(old_dir), str(new_dir)))

        result = handler.drain_settled()
        assert result.dir_ops == [DirOp(src=old_dir, dest=new_dir)]

        to_index, dirs_to_remove = _resolve_dir_ops(result.dir_ops, {media})
        # old location's photos get removed from the index...
        assert dirs_to_remove == [old_dir]
        # ...and the photos at the new location (recursively) get re-indexed
        assert set(to_index) == {img1, img2}

    def test_directory_moved_outside_media_dir(self, tmp_path):
        media = tmp_path / "organized"
        media.mkdir(parents=True)
        outside = tmp_path / "elsewhere"
        moved_src = media / "2020"          # gone from the watched tree
        moved_dest = outside / "2020"       # lands outside the watched tree, still on disk
        _touch(moved_dest / "img1.jpg")

        handler = _DebouncedHandler()
        handler.on_moved(DirMovedEvent(str(moved_src), str(moved_dest)))

        result = handler.drain_settled()
        assert result.dir_ops == [DirOp(src=moved_src, dest=moved_dest)]

        to_index, dirs_to_remove = _resolve_dir_ops(result.dir_ops, {media})
        # the old location is removed from the index...
        assert dirs_to_remove == [moved_src]
        # ...and nothing is re-indexed, because the destination isn't watched
        assert to_index == []

    def test_directory_deleted(self, tmp_path):
        media = tmp_path / "organized"
        media.mkdir(parents=True)
        gone_dir = media / "2019"

        handler = _DebouncedHandler()
        handler.on_deleted(DirDeletedEvent(str(gone_dir)))

        result = handler.drain_settled()
        assert result.dir_ops == [DirOp(src=gone_dir, dest=None)]

        to_index, dirs_to_remove = _resolve_dir_ops(result.dir_ops, {media})
        assert dirs_to_remove == [gone_dir]
        assert to_index == []


class TestHelpers:
    def test_under_watched(self, tmp_path):
        media = tmp_path / "m"
        assert _under_watched(media, {media})
        assert _under_watched(media / "a" / "b", {media})
        assert not _under_watched(tmp_path / "other", {media})

    def test_is_indexable(self):
        assert _is_indexable(Path("/photos/a.JPG"))
        # Video files are watched too (mp4/mov/m4v), case-insensitively.
        assert _is_indexable(Path("/photos/clip.mp4"))
        assert _is_indexable(Path("/photos/clip.MOV"))
        assert _is_indexable(Path("/photos/clip.m4v"))
        assert not _is_indexable(Path("/photos/.hidden.jpg"))
        assert not _is_indexable(Path("/photos/a.txt"))
        assert _is_indexable(Path("/photos/clip.avi"))  # cataloged (not inline-playable)
        assert _is_indexable(Path("/photos/clip.mkv"))
        assert not _is_indexable(Path("/photos/clip.webm"))  # genuinely unsupported


class _DummySessionFactory:
    """Stands in for the scoped SessionFactory: callable -> dummy session, + remove()."""
    def __call__(self):
        from types import SimpleNamespace
        return SimpleNamespace(close=lambda: None)

    def remove(self):
        pass


class TestMoveInIndex:
    """_move_in_index turns settled file moves into in-place index updates, with
    fresh-index / remove fallbacks. move_media_item_path + SessionFactory are stubbed."""

    def _run(self, monkeypatch, moves, watched, *, moved=True):
        calls = []
        monkeypatch.setattr(watcher, "move_media_item_path",
                            lambda s, old, new: calls.append((old, new)) or moved)
        monkeypatch.setattr(watcher, "SessionFactory", _DummySessionFactory())
        adds, removes = watcher._move_in_index(moves, watched)
        return adds, removes, calls

    def test_in_place_update_when_dest_in_watched(self, tmp_path, monkeypatch):
        media = tmp_path / "organized"
        new = _touch(media / "new.jpg")
        old = media / "old.jpg"  # source gone after the move
        adds, removes, calls = self._run(monkeypatch, [FileMove(src=old, dest=new)], {media})
        assert adds == [] and removes == []           # updated in place, nothing re-indexed/removed
        assert calls == [(str(old), str(new))]

    def test_fresh_index_when_source_not_in_index(self, tmp_path, monkeypatch):
        media = tmp_path / "organized"
        new = _touch(media / "new.jpg")
        old = media / "old.jpg"
        adds, removes, calls = self._run(monkeypatch, [FileMove(src=old, dest=new)], {media}, moved=False)
        assert adds == [new] and removes == []        # nothing at src -> index dest fresh
        assert calls == [(str(old), str(new))]

    def test_move_in_from_outside_indexes_dest(self, tmp_path, monkeypatch):
        # src is outside the media dirs (never indexed), dest lands inside: there's no
        # row to update in place, so the destination is fresh-indexed.
        media = tmp_path / "organized"
        dest = _touch(media / "photo.jpg")
        outside_src = tmp_path / "incoming" / "photo.jpg"  # outside the watched tree
        adds, removes, calls = self._run(
            monkeypatch, [FileMove(src=outside_src, dest=dest)], {media}, moved=False
        )
        assert adds == [dest] and removes == []
        assert calls == [(str(outside_src), str(dest))]  # tried in-place, found nothing

    def test_move_out_of_watched_removes_source(self, tmp_path, monkeypatch):
        media = tmp_path / "organized"
        outside = _touch(tmp_path / "elsewhere" / "new.jpg")
        old = media / "old.jpg"
        adds, removes, calls = self._run(monkeypatch, [FileMove(src=old, dest=outside)], {media})
        assert adds == [] and removes == [old]        # left the library -> remove
        assert calls == []                            # never touched the index path

    def test_move_to_non_photo_removes_source(self, tmp_path, monkeypatch):
        media = tmp_path / "organized"
        txt = _touch(media / "new.txt")
        old = media / "old.jpg"
        adds, removes, calls = self._run(monkeypatch, [FileMove(src=old, dest=txt)], {media})
        assert removes == [old] and adds == [] and calls == []

    def test_move_to_missing_dest_removes_source(self, tmp_path, monkeypatch):
        media = tmp_path / "organized"
        missing = media / "new.jpg"  # never created on disk
        old = media / "old.jpg"
        adds, removes, calls = self._run(monkeypatch, [FileMove(src=old, dest=missing)], {media})
        assert removes == [old] and adds == [] and calls == []


class TestMovePhotoPath:
    """move_media_item_path updates the row in place (preserving id), against a real DB."""

    @pytest.fixture
    def session(self, tmp_path):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session as SASession
        from yaffo.db import db
        engine = create_engine(f"sqlite:///{tmp_path / 'wt.db'}")
        db.metadata.create_all(engine)
        with SASession(engine) as sess:
            yield sess
        engine.dispose()

    def test_updates_path_in_place_preserving_id(self, session):
        from yaffo.db.models import MediaItem
        from yaffo.db.repositories.media_repository import move_media_item_path
        media_item = MediaItem(full_file_path="/m/old.jpg", status="INDEXED")
        session.add(media_item)
        session.commit()
        original_id = media_item.id

        assert move_media_item_path(session, "/m/old.jpg", "/m/new.jpg") is True
        moved = session.query(MediaItem).filter_by(id=original_id).one()
        assert moved.full_file_path == "/m/new.jpg"  # same row, new path

    def test_returns_false_when_no_photo_at_old_path(self, session):
        from yaffo.db.repositories.media_repository import move_media_item_path
        assert move_media_item_path(session, "/m/missing.jpg", "/m/new.jpg") is False


class TestLiveObserver:
    """Live-Observer mirrors of the unit tests above, driving real filesystem ops.

    Assertions are on the convergent outcome (is a path ultimately indexed /
    removed) rather than exact event shape, because the OS event stream for a
    given operation isn't identical across FSEvents / inotify / Windows.
    """

    # --- mirrors of TestFileEvents ---

    def test_created_photo_is_indexed(self, tmp_path):
        media = tmp_path / "organized"
        with _watching(media) as collector:
            media_item = _touch(media / "a.jpg")
            assert _eventually(collector, lambda: collector.indexes(media_item))

    def test_deleted_photo_is_removed(self, tmp_path):
        media = tmp_path / "organized"
        media_item = _touch(media / "a.jpg")  # exists before watching starts
        with _watching(media) as collector:
            media_item.unlink()
            assert _eventually(collector, lambda: collector.removes(media_item))

    def test_non_photo_is_ignored(self, tmp_path):
        media = tmp_path / "organized"
        with _watching(media) as collector:
            txt = _touch(media / "note.txt")
            assert _stays_false(collector, lambda: collector.indexes(txt))

    def test_create_then_delete_is_not_indexed(self, tmp_path):
        media = tmp_path / "organized"
        with _watching(media) as collector:
            media_item = _touch(media / "a.jpg")
            media_item.unlink()
            # a file that came and went before settling must never be indexed
            assert _stays_false(collector, lambda: collector.indexes(media_item))

    def test_delete_then_recreate_is_indexed(self, tmp_path):
        media = tmp_path / "organized"
        media_item = _touch(media / "a.jpg")  # exists before watching starts
        with _watching(media) as collector:
            media_item.unlink()
            _touch(media_item)
            # the file exists again at rest, so it must end up indexed
            assert _eventually(collector, lambda: collector.indexes(media_item))

    def test_file_move_removes_source_and_indexes_dest(self, tmp_path):
        media = tmp_path / "organized"
        old = _touch(media / "old.jpg")  # exists before watching starts
        with _watching(media) as collector:
            new = media / "new.jpg"
            old.rename(new)
            assert _eventually(
                collector, lambda: collector.removes(old) and collector.indexes(new)
            )

    def test_file_moved_in_from_outside_is_indexed(self, tmp_path):
        # A file living outside the watched media dir, moved in: however the OS reports
        # it (created, or moved with an out-of-tree source), the destination is indexed.
        media = tmp_path / "organized"
        media.mkdir(parents=True)
        incoming = _touch(tmp_path / "incoming" / "photo.jpg")  # outside the watched tree
        with _watching(media) as collector:
            dest = media / "photo.jpg"
            incoming.rename(dest)
            assert _eventually(collector, lambda: collector.indexes(dest))

    # --- mirrors of TestDirectoryEvents ---

    def test_directory_renamed_within_media_dir(self, tmp_path):
        media = tmp_path / "organized"
        old_dir = media / "2020"
        img1 = _touch(old_dir / "img1.jpg")          # exist before watching starts
        img2 = _touch(old_dir / "trip" / "img2.jpg")
        new_dir = media / "2020-vacation"
        with _watching(media) as collector:
            old_dir.rename(new_dir)
            new1 = new_dir / "img1.jpg"
            new2 = new_dir / "trip" / "img2.jpg"
            assert _eventually(
                collector,
                lambda: collector.removes(img1) and collector.removes(img2)
                and collector.indexes(new1) and collector.indexes(new2),
            )

    def test_directory_moved_outside_media_dir(self, tmp_path):
        media = tmp_path / "organized"
        outside = tmp_path / "elsewhere"
        outside.mkdir(parents=True)
        src_dir = media / "2020"
        img = _touch(src_dir / "img.jpg")  # exists before watching starts
        with _watching(media) as collector:
            shutil.move(str(src_dir), str(outside / "2020"))
            moved_img = outside / "2020" / "img.jpg"
            assert _eventually(collector, lambda: collector.removes(img))
            # destination is outside the watched tree, so it must never be indexed
            assert _stays_false(collector, lambda: collector.indexes(moved_img))

    def test_directory_deleted(self, tmp_path):
        media = tmp_path / "organized"
        target = media / "2019"
        img = _touch(target / "img.jpg")  # exists before watching starts
        with _watching(media) as collector:
            shutil.rmtree(target)
            assert _eventually(collector, lambda: collector.removes(img))
