import time as monotonic_time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.common import MEDIA_EXTENSIONS
from yaffo.db.models import MEDIA_STATUS_INDEXED, MediaItem
from yaffo.db.repositories.media_dir_repository import get_media_dirs
from yaffo.logging_config import get_logger
from yaffo.utils.index_jobs import enqueue_index_jobs
from yaffo.utils.index_jobs_dto import IndexJobs
from yaffo.utils.index_photos import (
    delete_orphaned_media_items,
    delete_orphaned_thumbnails,
    is_system_file,
)
from yaffo.utils.settings import get_thumbnail_dir
from yaffo.utils.safe_paths import PathOutsideAllowedRoots, resolve_path_in_roots

logger = get_logger(__name__, 'background_tasks')


# Why a DB row no longer has a live file under the media dirs. Drives the orphaned
# table copy so the user can tell a deleted file from a de-configured directory.
ORPHAN_MISSING = "missing"          # file gone from disk (its media dir still configured)
ORPHAN_UNCONFIGURED = "unconfigured"  # path no longer under any configured media dir


class MediaScanLimitExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaScan:
    """Diff between the configured media dirs and the photo index. Shared by the
    index-photos page (for display) and the scheduled file-sync task (as work)."""
    unindexed: list[dict]   # {filename, full_path} -- on disk, not yet indexed
    orphaned: list[dict]    # {id, full_path, reason} -- in the DB, no live file under media dirs
    total_imported: int
    total_indexed: int
    total_filesystem: int

    @property
    def files_to_index(self) -> list[str]:
        return [p['full_path'] for p in self.unindexed]

    @property
    def orphaned_media_item_ids(self) -> list[int]:
        return [o['id'] for o in self.orphaned]


def _orphan_reason(path: Path, media_dirs: list[Path]) -> str | None:
    """Why an indexed photo at `path` is orphaned, or None if it's still valid.

    A row is orphaned when (a) its path is no longer under any configured media dir
    (the directory was removed from Settings -- the file may still sit on disk), or
    (b) it's under a configured dir but the file is gone. Case (b) only counts when
    that dir's root currently exists on disk, so an unmounted drive doesn't get its
    whole subtree marked orphaned and wiped on the next sync."""
    expanded_path = path.expanduser()
    missing_lexical_owner = next(
        (
            media_dir
            for media_dir in media_dirs
            if not media_dir.expanduser().exists()
            and expanded_path.is_relative_to(media_dir.expanduser())
        ),
        None,
    )
    if missing_lexical_owner is not None:
        return None

    try:
        resolved = expanded_path.resolve(strict=False)
    except OSError:
        return ORPHAN_UNCONFIGURED

    owning_dir = None
    for media_dir in media_dirs:
        try:
            resolved_root = media_dir.expanduser().resolve(strict=True)
        except OSError:
            continue
        if resolved == resolved_root or resolved_root in resolved.parents:
            owning_dir = resolved_root
            break
    if owning_dir is None:
        return ORPHAN_UNCONFIGURED

    try:
        resolved, _root = resolve_path_in_roots(resolved, [owning_dir], must_exist=False)
    except PathOutsideAllowedRoots:
        return ORPHAN_UNCONFIGURED
    if not resolved.exists():
        return ORPHAN_MISSING
    return None


def iter_media_scan(
    session: Session,
    media_dirs: list[Path],
    thumbnail_dir: Path | None,
    progress_every: int = 500,
    max_walked: int | None = None,
    max_seconds: float | None = None,
) -> "Iterator[int | MediaScan]":
    """Walk the media dirs and diff them against the index, yielding the running
    count of photo files found every `progress_every` files walked (so a caller can
    drive a live counter), then yielding the final MediaScan.

    The slow part is the recursive walk and the per-row `exists()` check; emitting the
    count incrementally lets the page show progress instead of blocking on the whole
    scan. `scan_media_dirs` consumes this for callers that just want the result."""
    db_photos = session.query(MediaItem.id, MediaItem.full_file_path, MediaItem.status).all()
    indexed_paths = {
        str(Path(path).expanduser().resolve(strict=False))
        for _id, path, status in db_photos
        if status == MEDIA_STATUS_INDEXED
    }

    filesystem_paths: set[str] = set()
    unindexed: list[dict] = []
    walked = 0
    started_at = monotonic_time.monotonic()
    for media_dir in media_dirs:
        if not media_dir.exists():
            continue
        for photo_file in media_dir.rglob("*"):
            walked += 1
            if max_walked is not None and walked > max_walked:
                raise MediaScanLimitExceeded("media scan file limit exceeded")
            if (
                max_seconds is not None
                and monotonic_time.monotonic() - started_at > max_seconds
            ):
                raise MediaScanLimitExceeded("media scan time limit exceeded")
            if walked % progress_every == 0:
                yield len(filesystem_paths)
            if not photo_file.is_file():
                continue
            if photo_file.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            if is_system_file(photo_file.name):
                continue
            try:
                resolved_photo, _root = resolve_path_in_roots(photo_file, [media_dir])
            except PathOutsideAllowedRoots:
                continue
            if thumbnail_dir is not None and resolved_photo.is_relative_to(thumbnail_dir.resolve()):
                continue
            full_path = str(resolved_photo)
            filesystem_paths.add(full_path)
            if full_path not in indexed_paths:
                unindexed.append({'filename': photo_file.name, 'full_path': full_path})

    orphaned = []
    for media_item_id, path, _status in db_photos:
        reason = _orphan_reason(Path(path), media_dirs)
        if reason is not None:
            orphaned.append({'id': media_item_id, 'full_path': path, 'reason': reason})

    unindexed.sort(key=lambda x: x['full_path'])
    orphaned.sort(key=lambda x: x['full_path'])

    yield MediaScan(
        unindexed=unindexed,
        orphaned=orphaned,
        total_imported=len(db_photos),
        total_indexed=len(indexed_paths),
        total_filesystem=len(filesystem_paths),
    )


def scan_media_dirs(
    session: Session, media_dirs: list[Path], thumbnail_dir: Path | None
) -> MediaScan:
    """Compare what's on disk under `media_dirs` against the index (non-streaming)."""
    scan: MediaScan | None = None
    for event in iter_media_scan(session, media_dirs, thumbnail_dir):
        if isinstance(event, MediaScan):
            scan = event
    assert scan is not None  # iter_media_scan always yields a final MediaScan
    return scan


def perform_sync(
    session: Session,
    files_to_index: list[str],
    orphaned_media_item_ids: list[int],
    thumbnail_dir: Path,
    automation_id: int | None = None,
) -> IndexJobs:
    """Apply a sync: drop orphaned rows + thumbnails, then enqueue index jobs.

    The single action behind both the manual sync button and the scheduled task,
    so both create the same import/index Job rows the index-photos UI displays.
    `automation_id` tags those Jobs as a run of that automation (NULL when a user
    triggers the sync by hand).
    """
    delete_orphaned_media_items(session, orphaned_media_item_ids)
    delete_orphaned_thumbnails(session, thumbnail_dir)
    return enqueue_index_jobs(session, files_to_index, automation_id=automation_id)


def run_file_sync(session: Session, automation_id: int | None = None) -> IndexJobs | None:
    """Unattended full reconcile for the scheduled task: scan the configured
    media dirs and run the same sync the user would trigger by hand. Returns the
    created Jobs, or None when nothing is configured or already in sync.
    `automation_id` tags the created Jobs as that automation's run."""
    media_dirs = get_media_dirs(session)
    thumbnail_dir = get_thumbnail_dir(session)
    if not media_dirs:
        logger.info("file_sync: no media directories configured; skipping")
        return None
    if thumbnail_dir is None:
        logger.info("file_sync: no thumbnail directory configured; skipping")
        return None
    if not any(d.exists() for d in media_dirs):
        logger.warning("file_sync: no configured media directory exists; skipping")
        return None

    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    scan = scan_media_dirs(session, media_dirs, thumbnail_dir)
    if not scan.files_to_index and not scan.orphaned:
        logger.info("file_sync: index already in sync; nothing to do")
        return None

    logger.info(
        f"file_sync: {len(scan.files_to_index)} to (re)index, "
        f"{len(scan.orphaned)} orphan rows to remove"
    )
    return perform_sync(
        session, scan.files_to_index, scan.orphaned_media_item_ids, thumbnail_dir,
        automation_id=automation_id,
    )
