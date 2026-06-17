"""System automation `auto_assign_faces`: on a photo_indexed event, assign each
detected face to the one known person it matches at or above the configured
threshold, leaving a face unassigned when several people clear the bar (ambiguous)
or none do. This is the code-backed twin of the former seeded Starlark example --
the behaviour is identical (match each face to all people, assign only on a unique
strong match); the threshold is read from the automation's config.

Distinct from background_tasks.tasks.auto_assign_faces, which is the manual
'assign every matching face to ONE chosen person' batch job driven from the faces
UI -- this one matches each face against *all* people and is fired by an event.
"""
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_config import AUTOMATION_CONFIG, config_value
from yaffo.background_tasks.automation_runs import record_run
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.registry import register_handler
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Automation, AUTOMATION_HANDLER_AUTO_ASSIGN_FACES
from yaffo.db.repositories import person_repository, photos_repository
from yaffo.domain.compare_utils import calculate_face_similarity

# The handler's lone config field (see automation_config.AUTOMATION_CONFIG).
_THRESHOLD_FIELD = AUTOMATION_CONFIG[AUTOMATION_HANDLER_AUTO_ASSIGN_FACES][0]


def _assign_faces(session: Session, photo_ids: list[int], threshold: float) -> int:
    """For each face in the given photos, assign it to the single person it matches
    at/above `threshold`; skip faces with zero or multiple strong matches. Returns
    how many links were made. People are loaded once for the whole batch."""
    people = person_repository.get_people_with_embeddings(session)
    if not people:
        return 0
    assigned = 0
    for photo_id in photo_ids:
        for face in photos_repository.get_faces_for_photo(session, photo_id):
            strong = [
                person_id
                for person_id, score in calculate_face_similarity(face, people).items()
                if score >= threshold
            ]
            if len(strong) == 1 and person_repository.link_face_to_person(session, strong[0], face.id):
                assigned += 1
    return assigned


@huey.task()
def auto_assign_faces_automation_task(automation_id: int, photo_ids: list[int]):
    """Assign the faces in `photo_ids` to their unique strong match. Enqueued by the
    auto_assign_faces system handler when a photo_indexed event fires; the threshold
    is read live from the automation's config. The run is recorded as a Job."""
    session = SessionFactory()
    try:
        automation = session.get(Automation, automation_id)
        if automation is None:
            return
        threshold = config_value(automation, _THRESHOLD_FIELD)

        def work() -> str:
            assigned = _assign_faces(session, photo_ids, threshold)
            return (
                f"assigned {assigned} face(s) across {len(photo_ids)} "
                f"photo(s) at threshold {threshold}"
            )

        record_run(session, automation, work)
    finally:
        session.close()
        SessionFactory.remove()


@register_handler(AUTOMATION_HANDLER_AUTO_ASSIGN_FACES)
def enqueue_auto_assign_faces(automation: Automation, context: EventContext | None = None) -> None:
    """Handler for the built-in auto-assign-faces automation: enqueue the task for
    the photos the triggering event concerns. A schedule trigger (no context, no
    photo subjects) has nothing to act on, so it's a no-op."""
    photo_ids = context.photo_ids if context else []
    if photo_ids:
        auto_assign_faces_automation_task(automation.id, photo_ids)
