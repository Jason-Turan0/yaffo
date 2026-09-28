"""Finding and rebuilding thumbnail files the index points at but that are gone
from disk: face crops (Face.full_file_path) and video posters
(MediaItem.poster_path).

Rebuilding writes each missing file at the path the index already records, from
the item's own file and the face box stored with it, so faces keep their person
assignments and ignored status. Reindexing would re-detect the faces and drop both.
"""
import json
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.db.models import Face, Job, MediaItem, JOB_STATUS_PENDING, MEDIA_TYPE_VIDEO
from yaffo.logging_config import get_logger
from yaffo.utils.index_photos import write_face_crop
from yaffo.utils.index_video import face_crop_frame_index, write_poster, write_sampled_frame
from yaffo.utils.thumbnail_marker import THUMBNAIL_DIR_MARKER

logger = get_logger(__name__, 'background_tasks')

THUMBNAIL_REPAIR_JOB = "regenerate_thumbnails"


@dataclass(frozen=True)
class MissingThumbnails:
    """What the index points at in the thumbnail folder that isn't there.
    `available` is False when the folder can't be checked (not configured, or not
    there, like a drive that isn't connected); the counts are then zero."""
    available: bool
    faces: int = 0
    posters: int = 0
    media_item_ids: list[int] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.faces + self.posters


@dataclass(frozen=True)
class ItemRepair:
    """One item's rebuild: files written, and English reasons for any that weren't."""
    written: int
    failures: list[str]


def thumbnail_dir_available(thumbnail_dir: Path | None) -> bool:
    """Whether the thumbnail folder can be checked. A folder that holds neither the
    marker nor any file is most likely an empty mount point, not a folder whose
    thumbnails were all deleted (deleting them leaves the hidden marker)."""
    if thumbnail_dir is None or not thumbnail_dir.is_dir():
        return False
    return (thumbnail_dir / THUMBNAIL_DIR_MARKER).is_file() or any(thumbnail_dir.iterdir())


def find_missing_thumbnails(session: Session, thumbnail_dir: Path | None) -> MissingThumbnails:
    """Count the face crops and posters whose files are gone. The thumbnail folder is
    listed once, rather than checking each of its (often tens of thousands of)
    files. Items whose own file is gone are left out: nothing can be rebuilt from
    them, and the scan already lists them as orphaned."""
    if thumbnail_dir is None or not thumbnail_dir_available(thumbnail_dir):
        return MissingThumbnails(available=False)
    names = {entry.name for entry in thumbnail_dir.iterdir()}

    def exists(path: str) -> bool:
        file = Path(path)
        return file.name in names if file.parent == thumbnail_dir else file.exists()

    missing_faces: dict[int, int] = {}
    for media_item_id, path in session.query(Face.media_item_id, Face.full_file_path).filter(
            Face.full_file_path.isnot(None)):
        if not exists(path):
            missing_faces[media_item_id] = missing_faces.get(media_item_id, 0) + 1
    missing_posters = {
        media_item_id for media_item_id, path in session.query(MediaItem.id, MediaItem.poster_path).filter(
            MediaItem.poster_path.isnot(None))
        if not exists(path)
    }

    candidate_ids = set(missing_faces) | missing_posters
    rebuildable = sorted(
        media_item_id for media_item_id, path in session.query(MediaItem.id, MediaItem.full_file_path).filter(
            MediaItem.id.in_(candidate_ids))
        if path and Path(path).exists()
    )
    return MissingThumbnails(
        available=True,
        faces=sum(missing_faces.get(media_item_id, 0) for media_item_id in rebuildable),
        posters=sum(1 for media_item_id in rebuildable if media_item_id in missing_posters),
        media_item_ids=rebuildable,
    )


def missing_files_per_item(session: Session, media_item_ids: list[int]) -> dict[int, int]:
    """How many of each item's face crops and poster are missing right now. A repair
    job counts its progress in these files, recounted when it runs."""
    counts = dict.fromkeys(media_item_ids, 0)
    for media_item_id, path in session.query(Face.media_item_id, Face.full_file_path).filter(
            Face.media_item_id.in_(media_item_ids), Face.full_file_path.isnot(None)):
        if not Path(path).exists():
            counts[media_item_id] += 1
    for media_item_id, path in session.query(MediaItem.id, MediaItem.poster_path).filter(
            MediaItem.id.in_(media_item_ids), MediaItem.poster_path.isnot(None)):
        if not Path(path).exists():
            counts[media_item_id] += 1
    return counts


def repair_item_thumbnails(session: Session, media_item: MediaItem) -> ItemRepair:
    """Write the item's missing face crops and poster at their recorded paths. Files
    that already exist are left alone, so running it again is harmless."""
    source = Path(media_item.full_file_path)
    written = 0
    failures: list[str] = []
    missing_faces = [
        face for face in session.query(Face).filter(Face.media_item_id == media_item.id)
        if face.full_file_path and not Path(face.full_file_path).exists()
    ]
    is_video = media_item.media_type == MEDIA_TYPE_VIDEO

    with tempfile.TemporaryDirectory(prefix="yaffo_repair_") as tmp:
        frames: dict[int, Path | None] = {}
        for face in missing_faces:
            out_path = Path(face.full_file_path)
            location = (face.location_top, face.location_right, face.location_bottom, face.location_left)
            crop_source = source
            if is_video:
                index = face_crop_frame_index(out_path, source)
                if index is None:
                    failures.append(f"{out_path.name}: not named after a sampled frame of {source.name}")
                    continue
                if index not in frames:
                    frame = Path(tmp) / f"frame_{index}.png"
                    ok = write_sampled_frame(source, index, media_item.duration_seconds, frame)
                    frames[index] = frame if ok else None
                frame_file = frames[index]
                if frame_file is None:
                    failures.append(f"{out_path.name}: could not read frame {index} of {source.name}")
                    continue
                crop_source = frame_file
            try:
                write_face_crop(crop_source, location, out_path)
                written += 1
            except Exception as e:  # noqa: BLE001 - one bad file mustn't stop the item
                failures.append(f"{out_path.name}: {e}")

    if media_item.poster_path and not Path(media_item.poster_path).exists():
        if write_poster(source, Path(media_item.poster_path), media_item.duration_seconds):
            written += 1
        else:
            failures.append(f"{Path(media_item.poster_path).name}: could not read a frame of {source.name}")

    for failure in failures:
        logger.warning(f"Thumbnail repair, media item {media_item.id}: {failure}")
    return ItemRepair(written=written, failures=failures)


def start_thumbnail_repair(session: Session, missing: MissingThumbnails) -> str:
    """Create the repair Job for these missing thumbnails and queue the one task
    that runs it. Returns the Job id. The task is imported in-function, as index_jobs does: the
    tasks package imports the automation sandbox, which imports this module."""
    from yaffo.background_tasks.tasks.regenerate_thumbnails import regenerate_thumbnails_task

    job_id = str(uuid.uuid4())
    session.add(Job(
        id=job_id,
        name=THUMBNAIL_REPAIR_JOB,
        status=JOB_STATUS_PENDING,
        task_count=missing.total,
        message='Regenerated {totalCount}/{taskCount} thumbnails',
        completed_count=0,
        error_count=0,
        cancelled_count=0,
        job_data=json.dumps({'media_item_ids': list(missing.media_item_ids)}),
    ))
    session.commit()
    regenerate_thumbnails_task(job_id)
    return job_id
