import json
import time as monotonic_time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.common import MEDIA_EXTENSIONS
from yaffo.db.models import (
    JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, MEDIA_STATUS_FAILED, MEDIA_STATUS_INDEXED, Job, MediaItem,
)
from yaffo.db.repositories.job_repository import is_job_cancelled, open_run_job
from yaffo.utils.index_errors import file_signature
from yaffo.utils.job_codes import PROBLEM_MEDIA_FOLDER_EMPTY
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
from yaffo.utils.thumbnail_marker import ensure_thumbnail_dir, in_marked_thumbnail_dir
from yaffo.utils.time import utcnow

logger = get_logger(__name__, 'background_tasks')


# Why a DB row no longer has a live file under the media dirs. Drives the orphaned
# table copy so the user can tell a deleted file from a de-configured directory.
ORPHAN_MISSING = "missing"          # file gone from disk (its media dir still configured)
ORPHAN_UNCONFIGURED = "unconfigured"  # path no longer under any configured media dir


# Every file-sync run's own Job (see run_file_sync), so the run history shows each
# run and how it ended. `job_data["outcome"]` is one of FILE_SYNC_OUTCOMES; the run
# history words it (routes/utilities/run_history.py).
FILE_SYNC_JOB = "file_sync"
SYNC_NO_MEDIA_DIRS = "no_media_dirs"
SYNC_NO_THUMBNAIL_DIR = "no_thumbnail_dir"
SYNC_NO_FOLDER_CONNECTED = "no_folder_connected"
SYNC_IN_SYNC = "in_sync"
SYNC_STARTED = "started"
SYNC_FAILED = "failed"
SYNC_CANCELLED = "cancelled"
FILE_SYNC_OUTCOMES = (SYNC_NO_MEDIA_DIRS, SYNC_NO_THUMBNAIL_DIR, SYNC_NO_FOLDER_CONNECTED,
                      SYNC_IN_SYNC, SYNC_STARTED, SYNC_FAILED)
# Outcomes where the sync didn't run at all.
SYNC_SKIPPED = (SYNC_NO_MEDIA_DIRS, SYNC_NO_THUMBNAIL_DIR, SYNC_NO_FOLDER_CONNECTED)


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
    # Configured media folders that exist but hold no media files at all: usually a
    # drive that didn't mount properly (an empty mount point), not a deliberate
    # deletion. Unattended syncs leave the items under them alone.
    empty_roots: list[str] = field(default_factory=list)
    # FAILED items whose file is unchanged since they failed: known, not new work.
    # (A FAILED item whose file changed is back in `unindexed`, to be retried.)
    failed_unchanged: int = 0

    @property
    def files_to_index(self) -> list[str]:
        return [p['full_path'] for p in self.unindexed]

    @property
    def orphaned_media_item_ids(self) -> list[int]:
        return [o['id'] for o in self.orphaned]


def orphan_reason(path: Path, media_dirs: list[Path]) -> str | None:
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
    scoped: bool = False,
) -> "Iterator[int | MediaScan]":
    """Walk the media dirs and diff them against the index, yielding the running
    count of photo files found every `progress_every` files walked (so a caller can
    drive a live counter), then yielding the final MediaScan.

    The slow part is the recursive walk and the per-row `exists()` check; emitting the
    count incrementally lets the page show progress instead of blocking on the whole
    scan. `scan_media_dirs` consumes this for callers that just want the result."""
    rows = session.query(MediaItem.id, MediaItem.full_file_path, MediaItem.status,
                         MediaItem.index_failed_signature).all()
    db_photos = [(row[0], row[1], row[2]) for row in rows]
    # A FAILED item is skipped until its file changes (signature: size:mtime).
    failed_signatures = {
        str(Path(path).expanduser().resolve(strict=False)): signature
        for _id, path, status, signature in rows if status == MEDIA_STATUS_FAILED
    }
    if scoped:
        db_photos = [row for row in db_photos if any(
            Path(row[1]).is_relative_to(root) for root in media_dirs
        )]
    indexed_paths = {
        str(Path(path).expanduser().resolve(strict=False))
        for _id, path, status in db_photos
        if status == MEDIA_STATUS_INDEXED
    }

    filesystem_paths: set[str] = set()
    unindexed: list[dict] = []
    empty_roots: list[str] = []
    walked = 0
    failed_unchanged = 0
    marked_dirs: dict[Path, bool] = {}
    started_at = monotonic_time.monotonic()
    for media_dir in media_dirs:
        if not media_dir.exists():
            continue
        found_before = len(filesystem_paths)
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
            # Any yaffo thumbnail dir, not just ours: a peer's or a previous one.
            if in_marked_thumbnail_dir(photo_file, marked_dirs):
                continue
            full_path = str(resolved_photo)
            filesystem_paths.add(full_path)
            if full_path in indexed_paths:
                continue
            if full_path in failed_signatures and file_signature(resolved_photo) == failed_signatures[full_path]:
                failed_unchanged += 1
                continue
            unindexed.append({'filename': photo_file.name, 'full_path': full_path})
        if len(filesystem_paths) == found_before:
            empty_roots.append(str(media_dir))

    orphaned = []
    for media_item_id, path, _status in db_photos:
        reason = orphan_reason(Path(path), media_dirs)
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
        empty_roots=empty_roots,
        failed_unchanged=failed_unchanged,
    )


def under_empty_root(path: str, empty_roots: list[str]) -> bool:
    """Whether `path` lies under one of the scan's empty media folders."""
    expanded = Path(path).expanduser()
    return any(expanded.is_relative_to(Path(root).expanduser()) for root in empty_roots)


def root_has_media(root: Path) -> bool:
    """Whether a media folder holds at least one media file. Stops at the first
    one, so it's cheap on a healthy folder; an empty mount point returns False."""
    try:
        return any(
            path.suffix.lower() in MEDIA_EXTENSIONS and not is_system_file(path.name) and path.is_file()
            for path in root.rglob("*")
        )
    except OSError:
        return False


def scan_media_dirs(
    session: Session, media_dirs: list[Path], thumbnail_dir: Path | None, scoped: bool = False
) -> MediaScan:
    """Compare what's on disk under `media_dirs` against the index (non-streaming)."""
    scan: MediaScan | None = None
    for event in iter_media_scan(session, media_dirs, thumbnail_dir, scoped=scoped):
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


def _open_run(session: Session, automation_id: int | None, job_id: str | None) -> Job | None:
    """Open (or, on a queue retry, adopt) the run's Job; None when it already ended."""
    return open_run_job(session, job_id or str(uuid.uuid4()), name=FILE_SYNC_JOB,
                        automation_id=automation_id, task_count=1)


def _close_run(session: Session, job: Job, outcome: str, data: dict | None = None, error: str | None = None) -> None:
    """Finish the run's Job. A skipped or failed run is FAILED; a run that finished
    but needs attention (items left alone) is COMPLETED with an error. A run
    cancelled while it ran stays CANCELLED."""
    if is_job_cancelled(session, job.id):
        outcome = SYNC_CANCELLED
    failed = outcome in SYNC_SKIPPED or outcome in (SYNC_FAILED, SYNC_CANCELLED)
    if outcome != SYNC_CANCELLED:
        job.status = JOB_STATUS_FAILED if failed else JOB_STATUS_COMPLETED
    job.completed_count = 0 if failed else 1
    job.error_count = 1 if error else 0
    job.error = error
    job.job_data = json.dumps({"outcome": outcome, **(data or {})})
    job.completed_at = utcnow()
    session.commit()


def run_file_sync(session: Session, automation_id: int | None = None,
                  scope_paths: list[str] | None = None, job_id: str | None = None) -> IndexJobs | None:
    """Full reconcile for the file-sync automation (scheduled, or Run now): scan
    the configured media dirs and run the same sync the user would trigger by
    hand. Each run records its own FILE_SYNC_JOB with how it ended, so the run
    history shows it even when there was nothing to do. Returns the created
    import/index Jobs, or None when it skipped or was already in sync.
    `automation_id` tags the Jobs as that automation's run. `job_id` is the task's
    queue id, so a retried task records on the same run Job; a run whose Job
    already ended doesn't sync again."""
    run = _open_run(session, automation_id, job_id)
    if run is None:
        logger.info(f"file_sync: run {job_id} already ended; not syncing again")
        return None
    try:
        return _file_sync(session, run, scope_paths)
    except Exception as exc:
        logger.error(f"file_sync: run {run.id} failed: {exc}", exc_info=True)
        session.rollback()
        _close_run(session, run, SYNC_FAILED, error=f"The sync failed: {exc}")
        raise


def _file_sync(session: Session, run: Job, scope_paths: list[str] | None = None) -> IndexJobs | None:
    media_dirs = [Path(path) for path in scope_paths] if scope_paths is not None else get_media_dirs(session)
    thumbnail_dir = get_thumbnail_dir(session)
    if not media_dirs:
        logger.info("file_sync: no media directories configured; skipping")
        _close_run(session, run, SYNC_NO_MEDIA_DIRS, error="No media folders are configured.")
        return None
    if thumbnail_dir is None:
        logger.info("file_sync: no thumbnail directory configured; skipping")
        _close_run(session, run, SYNC_NO_THUMBNAIL_DIR, error="No thumbnail folder is configured.")
        return None
    if not any(d.exists() for d in media_dirs):
        logger.warning("file_sync: no configured media directory exists; skipping")
        _close_run(session, run, SYNC_NO_FOLDER_CONNECTED,
                   error=f"None of the media folders is connected: {', '.join(str(d) for d in media_dirs)}.")
        return None

    ensure_thumbnail_dir(thumbnail_dir)
    scan = scan_media_dirs(session, media_dirs, thumbnail_dir, scoped=scope_paths is not None)
    # Nobody reviews an unattended sync: a media folder that came back empty is far
    # likelier a failed mount than a deletion, so its items stay.
    orphaned_ids = [o['id'] for o in scan.orphaned if not under_empty_root(o['full_path'], scan.empty_roots)]
    held_back = len(scan.orphaned) - len(orphaned_ids)
    error = None
    if held_back:
        logger.warning(
            f"file_sync: media folder(s) {', '.join(scan.empty_roots)} hold no media files; leaving "
            f"{held_back} indexed item(s) under them alone"
        )
        error = (f"These media folders hold no media files, which usually means a drive isn't connected: "
                 f"{', '.join(scan.empty_roots)}. The {held_back} indexed item(s) under them were left in the "
                 "library. Check the drive, or remove the folder in Settings if it's empty on purpose.")
    data = {"indexed": len(scan.files_to_index), "removed": len(orphaned_ids), "held_back": held_back,
            "empty_roots": scan.empty_roots}
    if held_back:
        data.update(problem=PROBLEM_MEDIA_FOLDER_EMPTY,
                    problem_params={"roots": scan.empty_roots, "count": held_back})
    # The scan is the slow part; a cancel during it must stop the sync before it
    # deletes orphans or enqueues indexing.
    if is_job_cancelled(session, run.id):
        logger.info(f"file_sync: run {run.id} cancelled after the scan; nothing changed")
        _close_run(session, run, SYNC_CANCELLED, data)
        return None
    if not scan.files_to_index and not orphaned_ids:
        logger.info("file_sync: index already in sync; nothing to do")
        _close_run(session, run, SYNC_IN_SYNC, data, error)
        return None

    logger.info(
        f"file_sync: {len(scan.files_to_index)} to (re)index, "
        f"{len(orphaned_ids)} orphan rows to remove"
    )
    jobs = perform_sync(
        session, scan.files_to_index, orphaned_ids, thumbnail_dir,
        automation_id=run.automation_id,
    )
    _close_run(session, run, SYNC_STARTED,
               {**data, "import_job_id": jobs.import_job_id, "index_job_id": jobs.index_job_id}, error)
    return jobs
