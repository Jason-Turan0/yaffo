"""System automation `export_photo_tag`: when a photo is modified (a face assigned
or removed, a person renamed, a location set), write the photo's tags back into the
file's metadata so the on-disk file stays in sync with the index.

Independent toggles in the automation's config decide what gets written:
`export_location_tag_enabled` (the photo's `location_name`), `export_people_tag_enabled`
(the names of people linked to the photo's faces), `export_labels_enabled` (its
classification labels), `export_custom_tags_enabled` (its manual name/value tags), and
`export_favorite_enabled` (a "Favorite" keyword, written only when the photo is
favorited). Labels, custom tags, and the favorite keyword are written into the file's
keyword fields. With none enabled the run is a no-op. Fired by the `photo_modified`
event with the affected photo ids; the actual write reuses `utils.write_metadata`.
"""
from pathlib import Path

from sqlalchemy.orm import Session, joinedload

from yaffo.background_tasks.automation_config import AUTOMATION_CONFIG, config_value
from yaffo.background_tasks.automation_runs import record_run
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.background_tasks.registry import register_handler
from yaffo.background_tasks.utils import SessionFactory
from yaffo.background_tasks.watcher_suppression import record_self_write
from yaffo.db.models import Automation, Face, MediaItem, MediaLabel, AUTOMATION_HANDLER_EXPORT_PHOTO_TAG
from yaffo.logging_config import get_logger
from yaffo.utils.write_metadata import write_photo_metadata

logger = get_logger(__name__, 'background_tasks')

# The handler's config fields (see automation_config.AUTOMATION_CONFIG), by key.
_FIELDS = {f.key: f for f in AUTOMATION_CONFIG[AUTOMATION_HANDLER_EXPORT_PHOTO_TAG]}
_LOCATION_FIELD = _FIELDS["export_location_tag_enabled"]
_PEOPLE_FIELD = _FIELDS["export_people_tag_enabled"]
_LABELS_FIELD = _FIELDS["export_labels_enabled"]
_CUSTOM_TAGS_FIELD = _FIELDS["export_custom_tags_enabled"]
_FAVORITE_FIELD = _FIELDS["export_favorite_enabled"]

# The keyword written into the file when a photo is favorited.
FAVORITE_KEYWORD = "Favorite"

# Photo ids per load query — stay under SQLite's ~999 bound-param cap and bound the
# joinedload's memory for a large batch.
_LOAD_CHUNK = 500


def _people_names(photo: MediaItem) -> list[str]:
    """Distinct names of people linked to any face in the photo."""
    names = {
        person.name
        for face in photo.faces
        for person in face.people
        if person.name
    }
    return sorted(names)


def _label_names(photo: MediaItem) -> list[str]:
    """Distinct classification-label names assigned to the photo."""
    return sorted({pl.label.name for pl in photo.labels if pl.label and pl.label.name})


def _custom_tag_keywords(photo: MediaItem) -> list[str]:
    """The photo's manual tags as keyword strings — 'name: value', or just 'name'
    when the tag has no value."""
    return sorted({
        f"{tag.tag_name}: {tag.tag_value}" if tag.tag_value else tag.tag_name
        for tag in photo.tags
        if tag.tag_name
    })


def _export_tags(
    session: Session,
    progress_reporter: ProgressReporter,
    photo_ids: list[int],
    export_location: bool,
    export_people: bool,
    export_labels: bool = False,
    export_custom_tags: bool = False,
    export_favorite: bool = False,
) -> int:
    """Write the enabled tag(s) into each photo's file metadata. Returns how many
    files were written. Photos whose file is missing are skipped.

    The work is file I/O (exiftool), not a DB write, so no lock is held while writing;
    the photos are *loaded* in chunks (one IN-clause per chunk) to stay under SQLite's
    bound-param cap and bound the joinedload's memory."""
    if not photo_ids or not (export_location or export_people or export_labels or export_custom_tags or export_favorite):
        return 0
    photos = []
    for start in range(0, len(photo_ids), _LOAD_CHUNK):
        chunk = photo_ids[start:start + _LOAD_CHUNK]
        photos.extend(
            session.query(MediaItem)
            .options(
                joinedload(MediaItem.faces).joinedload(Face.people),
                joinedload(MediaItem.labels).joinedload(MediaLabel.label),
                joinedload(MediaItem.tags),
            )
            .filter(MediaItem.id.in_(chunk))
            .all()
        )
    written = 0
    def photo_processor(photo: MediaItem):
        nonlocal written
        photo_path = Path(photo.full_file_path)
        if not photo_path.exists():
            logger.warning(f"export_photo_tag: file not found, skipping {photo_path}")
            return
        people = _people_names(photo) if export_people else None
        keywords = []
        if export_labels:
            keywords += _label_names(photo)
        if export_custom_tags:
            keywords += _custom_tag_keywords(photo)
        if export_favorite and photo.favorite:
            keywords.append(FAVORITE_KEYWORD)
        success, error = write_photo_metadata(
            photo_path=photo_path,
            location_name=photo.location_name if export_location else None,
            people_names=people or None,
            keywords=keywords or None,
        )
        if success:
            written += 1
            # We just wrote a watched file; record it so the watcher doesn't re-index it
            # and loop back through photo_indexed (loop guard, Mechanism 2).
            record_self_write(photo_path)
        else:
            logger.warning(f"export_photo_tag: failed to write {photo_path}: {error}")
    progress_reporter.run_with_progress(photos, photo_processor)
    return written


@task_queue.task()
def export_photo_tag_task(automation_id: int, photo_ids: list[int]):
    """Write people/location tags to the given photos' files. Enqueued by the
    export_photo_tag handler on a photo_modified event; which tags to write is read
    live from the automation's config. The run is recorded as a Job."""
    session = SessionFactory()
    try:
        automation = session.get(Automation, automation_id)
        if automation is None:
            return
        export_location = bool(config_value(automation, _LOCATION_FIELD))
        export_people = bool(config_value(automation, _PEOPLE_FIELD))
        export_labels = bool(config_value(automation, _LABELS_FIELD))
        export_custom_tags = bool(config_value(automation, _CUSTOM_TAGS_FIELD))
        export_favorite = bool(config_value(automation, _FAVORITE_FIELD))

        def work(progress_reporter: ProgressReporter) -> str:
            written = _export_tags(
                session, progress_reporter, photo_ids, export_location, export_people,
                export_labels, export_custom_tags, export_favorite,
            )
            return (
                f"wrote metadata to {written}/{len(photo_ids)} file(s) "
                f"(location={export_location}, people={export_people}, "
                f"labels={export_labels}, custom_tags={export_custom_tags}, "
                f"favorite={export_favorite})"
            )

        record_run(session, automation, work)
    finally:
        session.close()
        SessionFactory.remove()


@register_handler(AUTOMATION_HANDLER_EXPORT_PHOTO_TAG)
def enqueue_export_photo_tag(automation: Automation, context: EventContext | None = None) -> None:
    """Handler for the built-in export-photo-tag automation: enqueue the write for
    the photos the triggering event concerns. A schedule trigger (no context, no
    photo subjects) has nothing to act on, so it's a no-op."""
    photo_ids = context.media_ids if context else []
    if photo_ids:
        export_photo_tag_task(automation.id, photo_ids)
