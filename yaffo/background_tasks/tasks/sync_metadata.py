from pathlib import Path
from sqlalchemy.orm import joinedload

from yaffo.db.models import Job, Photo, Face, JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING, JOB_STATUS_PENDING, \
    PHOTO_STATUS_SYNCED
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.utils import SessionFactory, get_job_status

logger = get_logger(__name__, 'background_tasks')


@huey.task()
def sync_metadata_task(job_id: str, photo_id_batch: list[int]):
    """Huey task to sync metadata to photo files."""
    from yaffo.utils.write_metadata import write_photo_metadata

    logger.info(f"Starting sync_metadata_task for job {job_id} with {len(photo_id_batch)} photos")
    processed_count = 0
    error_count = 0
    cancel_count = 0
    check_cancel_frequency = 5

    job_status = get_job_status(job_id)
    if job_status == JOB_STATUS_CANCELLED:
        return

    session = SessionFactory()
    try:
        photos = session.query(Photo).options(
            joinedload(Photo.faces).joinedload(Face.people)
        ).filter(Photo.id.in_(photo_id_batch)).all()
        success_photo_ids = []

        for index, photo in enumerate(photos):
            if index > 0 and index % check_cancel_frequency == 0:
                job_status = get_job_status(job_id)
                if job_status == JOB_STATUS_CANCELLED:
                    logger.info(f"Job {job_id} cancelled at photo {index}/{len(photos)}")
                    cancel_count = len(photos) - index
                    break

            photo_path = Path(photo.full_file_path)
            if not photo_path.exists():
                logger.warning(f"Photo file not found: {photo_path}")
                error_count += 1
                continue

            people_names = []
            for face in photo.faces:
                for person in face.people:
                    if person.name:
                        people_names.append(person.name)

            people_names = list(set(people_names))

            success, error = write_photo_metadata(
                photo_path=photo_path,
                date_taken=photo.date_taken,
                location_name=photo.location_name,
                people_names=people_names if people_names else None
            )

            if success:
                success_photo_ids.append(photo.id)
                processed_count += 1
                logger.debug(f"Successfully synced metadata for {photo_path}")
            else:
                error_count += 1
                logger.warning(f"Failed to sync metadata for {photo_path}: {error}")

        update_job_params = {
            'completed_count': Job.completed_count + processed_count,
            'error_count': Job.error_count + error_count,
            'cancelled_count': Job.cancelled_count + cancel_count,
        }
        if job_status == JOB_STATUS_PENDING:
            update_job_params['status'] = JOB_STATUS_RUNNING
        session.query(Photo).filter(Photo.id.in_(success_photo_ids)).update({
            'status': PHOTO_STATUS_SYNCED
        })
        session.query(Job).filter_by(id=job_id).update(update_job_params)
        session.commit()
        logger.info(
            f"Completed job {job_id} batch: processed={processed_count}, errors={error_count}, cancelled={cancel_count}"
        )

    except Exception as e:
        logger.error(f"Error in sync_metadata_task for job {job_id}: {e}", exc_info=True)
        session.rollback()
        session.query(Job).filter_by(id=job_id).update({
            'error_count': Job.error_count + len(photo_id_batch)
        })
        session.commit()
    finally:
        session.close()
        SessionFactory.remove()