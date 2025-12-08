from pathlib import Path

from yaffo.db.models import Job, Photo, JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING, JOB_STATUS_PENDING
from yaffo.utils.index_photos import import_photo
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.utils import SessionFactory, get_job_status

logger = get_logger(__name__, 'background_tasks')


@huey.task()
def import_photo_task(job_id: str, file_path_batch: list[str]):
    """
    Huey task to import photos - create photos in database.
    Supports graceful cancellation and crash recovery.
    """
    logger.info(f"Starting import_photo_task for job {job_id} with {len(file_path_batch)} files")
    processed_results = []
    error_count = 0
    cancel_count = 0
    check_cancel_frequency = 5
    job_status = get_job_status(job_id)
    if job_status == JOB_STATUS_CANCELLED:
        return

    for index, file_path in enumerate(file_path_batch):
        if index % check_cancel_frequency == 0:
            job_status = get_job_status(job_id)
            if job_status == JOB_STATUS_CANCELLED:
                cancel_count = len(file_path_batch) - index
                logger.info(f"Job {job_id} cancelled at photo {index}/{len(file_path_batch)}")
                break

        logger.debug(f"Importing photo {file_path}")
        result = import_photo(Path(file_path))
        if result is None:
            logger.warning(f"Failed to process photo {file_path}")
            error_count += 1
            continue
        processed_results.append(result)

    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        for result in processed_results:
            photo = Photo(
                full_file_path=result["full_file_path"],
                date_taken=result["date_taken"],
                latitude=result.get("latitude"),
                longitude=result.get("longitude"),
                location_name=result.get("location_name")
            )
            session.add(photo)
            session.flush()
        processed_count = len(processed_results)
        update_job_params = {
            'completed_count': Job.completed_count + processed_count,
            'cancelled_count': Job.cancelled_count + cancel_count,
            'error_count': Job.error_count + error_count,
        }
        if job_status == JOB_STATUS_PENDING:
            update_job_params['status'] = JOB_STATUS_RUNNING

        session.query(Job).filter_by(id=job_id).update(update_job_params)
        session.commit()
        logger.info(
            f"Completed job {job_id} batch: processed={processed_count}, errors={error_count}, cancelled={cancel_count}")

    except Exception as e:
        logger.error(f"Error in import_photo_task for job {job_id}: {e}", exc_info=True)
        session.rollback()
        session.query(Job).filter_by(id=job_id).update({
            'error_count': Job.error_count + len(file_path_batch)
        })
        session.commit()
    finally:
        session.close()
        SessionFactory.remove()