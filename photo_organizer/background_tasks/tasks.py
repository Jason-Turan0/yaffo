from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, joinedload
from photo_organizer.db.models import Job, Photo, JOB_STATUS_CANCELLED, Face, Tag, FACE_STATUS_UNASSIGNED, Person, \
    JobResult, JOB_STATUS_RUNNING, JOB_STATUS_PENDING
from photo_organizer.db.repositories.person_repository import update_person_embedding
from photo_organizer.utils.index_photos import index_photo_faces, import_photo
from photo_organizer.common import DB_PATH
from photo_organizer.logging_config import get_logger
from photo_organizer.background_tasks.config import huey
from photo_organizer.domain.compare_utils import calculate_similarity
import json

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={'check_same_thread': False},
    pool_pre_ping=True  # Verify connections before use
)
# Use scoped_session for thread-safe session management
SessionFactory = scoped_session(sessionmaker(bind=engine))
logger = get_logger(__name__, 'background_tasks')

def get_job_status(job_id):
    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        return job.status if job is not None else JOB_STATUS_CANCELLED
    finally:
        session.close()
        SessionFactory.remove()

"""
    Huey task to import photos - create photos and tags in database.
    Supports graceful cancellation and crash recovery.
"""
@huey.task()
def import_photo_task(job_id: str, file_path_batch: list[str]):
    logger.info(f"Starting import_photo_task for job {job_id} with {len(file_path_batch)} files")
    processed_results = []
    error_count = 0
    cancel_count = 0
    check_cancel_frequency = 5
    job_status = get_job_status(job_id)
    if job_status == JOB_STATUS_CANCELLED:
        return

    for index, file_path in enumerate(file_path_batch):
        # Periodically check for cancellation without holding database lock
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

        # Bulk insert all photos and tags
        for result in processed_results:
            photo = Photo(
                full_file_path=result["full_file_path"],
                relative_file_path=result["relative_file_path"],
                hash=result["hash"],
                date_taken=result["date_taken"],
                latitude=result.get("latitude"),
                longitude=result.get("longitude"),
                location_name=result.get("location_name")
            )
            session.add(photo)
            session.flush()

            # Add all tags for this photo
            for tag_data in result.get("tags", []):
                tag = Tag(
                    photo_id=photo.id,
                    tag_name=tag_data['tag_name'],
                    tag_value=tag_data['tag_value']
                )
                session.add(tag)
        processed_count = len(processed_results)
        update_job_params ={
            'completed_count': Job.completed_count + processed_count,
            'cancelled_count': Job.cancelled_count + cancel_count,
            'error_count': Job.error_count + error_count,
        }
        if job_status == JOB_STATUS_PENDING:
            update_job_params['status'] = JOB_STATUS_RUNNING

        session.query(Job).filter_by(id=job_id).update(update_job_params)
        session.commit()
        logger.info(f"Completed job {job_id} batch: processed={processed_count}, errors={error_count}, cancelled={cancel_count}")

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

@huey.task()
def index_photo_task(job_id: str, file_path_batch: list[str]):
    logger.info(f"Starting index_photo_task for job {job_id} with {len(file_path_batch)} files")
    processed_results = []
    error_count = 0
    cancel_count = 0
    check_cancel_frequency = 5
    job_status = get_job_status(job_id)
    if job_status == JOB_STATUS_CANCELLED:
        return

    for index, file_path in enumerate(file_path_batch):
        # Periodically check for cancellation without holding database lock
        if index % check_cancel_frequency == 0:
            job_status = get_job_status(job_id)
            if job_status == JOB_STATUS_CANCELLED:
                logger.info(f"Job {job_id} cancelled at photo {index}/{len(file_path_batch)}")
                cancel_count = len(file_path_batch) - index
                break

        logger.debug(f"Processing photo {file_path}")
        faces_data = index_photo_faces(Path(file_path))
        if faces_data is None:
            logger.warning(f"Failed to process faces for photo {file_path}")
            error_count += 1
            continue

        # Store result for later database insertion
        processed_results.append({
            'full_file_path': file_path,
            'faces_data': faces_data,
        })
    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        photos_in_batch = session.query(Photo).filter(Photo.full_file_path.in_(file_path_batch)).all()
        processed_count = 0
        # Bulk insert all faces
        for result in processed_results:
            full_file_path = result["full_file_path"]
            faces_data = result["faces_data"]
            photo = next(photo for photo in photos_in_batch if photo.full_file_path == full_file_path)
            if photo is None:
                logger.error(f"Failed to find photo in db for {full_file_path}")
                error_count += 1
                continue

            # Add all faces for this photo
            for face_data in faces_data:
                face = Face(
                    embedding=face_data['embedding'].tobytes(),
                    full_file_path=face_data['full_file_path'],
                    status=FACE_STATUS_UNASSIGNED,
                    relative_file_path=face_data['relative_file_path'],
                    photo_id=photo.id,
                    location_top=face_data['location_top'],
                    location_right=face_data['location_right'],
                    location_bottom=face_data['location_bottom'],
                    location_left=face_data['location_left']
                )
                session.add(face)
            processed_count  += 1
        update_job_params = {
            'cancelled_count': Job.cancelled_count + cancel_count,
            'completed_count': Job.completed_count + processed_count,
            'error_count': Job.error_count + error_count,
        }
        if job_status == JOB_STATUS_PENDING:
            update_job_params['status'] = JOB_STATUS_RUNNING

        session.query(Job).filter_by(id=job_id).update(update_job_params)
        session.commit()
        logger.info(
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

def load_assign_faces_task_data(person_id, face_ids):
    session = SessionFactory()
    try:
        person = session.query(Person).options(joinedload(Person.embeddings_by_year)).filter_by(id=person_id).first()
        faces = session.query(Face).filter(Face.id.in_(face_ids)).all()
        return person, faces
    finally:
        session.close()
        SessionFactory.remove()

@huey.task(context=True)
def auto_assign_faces_task(job_id: str, face_id_batch: list[int], person_id: int, similarity_threshold: float, task=None):
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
        job_result = JobResult(job_id=job_id,huey_task_id = task.id, result_data= json.dumps({'matches': matches}))
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