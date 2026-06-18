"""System automation `export_photo_tag`: when a photo is modified (a face assigned
or removed, a person renamed, a location set), write the photo's people and/or
location tags back into the file's metadata so the on-disk file stays in sync with
the index.

Two independent toggles in the automation's config decide what gets written:
`export_location_tag_enabled` (the photo's `location_name`) and
`export_people_tag_enabled` (the names of people linked to the photo's faces). With
neither enabled the run is a no-op. Fired by the `photo_modified` event with the
affected photo ids; the actual write reuses `utils.write_metadata`.
"""
from pathlib import Path

from sqlalchemy.orm import Session, joinedload

from yaffo.background_tasks.automation_config import AUTOMATION_CONFIG, config_value
from yaffo.background_tasks.automation_runs import record_run
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.registry import register_handler
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Automation, Face, Photo, AUTOMATION_HANDLER_EXPORT_PHOTO_TAG
from yaffo.logging_config import get_logger
from yaffo.utils.write_metadata import write_photo_metadata

logger = get_logger(__name__, 'background_tasks')

# The handler's config fields (see automation_config.AUTOMATION_CONFIG), by key.
_FIELDS = {f.key: f for f in AUTOMATION_CONFIG[AUTOMATION_HANDLER_EXPORT_PHOTO_TAG]}
_LOCATION_FIELD = _FIELDS["export_location_tag_enabled"]
_PEOPLE_FIELD = _FIELDS["export_people_tag_enabled"]


def _people_names(photo: Photo) -> list[str]:
    """Distinct names of people linked to any face in the photo."""
    names = {
        person.name
        for face in photo.faces
        for person in face.people
        if person.name
    }
    return sorted(names)


def _export_tags(
    session: Session,
    photo_ids: list[int],
    export_location: bool,
    export_people: bool,
) -> int:
    """Write the enabled tag(s) into each photo's file metadata. Returns how many
    files were written. Photos whose file is missing are skipped."""
    if not photo_ids or not (export_location or export_people):
        return 0
    photos = (
        session.query(Photo)
        .options(joinedload(Photo.faces).joinedload(Face.people))
        .filter(Photo.id.in_(photo_ids))
        .all()
    )
    written = 0
    for photo in photos:
        photo_path = Path(photo.full_file_path)
        if not photo_path.exists():
            logger.warning(f"export_photo_tag: file not found, skipping {photo_path}")
            continue
        people = _people_names(photo) if export_people else None
        success, error = write_photo_metadata(
            photo_path=photo_path,
            location_name=photo.location_name if export_location else None,
            people_names=people or None,
        )
        if success:
            written += 1
        else:
            logger.warning(f"export_photo_tag: failed to write {photo_path}: {error}")
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

        def work() -> str:
            written = _export_tags(session, photo_ids, export_location, export_people)
            return (
                f"wrote metadata to {written}/{len(photo_ids)} file(s) "
                f"(location={export_location}, people={export_people})"
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
    photo_ids = context.photo_ids if context else []
    if photo_ids:
        export_photo_tag_task(automation.id, photo_ids)
