from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.common import PHOTO_EXTENSIONS
from yaffo.db.models import Photo, PHOTO_STATUS_INDEXED, PHOTO_STATUS_SYNCED
from yaffo.logging_config import get_logger
from yaffo.utils.index_jobs import IndexJobs, enqueue_index_jobs
from yaffo.utils.index_photos import (
    delete_orphaned_photos,
    delete_orphaned_thumbnails,
    is_system_file,
)
from yaffo.utils.settings import get_media_dirs, get_thumbnail_dir

logger = get_logger(__name__, 'background_tasks')


@dataclass(frozen=True)
class MediaScan:
    """Diff between the configured media dirs and the photo index. Shared by the
    index-photos page (for display) and the scheduled file-sync task (as work)."""
    unindexed: list[dict]   # {filename, full_path} -- on disk, not yet indexed
    orphaned: list[dict]    # {id, full_path} -- in the DB, file gone from disk
    total_imported: int
    total_indexed: int
    total_filesystem: int

    @property
    def files_to_index(self) -> list[str]:
        return [p['full_path'] for p in self.unindexed]

    @property
    def orphaned_photo_ids(self) -> list[int]:
        return [o['id'] for o in self.orphaned]


def scan_media_dirs(
    session: Session, media_dirs: list[Path], thumbnail_dir: Path | None
) -> MediaScan:
    """Compare what's on disk under `media_dirs` against the index."""
    db_photos = session.query(Photo.id, Photo.full_file_path, Photo.status).all()
    indexed_paths = {
        path for _id, path, status in db_photos
        if status in (PHOTO_STATUS_INDEXED, PHOTO_STATUS_SYNCED)
    }

    filesystem_paths: set[str] = set()
    unindexed: list[dict] = []
    for media_dir in media_dirs:
        if not media_dir.exists():
            continue
        for photo_file in media_dir.rglob("*"):
            if not photo_file.is_file():
                continue
            if photo_file.suffix.lower() not in PHOTO_EXTENSIONS:
                continue
            if is_system_file(photo_file.name):
                continue
            if thumbnail_dir is not None and photo_file.is_relative_to(thumbnail_dir):
                continue
            full_path = str(photo_file)
            filesystem_paths.add(full_path)
            if full_path not in indexed_paths:
                unindexed.append({'filename': photo_file.name, 'full_path': full_path})

    orphaned = [
        {'id': photo_id, 'full_path': path}
        for photo_id, path, _status in db_photos
        if not Path(path).exists()
    ]

    unindexed.sort(key=lambda x: x['full_path'])
    orphaned.sort(key=lambda x: x['full_path'])

    return MediaScan(
        unindexed=unindexed,
        orphaned=orphaned,
        total_imported=len(db_photos),
        total_indexed=len(indexed_paths),
        total_filesystem=len(filesystem_paths),
    )


def perform_sync(
    session: Session,
    files_to_index: list[str],
    orphaned_photo_ids: list[int],
    thumbnail_dir: Path,
    automation_id: int | None = None,
) -> IndexJobs:
    """Apply a sync: drop orphaned rows + thumbnails, then enqueue index jobs.

    The single action behind both the manual sync button and the scheduled task,
    so both create the same import/index Job rows the index-photos UI displays.
    `automation_id` tags those Jobs as a run of that automation (NULL when a user
    triggers the sync by hand).
    """
    delete_orphaned_photos(session, orphaned_photo_ids)
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
        session, scan.files_to_index, scan.orphaned_photo_ids, thumbnail_dir,
        automation_id=automation_id,
    )