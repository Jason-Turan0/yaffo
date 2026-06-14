import threading
import time
from pathlib import Path
from typing import NamedTuple

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver, ObservedWatch

from yaffo.background_tasks.tasks import SessionFactory
from yaffo.common import PHOTO_EXTENSIONS, TEMP_DIR, THUMBNAIL_DIR, TRASH_DIR
from yaffo.logging_config import get_logger
from yaffo.utils.index_jobs import enqueue_index_jobs
from yaffo.utils.index_photos import delete_photos_by_paths, delete_photos_under_dir
from yaffo.utils.settings import get_media_dirs

logger = get_logger(__name__, 'watcher')

IGNORED_DIRS = (TEMP_DIR, TRASH_DIR, THUMBNAIL_DIR)

SETTLE_SECONDS = 5.0   # a file must be quiet this long before we touch it
POLL_INTERVAL = 1.0    # how often the flusher checks for settled files


class DirOp(NamedTuple):
    src: Path
    dest: Path | None   # None when the directory was deleted rather than moved


class Drained(NamedTuple):
    adds: list[Path]
    deletes: list[Path]
    dir_ops: list[DirOp]


def _is_indexable(path: Path) -> bool:
    if path.suffix.lower() not in PHOTO_EXTENSIONS:
        return False
    if path.name.startswith("."):
        return False
    return not any(ignored in path.parents for ignored in IGNORED_DIRS)


class _DebouncedHandler(FileSystemEventHandler):
    """Records file events; a separate flusher enqueues files once they settle.

    Watchdog dispatches events on a single thread, so the handler must not block
    or do real work. It only stamps paths; draining/enqueuing happens elsewhere.
    """

    def __init__(self) -> None:
        self._pending_adds: dict[Path, float] = {}
        self._pending_deletes: dict[Path, float] = {}
        self._pending_dir_ops: dict[Path, tuple[Path | None, float]] = {}
        self._lock = threading.Lock()

    def _mark_add(self, raw_path: str) -> None:
        path = Path(raw_path)
        if not _is_indexable(path):
            return
        with self._lock:
            self._pending_deletes.pop(path, None)
            self._pending_adds[path] = time.monotonic()

    def _mark_delete(self, raw_path: str) -> None:
        path = Path(raw_path)
        if not _is_indexable(path):
            return
        with self._lock:
            self._pending_adds.pop(path, None)
            self._pending_deletes[path] = time.monotonic()

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._mark_add(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._mark_add(event.src_path)

    def _mark_dir_op(self, src_path: str, dest_path: str | None) -> None:
        src = Path(src_path)
        dest = Path(dest_path) if dest_path is not None else None
        with self._lock:
            self._pending_dir_ops[src] = (dest, time.monotonic())

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            self._mark_dir_op(event.src_path, None)
        else:
            self._mark_delete(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            self._mark_dir_op(event.src_path, event.dest_path)
        else:
            self._mark_delete(event.src_path)
            self._mark_add(event.dest_path)

    def drain_settled(self) -> Drained:
        now = time.monotonic()
        adds: list[Path] = []
        deletes: list[Path] = []
        dir_ops: list[DirOp] = []
        with self._lock:
            for path, last_seen in list(self._pending_adds.items()):
                if now - last_seen < SETTLE_SECONDS:
                    continue
                del self._pending_adds[path]
                if path.exists():
                    adds.append(path)
            for path, last_seen in list(self._pending_deletes.items()):
                if now - last_seen < SETTLE_SECONDS:
                    continue
                del self._pending_deletes[path]
                if not path.exists():
                    deletes.append(path)
            for src, (dest, last_seen) in list(self._pending_dir_ops.items()):
                if now - last_seen < SETTLE_SECONDS:
                    continue
                del self._pending_dir_ops[src]
                dir_ops.append(DirOp(src=src, dest=dest))
        return Drained(adds=adds, deletes=deletes, dir_ops=dir_ops)


def _enqueue(paths: list[Path]) -> None:
    session = SessionFactory()
    try:
        enqueue_index_jobs(session, [str(p) for p in paths])
    finally:
        session.close()
        SessionFactory.remove()


def _remove(paths: list[Path]) -> None:
    session = SessionFactory()
    try:
        count = delete_photos_by_paths(session, [str(p) for p in paths])
        if count:
            logger.info(f"Removed {count} deleted photo(s) from the index")
    finally:
        session.close()
        SessionFactory.remove()


def _remove_dir(directory: Path) -> None:
    session = SessionFactory()
    try:
        count = delete_photos_under_dir(session, str(directory))
        if count:
            logger.info(f"Removed {count} photo(s) under moved/deleted directory {directory}")
    finally:
        session.close()
        SessionFactory.remove()


def _under_watched(path: Path, watched: set[Path]) -> bool:
    return any(path == media_dir or media_dir in path.parents for media_dir in watched)


def _existing_photos_under(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [p for p in directory.rglob("*") if p.is_file() and _is_indexable(p)]


def _resolve_dir_ops(dir_ops: list[DirOp], watched: set[Path]) -> tuple[list[Path], list[Path]]:
    """Translate settled directory moves/deletes into concrete file work.

    Returns (paths_to_index, dirs_to_remove). A directory's old location is
    always removed from the index. Its new location is re-indexed only when the
    move landed inside a watched media dir (a move out of the tree just deletes).
    """
    paths_to_index: list[Path] = []
    dirs_to_remove: list[Path] = []
    for op in dir_ops:
        dirs_to_remove.append(op.src)
        if op.dest is not None and _under_watched(op.dest, watched):
            paths_to_index.extend(_existing_photos_under(op.dest))
    return paths_to_index, dirs_to_remove


def _desired_media_dirs() -> set[Path]:
    """Currently-configured media dirs that exist on disk."""
    session = SessionFactory()
    try:
        configured = get_media_dirs(session)
    finally:
        session.close()
        SessionFactory.remove()

    desired = set()
    for media_dir in configured:
        if media_dir.exists():
            desired.add(media_dir)
        else:
            logger.warning(f"Media directory does not exist, skipping: {media_dir}")
    return desired


def _reconcile_watches(
    observer: BaseObserver,
    handler: FileSystemEventHandler,
    watches: dict[Path, ObservedWatch],
    desired: set[Path],
) -> None:
    """Add/remove observer watches so they match the desired set, in place."""
    for path in set(watches) - desired:
        observer.unschedule(watches.pop(path))
        logger.info(f"Stopped watching {path}")
    for path in desired - set(watches):
        watches[path] = observer.schedule(handler, str(path), recursive=True)
        logger.info(f"Watching {path}")


def main() -> None:
    handler = _DebouncedHandler()
    observer = Observer()
    watches: dict[Path, ObservedWatch] = {}

    desired = _desired_media_dirs()
    if not desired:
        logger.info("No media directories configured yet; waiting for configuration.")
    _reconcile_watches(observer, handler, watches, desired)

    observer.start()
    try:
        while True:
            time.sleep(POLL_INTERVAL)

            desired = _desired_media_dirs()
            if desired != set(watches):
                _reconcile_watches(observer, handler, watches, desired)

            result = handler.drain_settled()
            to_index, dirs_to_remove = _resolve_dir_ops(result.dir_ops, set(watches))

            adds = list(dict.fromkeys(result.adds + to_index))
            if adds:
                _enqueue(adds)
            if result.deletes:
                _remove(result.deletes)
            for directory in dirs_to_remove:
                _remove_dir(directory)
    except KeyboardInterrupt:
        logger.info("Watcher shutting down")
    finally:
        observer.stop()
        observer.join()


if __name__ == '__main__':
    main()