"""System automation `auto_assign_faces`: on a photo_indexed event, assign each
detected face to a known person it matches at or above the configured threshold.
By default, a face is left unassigned when several people clear the bar
(ambiguous), but the automation can be configured to pick the highest-scoring
match in that case.

Distinct from background_tasks.tasks.auto_assign_faces, which is the manual
'assign every matching face to ONE chosen person' batch job driven from the faces
UI -- this one matches each face against *all* people and is fired by an event.
"""
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_config import AUTOMATION_CONFIG, config_value
from yaffo.background_tasks.automation_runs import record_run
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.background_tasks.registry import register_handler
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Automation, AUTOMATION_HANDLER_AUTO_ASSIGN_FACES
from yaffo.db.repositories import person_repository, media_repository
from yaffo.db.repositories.person_repository import get_similarity_bounds
from yaffo.domain.compare_utils import calculate_face_similarity, ui_threshold_to_similarity

# The handler's config fields (see automation_config.AUTOMATION_CONFIG), by key.
_CONFIG_FIELDS = {
    field.key: field
    for field in AUTOMATION_CONFIG[AUTOMATION_HANDLER_AUTO_ASSIGN_FACES]
}
_THRESHOLD_FIELD = _CONFIG_FIELDS["threshold"]
_ASSIGN_MULTIPLE_MATCHES_FIELD = _CONFIG_FIELDS["assign_multiple_matches"]

# Photos buffered before each bulk link write. Similarity matching runs lock-free;
# the links are flushed in chunks so the write lock is taken only in short bursts.
_FLUSH_SIZE = 200


def _assign_faces(
    session: Session,
    progress_reporter: ProgressReporter,
    media_item_ids: list[int],
    threshold: float,
    assign_multiple_matches: bool = False,
) -> int:
    """For each face in the given photos, assign it to the single person it matches
    at/above `threshold`; skip faces with zero matches. Multiple strong matches are
    skipped unless `assign_multiple_matches` is true, in which case the
    highest-scoring match is used. Returns how many links were made. People are
    loaded once for the whole batch.

    Similarity matching holds no DB lock: matched (person_id, face_id) links are
    buffered and bulk-written every `_FLUSH_SIZE` photos (and once at the end)."""
    people = person_repository.get_people_with_embeddings(session)
    if not people:
        return 0

    assigned = 0
    pending: list[tuple[int, int]] = []
    photos_in_buffer = 0

    def flush():
        nonlocal assigned, photos_in_buffer
        if pending:
            assigned += person_repository.bulk_link_faces_to_people(session, pending)
            pending.clear()
        photos_in_buffer = 0

    def media_item_processor(media_item_id: int):
        nonlocal photos_in_buffer
        for face in media_repository.get_faces_for_media_item(session, media_item_id):
            strong = [
                (person_id, score)
                for person_id, score in calculate_face_similarity(face, people).items()
                if score >= threshold
            ]
            if len(strong) == 1:
                pending.append((strong[0][0], face.id))
            elif assign_multiple_matches and strong:
                person_id, _score = max(strong, key=lambda match: match[1])
                pending.append((person_id, face.id))
        photos_in_buffer += 1
        if photos_in_buffer >= _FLUSH_SIZE:
            flush()

    progress_reporter.run_with_progress(media_item_ids, media_item_processor)
    flush()  # remaining tail
    return assigned


@task_queue.task()
def auto_assign_faces_automation_task(automation_id: int, media_item_ids: list[int]):
    """Assign the faces in `media_item_ids` to their unique strong match. Enqueued by the
    auto_assign_faces system handler when a photo_indexed event fires; the threshold
    is read live from the automation's config. The run is recorded as a Job."""
    session = SessionFactory()
    try:
        automation = session.get(Automation, automation_id)
        if automation is None:
            return
        # The stored threshold is a 0-100 UI value; scale it to a cosine cutoff
        # against the live similarity band, exactly as the face screens do.
        ui_threshold = config_value(automation, _THRESHOLD_FIELD)
        threshold = ui_threshold_to_similarity(ui_threshold, *get_similarity_bounds(session))
        assign_multiple_matches = bool(config_value(automation, _ASSIGN_MULTIPLE_MATCHES_FIELD))

        def work(progress_reporter: ProgressReporter) -> str:
            assigned = _assign_faces(
                session,
                progress_reporter,
                media_item_ids,
                threshold,
                assign_multiple_matches=assign_multiple_matches,
            )
            match_policy = "allowing multiple matches" if assign_multiple_matches else "unique matches only"
            return (
                f"assigned {assigned} face(s) across {len(media_item_ids)} "
                f"photo(s) at threshold {ui_threshold} (cosine {threshold:.3f}, "
                f"{match_policy})"
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
    media_item_ids = context.media_item_ids if context else []
    if media_item_ids:
        auto_assign_faces_automation_task(automation.id, media_item_ids)
