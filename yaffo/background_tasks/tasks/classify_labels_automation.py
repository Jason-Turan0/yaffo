"""System automation `classify_labels`: on a photo_indexed event, label each photo
with the entries from the user's vocabulary it matches, using offline zero-shot
CLIP (utils.image_classifier). Enqueued per newly-indexed photo by the handler, and
over the whole library by the Settings "re-classify all" backfill. The CLIP cosine
threshold and per-photo label cap are read live from the automation's config.

Mirrors auto_assign_faces_automation: a thin handler that enqueues a task, the task
reads config, does the work inside record_run (Job history), and closes its session.
"""
from pathlib import Path

import numpy as np
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_config import AUTOMATION_CONFIG, config_value
from yaffo.background_tasks.automation_runs import record_run
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import EventContext, emit_event, event_chain_scope
from yaffo.background_tasks.progress_reporter import ProgressReporter
from yaffo.background_tasks.registry import register_handler
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import (
    Automation,
    AUTOMATION_HANDLER_CLASSIFY_LABELS,
    CLASSIFY_LABELS_DEFAULT_THRESHOLD,
    EVENT_PHOTO_LABELED,
)
from yaffo.db.repositories import classification_repository, photos_repository
from yaffo.logging_config import get_logger
from yaffo.utils.image import image_from_path, image_to_numpy
from yaffo.utils.image_classifier import embed_image, embed_texts, get_clip_threshold

logger = get_logger(__name__, "background_tasks")

_FIELDS = {field.key: field for field in AUTOMATION_CONFIG[AUTOMATION_HANDLER_CLASSIFY_LABELS]}


def _classify_photos(session: Session, progress_reporter: ProgressReporter, photo_ids: list[int], threshold: float,
                     max_labels: int) -> list[int]:
    """Label each photo with the vocabulary entries scoring >= threshold (cosine),
    keeping the top `max_labels`. Replaces each photo's prior labels. Returns the ids
    of the photos that received at least one label. Label embeddings are computed once
    for the whole batch."""
    labels = classification_repository.get_enabled_labels(session)
    if not labels:
        return []
    label_embeddings = embed_texts([label.effective_prompt for label in labels])
    paths = photos_repository.get_paths_by_ids(session, photo_ids)

    labeled: list[int] = []
    def photo_processor (photo_id: int):
        path = paths.get(photo_id)
        if not path:
            return
        try:
            image = image_to_numpy(image_from_path(Path(path)))
        except Exception as e:
            logger.warning(f"classify_labels: could not load photo {photo_id} ({path}): {e}")
            return
        # float32 BLAS matmul can emit spurious divide/overflow RuntimeWarnings on
        # some platforms even though the result is finite (verified in the spike);
        # the inputs are guarded for finiteness upstream, so silence the noise.
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            sims = label_embeddings @ embed_image(image)
        order = np.argsort(-sims)[:max_labels]
        assignments = [(labels[i].id, float(sims[i])) for i in order if sims[i] >= threshold]
        classification_repository.replace_photo_labels(session, photo_id, assignments)
        if assignments:
            labeled.append(photo_id)
    progress_reporter.run_with_progress(photo_ids, photo_processor)
    return labeled


@task_queue.task()
def classify_labels_automation_task(
        automation_id: int, photo_ids: list[int], origin_automation_ids: list[int] | None = None
):
    """Label `photo_ids` against the enabled vocabulary. Enqueued by the handler on a
    photo_indexed event (the new photos) or by the Settings backfill (every indexed
    photo). The run is recorded as a Job. `origin_automation_ids` is the loop guard's
    causal chain threaded from the triggering event."""
    session = SessionFactory()
    try:
        automation = session.get(Automation, automation_id)
        if automation is None:
            return
        ui_threshold = int(
            config_value(automation, _FIELDS["confidence_threshold"])) or CLASSIFY_LABELS_DEFAULT_THRESHOLD
        threshold = get_clip_threshold(ui_threshold)
        max_labels = int(config_value(automation, _FIELDS["max_labels"]))

        labeled: list[int] = []

        def work(progress_reporter: ProgressReporter) -> str:
            nonlocal labeled
            labeled = _classify_photos(session, progress_reporter, photo_ids, threshold, max_labels)
            return (
                f"labeled {len(labeled)} of {len(photo_ids)} photo(s) "
                f"at threshold {threshold:.2f} (max {max_labels} each)"
            )

        # Scope the run so the photo_labeled it emits carries this automation (loop guard).
        with event_chain_scope(origin_automation_ids, automation_id):
            record_run(session, automation, work)
            # Emit after record_run so the labels are committed before subscribers run
            # (record_run commits work's writes); fire only when something was labeled.
            if labeled:
                emit_event(EVENT_PHOTO_LABELED, {"photo_ids": labeled})
    finally:
        session.close()
        SessionFactory.remove()


@register_handler(AUTOMATION_HANDLER_CLASSIFY_LABELS)
def enqueue_classify_labels(automation: Automation, context: EventContext | None = None) -> None:
    """Handler for the built-in classify-labels automation: enqueue the task for the
    photos the triggering event concerns. A schedule trigger (no context) is a no-op."""
    photo_ids = context.photo_ids if context else []
    if photo_ids:
        origin = context.origin_automation_ids if context else []
        classify_labels_automation_task(automation.id, photo_ids, origin)
