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
    EVENT_MEDIA_LABELED,
    MEDIA_TYPE_VIDEO,
)
from yaffo.db.repositories import classification_repository, media_repository
from yaffo.logging_config import get_logger
from yaffo.utils.image import image_from_path, image_to_numpy
from yaffo.utils.image_classifier import embed_image, embed_texts, get_clip_threshold
from yaffo.utils.index_video import iter_video_frame_arrays

logger = get_logger(__name__, "background_tasks")

_FIELDS = {field.key: field for field in AUTOMATION_CONFIG[AUTOMATION_HANDLER_CLASSIFY_LABELS]}


# Photos buffered before each bulk write. Inference (the slow part) runs lock-free;
# results are flushed in chunks of this size so the write lock is taken only in short
# bursts, partial progress is durable, and labels appear progressively in the UI.
_FLUSH_SIZE = 200


def classify_media_items(
        session: Session,
        media_item_ids: list[int],
        threshold: float,
        max_labels: int,
        progress_reporter: ProgressReporter | None = None,
) -> list[int]:
    """Label each photo with the vocabulary entries scoring >= threshold (cosine),
    keeping the top `max_labels`. Replaces each photo's prior labels. Returns the ids
    of the photos that received at least one label.

    CLIP inference holds no DB lock: each photo's assignments are computed into an
    in-memory buffer, then flushed to the DB in bulk every `_FLUSH_SIZE` photos (and
    once at the end). Label embeddings are computed once for the whole batch."""
    labels = classification_repository.get_enabled_labels(session)
    if not labels:
        return []
    try:
        label_embeddings = embed_texts([label.effective_prompt for label in labels])
    except Exception as e:  # noqa: BLE001 - missing/broken CLIP should make this run a no-op
        logger.warning("classify_labels: CLIP unavailable; skipping classification: %s", e)
        return []
    targets = media_repository.get_label_inputs_by_ids(session, media_item_ids)

    def _video_label_sims(path: str, duration):
        """Per-label scores for a video: the element-wise max over its sampled
        frames, so a concept appearing in any frame counts. None if no frame was
        extractable (e.g. ffmpeg unavailable)."""
        agg = None
        for frame in iter_video_frame_arrays(Path(path), duration):
            sims = label_embeddings @ embed_image(frame)
            agg = sims if agg is None else np.maximum(agg, sims)
        return agg

    labeled: list[int] = []
    pending: list[tuple[int, list[tuple[int, float]]]] = []

    def flush():
        if pending:
            classification_repository.bulk_replace_media_labels(session, pending)
            pending.clear()

    def media_item_processor(media_item_id: int):
        target = targets.get(media_item_id)
        if not target:
            return
        path, media_type, duration = target
        # float32 BLAS matmul can emit spurious divide/overflow RuntimeWarnings on
        # some platforms even though the result is finite (verified in the spike);
        # the inputs are guarded for finiteness upstream, so silence the noise.
        try:
            with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
                if media_type == MEDIA_TYPE_VIDEO:
                    # Label a video from sampled frames (poster + others), aggregated.
                    sims = _video_label_sims(path, duration)
                else:
                    sims = label_embeddings @ embed_image(image_to_numpy(image_from_path(Path(path))))
        except Exception as e:
            logger.warning(f"classify_labels: could not load media {media_item_id} ({path}): {e}")
            return
        if sims is None:
            return  # video with no extractable frames (e.g. ffmpeg unavailable)
        order = np.argsort(-sims)[:max_labels]
        assignments = [(labels[i].id, float(sims[i])) for i in order if sims[i] >= threshold]
        pending.append((media_item_id, assignments))
        if assignments:
            labeled.append(media_item_id)
        if len(pending) >= _FLUSH_SIZE:
            flush()

    if progress_reporter is None:
        for media_item_id in media_item_ids:
            media_item_processor(media_item_id)
    else:
        progress_reporter.run_with_progress(media_item_ids, media_item_processor)
    flush()  # remaining tail
    return labeled


def _classify_media_items(
        session: Session,
        progress_reporter: ProgressReporter,
        media_item_ids: list[int],
        threshold: float,
        max_labels: int,
) -> list[int]:
    return classify_media_items(
        session,
        media_item_ids,
        threshold,
        max_labels,
        progress_reporter,
    )


@task_queue.task()
def classify_labels_automation_task(
        automation_id: int, media_item_ids: list[int], origin_automation_ids: list[int] | None = None
):
    """Label `media_item_ids` against the enabled vocabulary. Enqueued by the handler on a
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
            labeled = classify_media_items(
                session,
                media_item_ids,
                threshold,
                max_labels,
                progress_reporter,
            )
            return (
                f"labeled {len(labeled)} of {len(media_item_ids)} photo(s) "
                f"at threshold {threshold:.2f} (max {max_labels} each)"
            )

        # Scope the run so the photo_labeled it emits carries this automation (loop guard).
        with event_chain_scope(origin_automation_ids, automation_id):
            record_run(session, automation, work)
            # Emit after record_run so the labels are committed before subscribers run
            # (record_run commits work's writes); fire only when something was labeled.
            if labeled:
                emit_event(EVENT_MEDIA_LABELED, {"media_item_ids": labeled})
    finally:
        session.close()
        SessionFactory.remove()


@register_handler(AUTOMATION_HANDLER_CLASSIFY_LABELS)
def enqueue_classify_labels(automation: Automation, context: EventContext | None = None) -> None:
    """Handler for the built-in classify-labels automation: enqueue the task for the
    photos the triggering event concerns. A schedule trigger (no context) is a no-op."""
    media_item_ids = context.media_item_ids if context else []
    if media_item_ids:
        origin = context.origin_automation_ids if context else []
        classify_labels_automation_task(automation.id, media_item_ids, origin)
