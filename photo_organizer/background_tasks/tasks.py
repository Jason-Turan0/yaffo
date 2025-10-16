from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from photo_organizer.db.models import Job, Photo, JOB_STATUS_CANCELLED, Face, FACE_STATUS_UNASSIGNED
from photo_organizer.utils.index_photos import process_photo
from photo_organizer.common import DB_PATH
from photo_organizer.logging_config import get_logger
from photo_organizer.background_tasks.config import huey

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={'check_same_thread': False},
    pool_pre_ping=True  # Verify connections before use
)
# Use scoped_session for thread-safe session management
SessionFactory = scoped_session(sessionmaker(bind=engine))

"""
    Huey task to sync photos - index new files and delete orphaned entries.
    Supports graceful cancellation and crash recovery.
"""
@huey.task()
def index_photo_task(job_id : str, file_path_batch : list[str]):
    logger = get_logger(__name__, 'background_tasks')
    logger.info(f"Starting index_photo_task for job {job_id} with {len(file_path_batch)} files")
    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        error_count = 0
        processed_count = 0
        cancel_count = 0
        check_cancel_frequency = 2

        for index, file_path in enumerate(file_path_batch):
            if index > 0 and index % check_cancel_frequency == 0:
                job = session.query(Job).filter_by(id=job_id).first()

            if job and job.status == JOB_STATUS_CANCELLED:
                cancel_count += 1
                continue
            logger.debug(f"Processing photo {file_path}")
            result = process_photo(Path(file_path))
            if result is None:
                logger.warning(f"Failed to process photo {file_path}")
                error_count += 1
                continue

            photo = Photo(
                full_file_path=result["full_file_path"],
                relative_file_path=result["relative_file_path"],
                hash=result["hash"],
                date_taken=result["date_taken"]
            )
            session.add(photo)
            session.flush()

            for face_data in result["faces"]:
                face = Face(
                    embedding=face_data['embedding'].tobytes(),
                    full_file_path=face_data['full_file_path'],
                    relative_file_path=face_data['relative_file_path'],
                    status=FACE_STATUS_UNASSIGNED,
                    photo_id=photo.id,
                    location_top=face_data['location_top'],
                    location_right=face_data['location_right'],
                    location_bottom=face_data['location_bottom'],
                    location_left=face_data['location_left']
                )
                session.add(face)
            processed_count += 1


        session.query(Job).filter_by(id=job_id).update({
            'completed_count': Job.completed_count + processed_count,
            'error_count': Job.error_count + error_count,
            'cancelled_count': Job.cancelled_count + cancel_count,
        })
        session.commit()
        logger.info(f"Completed job {job_id} batch: processed={processed_count}, errors={error_count}, cancelled={cancel_count}")

    except Exception as e:
        logger.error(f"Error in index_photo_task for job {job_id}: {e}", exc_info=True)
        session.query(Job).filter_by(id=job_id).update({
            'error_count': Job.error_count + len(file_path_batch)
        })
        session.commit()
    finally:
        # Clean up session
        session.close()
        SessionFactory.remove()