import json
from typing import Any

from sqlalchemy.orm import Session

from yaffo.db.models import Job, MediaItem, JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING
from yaffo.db.repositories.job_repository import is_job_cancelled, mark_job_stopped, refresh_estimated_completion
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.tasks.complete_job import finalize_job
from yaffo.background_tasks.utils import SessionFactory
from yaffo.utils.job_codes import OUTCOME_REGENERATED, load_job_data
from yaffo.utils.thumbnail_repair import missing_files_per_item, repair_item_thumbnails
from yaffo.utils.time import utcnow

logger = get_logger(__name__, 'background_tasks')

# Items between progress writes (and cancellation checks). Each item is a photo
# decode or an ffmpeg frame grab, so a tick is a few seconds of work.
PROGRESS_EVERY = 20
# English failure reasons kept on Job.error, for Details and the assistant.
MAX_RECORDED_FAILURES = 20


@task_queue.task()
def regenerate_thumbnails_task(job_id: str) -> None:
    """Rebuild the missing face crops and posters of the Job's items, one item at a
    time in this one task. Progress counts files: each one written is completed,
    each one that couldn't be is an error. Replay-safe: files that exist are
    skipped, so a requeued run only finishes what's left."""
    session = SessionFactory()
    try:
        job = session.get(Job, job_id)
        if job is None:
            logger.error(f"Thumbnail repair job {job_id} not found")
            return
        if job.status == JOB_STATUS_CANCELLED:
            mark_job_stopped(session, job_id)
            session.commit()
            return
        data = load_job_data(job.job_data)
        media_item_ids = data.get('media_item_ids', [])
        # Progress counts thumbnail files, recounted now: some may have come back
        # since the job was queued. A requeued run starts over the same way.
        missing = missing_files_per_item(session, media_item_ids)
        job.status = JOB_STATUS_RUNNING
        job.started_at = job.started_at or utcnow()
        job.task_count = sum(missing.values())
        job.completed_count = job.error_count = job.cancelled_count = 0
        session.commit()

        failures: list[str] = []
        completed = errors = written = failed = 0
        cancelled = False
        for index, media_item_id in enumerate(media_item_ids):
            if index and index % PROGRESS_EVERY == 0:
                _write_progress(session, job_id, completed, errors, failures)
                completed = errors = 0
                if is_job_cancelled(session, job_id):
                    left = sum(missing[i] for i in media_item_ids[index:])
                    session.query(Job).filter_by(id=job_id).update({'cancelled_count': left})
                    session.commit()
                    logger.info(f"Thumbnail repair {job_id} cancelled at item {index}/{len(media_item_ids)}")
                    cancelled = True
                    break
            expected = missing.get(media_item_id, 0)
            if not expected:
                continue
            media_item = session.get(MediaItem, media_item_id)
            if media_item is None:  # deleted since the recount; its faces went with it
                continue
            try:
                repair = repair_item_thumbnails(session, media_item)
                item_failed = min(len(repair.failures), expected)
                failures.extend(repair.failures)
            except Exception as e:  # noqa: BLE001 - one item mustn't stop the rest
                logger.error(f"Thumbnail repair failed for media item {media_item_id}: {e}", exc_info=True)
                item_failed = expected
                failures.append(f"media item {media_item_id}: {e}")
            # Every file counted for the item is now either there or failed.
            completed += expected - item_failed
            errors += item_failed
            written += expected - item_failed
            failed += item_failed
        outcome = None if cancelled else {'outcome': OUTCOME_REGENERATED, 'written': written, 'total': written + failed}
        _write_progress(session, job_id, completed, errors, failures, outcome)
    finally:
        session.close()
        SessionFactory.remove()
    finalize_job(job_id)


def _write_progress(session: Session, job_id: str, completed: int, errors: int, failures: list[str],
                    outcome: dict[str, Any] | None = None) -> None:
    values: dict[Any, Any] = {
        'completed_count': Job.completed_count + completed,
        'error_count': Job.error_count + errors,
    }
    if failures:
        values['error'] = "\n".join(failures[:MAX_RECORDED_FAILURES])
    if outcome is not None:
        job = session.get(Job, job_id)
        data = load_job_data(job.job_data if job is not None else None)
        data.update(outcome)
        values['job_data'] = json.dumps(data)
    session.query(Job).filter_by(id=job_id).update(values)
    refresh_estimated_completion(session, job_id)
    session.commit()
