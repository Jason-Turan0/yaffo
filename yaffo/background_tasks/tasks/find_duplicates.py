from pathlib import Path
import json
from collections import defaultdict
import imagehash

from yaffo.db.models import Job, JobResult, MediaItem, JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING, JOB_STATUS_PENDING, \
    JOB_STATUS_COMPLETED, EVENT_DUPLICATES_FOUND
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import emit_event
from yaffo.background_tasks.utils import SessionFactory, get_job_status
from yaffo.utils.image import image_from_path

logger = get_logger(__name__, 'background_tasks')


def _resolve_group_photo_ids(session, duplicate_groups: list[dict]) -> list[list[int]]:
    """Map each duplicate group's file paths to photo ids (dropping any not indexed),
    sorted earliest-indexed first (ascending id) so the keeper is element [0]. Groups
    left with fewer than two indexed photos are dropped. Keeps paths out of the event
    payload — the sandbox only ever sees photo ids."""
    all_paths = [p for group in duplicate_groups for p in group.get('paths', [])]
    if not all_paths:
        return []
    rows = session.query(MediaItem.id, MediaItem.full_file_path).filter(MediaItem.full_file_path.in_(all_paths)).all()
    path_to_id = {path: pid for pid, path in rows}
    resolved = []
    for group in duplicate_groups:
        ids = sorted({path_to_id[p] for p in group.get('paths', []) if p in path_to_id})
        if len(ids) > 1:
            resolved.append(ids)
    return resolved


@task_queue.task(context=True)
def find_duplicates_task(job_id: str, file_paths: list[str], task=None):
    """Background task to find duplicate photos using perceptual hashing."""
    logger.debug(f"Starting find_duplicates_task for job {job_id} with {len(file_paths)} files")

    check_cancel_frequency = 10
    hashes = defaultdict(list)
    processed_count = 0
    error_count = 0
    cancel_count = 0

    job_status = get_job_status(job_id)
    if job_status == JOB_STATUS_CANCELLED:
        return

    session = SessionFactory()
    try:
        if job_status == JOB_STATUS_PENDING:
            session.query(Job).filter_by(id=job_id).update({'status': JOB_STATUS_RUNNING})
            session.commit()
    except Exception as e:
        logger.error(f"Error updating job status: {e}")
        session.rollback()
    finally:
        session.close()
        SessionFactory.remove()

    for index, file_path in enumerate(file_paths):
        if index > 0 and index % check_cancel_frequency == 0:
            job_status = get_job_status(job_id)
            if job_status == JOB_STATUS_CANCELLED:
                logger.info(f"Job {job_id} cancelled at file {index}/{len(file_paths)}")
                cancel_count = len(file_paths) - index
                break

        try:
            image = image_from_path(Path(file_path))
            hash_value = imagehash.phash(image)
            hashes[str(hash_value)].append(file_path)
            processed_count += 1
        except Exception as e:
            logger.warning(f"Failed to hash {file_path}: {e}")
            error_count += 1

        if (index + 1) % 50 == 0:
            session = SessionFactory()
            try:
                session.query(Job).filter_by(id=job_id).update({
                    'completed_count': processed_count,
                    'error_count': error_count,
                })
                session.commit()
            except Exception as e:
                logger.error(f"Error updating job progress: {e}")
                session.rollback()
            finally:
                session.close()
                SessionFactory.remove()

    duplicate_groups = []
    group_id = 0
    for hash_value, paths in hashes.items():
        if len(paths) > 1:
            duplicate_groups.append({
                'id': group_id,
                'paths': paths
            })
            group_id += 1

    session = SessionFactory()
    event_groups: list[list[int]] = []
    try:
        if duplicate_groups:
            job_result = JobResult(
                job_id=job_id,
                task_id=task.id,
                result_data=json.dumps(duplicate_groups)
            )
            session.add(job_result)

        session.query(Job).filter_by(id=job_id).update({
            'completed_count': processed_count,
            'error_count': error_count,
            'cancelled_count': cancel_count,
            'status': JOB_STATUS_COMPLETED
        })
        session.commit()
        if duplicate_groups:
            event_groups = _resolve_group_photo_ids(session, duplicate_groups)
        logger.debug(
            f"Completed job {job_id}: processed={processed_count}, errors={error_count}, "
            f"cancelled={cancel_count}, duplicate_groups={len(duplicate_groups)}"
        )

    except Exception as e:
        logger.error(f"Error in find_duplicates_task for job {job_id}: {e}", exc_info=True)
        session.rollback()
    finally:
        session.close()
        SessionFactory.remove()

    # Fire the domain event so subscribed automations (e.g. move-duplicates) can act
    # on the groups. photo_ids is the flattened set; groups keeps the per-set
    # structure with the keeper first.
    if event_groups:
        photo_ids = sorted({pid for group in event_groups for pid in group})
        emit_event(EVENT_DUPLICATES_FOUND, {"job_id": job_id, "media_ids": photo_ids, "groups": event_groups})