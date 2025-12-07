import json

from yaffo.db.models import Job, JobResult, JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING, JOB_STATUS_PENDING
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.utils import SessionFactory, get_job_status, load_assign_faces_task_data
from yaffo.domain.compare_utils import calculate_similarity

logger = get_logger(__name__, 'background_tasks')


@huey.task(context=True)
def auto_assign_faces_task(job_id: str, face_id_batch: list[int], person_id: int, similarity_threshold: float,
                           task=None):
    """Huey task to auto-assign faces to a person based on similarity threshold."""
    logger.info(f"Starting auto_assign_faces_task for job {job_id} with {len(face_id_batch)} faces. task = {task}")
    error_count = 0
    job_status = get_job_status(job_id)
    if job_status == JOB_STATUS_CANCELLED:
        return

    person, faces = load_assign_faces_task_data(person_id, face_id_batch)
    similarities = calculate_similarity(person, faces)
    matches = [
        {'face_id': face_id, 'similarity': similarity}
        for face_id, similarity in similarities.items()
        if similarity >= similarity_threshold
    ]
    processed_count = len(face_id_batch)

    session = SessionFactory()
    try:
        job_result = JobResult(job_id=job_id, huey_task_id=task.id, result_data=json.dumps({'matches': matches}))
        session.add(job_result)
        update_job_params = {
            'error_count': Job.error_count + error_count,
            'completed_count': Job.completed_count + processed_count,
        }
        if job_status == JOB_STATUS_PENDING:
            update_job_params['status'] = JOB_STATUS_RUNNING

        session.query(Job).filter_by(id=job_id).update(update_job_params)
        session.commit()
        logger.info(f"Completed job {job_id} batch: processed={len(face_id_batch)}, matches={len(matches)}")

    except Exception as e:
        logger.error(f"Error in auto_assign_faces_task for job {job_id}: {e}", exc_info=True)
        session.rollback()
        session.query(Job).filter_by(id=job_id).update({
            'error_count': Job.error_count + len(face_id_batch)
        })
        session.commit()
    finally:
        session.close()
        SessionFactory.remove()