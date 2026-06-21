from pathlib import Path

from yaffo.db.models import Job, Photo, Face, JOB_STATUS_CANCELLED, FACE_STATUS_UNASSIGNED, \
    JOB_STATUS_RUNNING, JOB_STATUS_PENDING, PHOTO_STATUS_INDEXED
from yaffo.utils.index_photos import index_photo
from yaffo.domain.compare_utils import serialize_embedding
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory, get_job_status, get_current_thumbnail_dir

logger = get_logger(__name__, 'background_tasks')


@task_queue.task()
def index_photo_task(job_id: str, file_path_batch: list[str]):
    """Background task to index photos - detect faces, extract tags, etc."""
    logger.debug(f"Starting index_photo_task for job {job_id} with {len(file_path_batch)} files")
    processed_results = []
    error_count = 0
    cancel_count = 0
    check_cancel_frequency = 5
    job_status = get_job_status(job_id)
    if job_status == JOB_STATUS_CANCELLED:
        return
    thumbnail_dir = get_current_thumbnail_dir()

    for index, file_path in enumerate(file_path_batch):
        if index > 0 and index % check_cancel_frequency == 0:
            job_status = get_job_status(job_id)
            if job_status == JOB_STATUS_CANCELLED:
                logger.info(f"Job {job_id} cancelled at photo {index}/{len(file_path_batch)}")
                cancel_count = len(file_path_batch) - index
                break

        logger.debug(f"Processing photo {file_path}")
        index_results = index_photo(Path(file_path), thumbnail_dir)
        if index_results is None:
            logger.warning(f"Failed to process faces for photo {file_path}")
            error_count += 1
            continue

        processed_results.append({
            'full_file_path': file_path,
            'index_results': index_results,
        })

    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        photos_in_batch = session.query(Photo).filter(Photo.full_file_path.in_(file_path_batch)).all()
        processed_count = 0

        for result in processed_results:
            full_file_path = result["full_file_path"]
            index_results = result["index_results"]
            faces_data = index_results["faces_data"]
            latitude = index_results["latitude"]
            longitude = index_results["longitude"]
            location_name = index_results["location_name"]
            photo = next(photo for photo in photos_in_batch if photo.full_file_path == full_file_path)
            if photo is None:
                logger.error(f"Failed to find photo in db for {full_file_path}")
                error_count += 1
                continue
            photo.latitude = latitude
            photo.longitude = longitude
            photo.location_name = location_name
            photo.date_taken = index_results["date_taken"]
            photo.year = index_results["year"]
            photo.month = index_results["month"]
            photo.device = index_results["device"]
            photo.status = PHOTO_STATUS_INDEXED

            for face_data in faces_data:
                face = Face(
                    embedding=serialize_embedding(face_data['embedding']),
                    full_file_path=face_data['full_file_path'],
                    status=FACE_STATUS_UNASSIGNED,
                    photo_id=photo.id,
                    location_top=face_data['location_top'],
                    location_right=face_data['location_right'],
                    location_bottom=face_data['location_bottom'],
                    location_left=face_data['location_left'],
                    estimated_age=face_data.get('estimated_age'),
                    gender=face_data.get('gender'),
                    det_score=face_data.get('det_score'),
                )
                session.add(face)
            processed_count += 1

        update_job_params = {
            'cancelled_count': Job.cancelled_count + cancel_count,
            'completed_count': Job.completed_count + processed_count,
            'error_count': Job.error_count + error_count,
        }
        if job_status == JOB_STATUS_PENDING:
            update_job_params['status'] = JOB_STATUS_RUNNING
        session.query(Job).filter_by(id=job_id).update(update_job_params)
        session.commit()
        logger.debug(
            f"Completed job {job_id} batch: processed={processed_count}, errors={error_count}, cancelled={cancel_count}")

    except Exception as e:
        logger.error(f"Error in index_photo_task for job {job_id}: {e}", exc_info=True)
        session.rollback()
        session.query(Job).filter_by(id=job_id).update({
            'error_count': Job.error_count + len(file_path_batch)
        })
        session.commit()
    finally:
        session.close()
        SessionFactory.remove()