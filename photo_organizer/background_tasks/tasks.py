from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, joinedload
from photo_organizer.db.models import Job, Photo, JOB_STATUS_CANCELLED, Face, Tag, FACE_STATUS_UNASSIGNED, Person, \
    JobResult
from photo_organizer.utils.index_photos import process_photo
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

def is_job_cancelled(job_id):
    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        return job is None or job.status == JOB_STATUS_CANCELLED
    finally:
        session.close()
        SessionFactory.remove()

"""
    Huey task to sync photos - index new files and delete orphaned entries.
    Supports graceful cancellation and crash recovery.
"""
@huey.task()
def index_photo_task(job_id: str, file_path_batch: list[str]):
    logger.info(f"Starting index_photo_task for job {job_id} with {len(file_path_batch)} files")
    processed_results = []
    error_count = 0
    cancel_count = 0
    check_cancel_frequency = 5

    for index, file_path in enumerate(file_path_batch):
        # Periodically check for cancellation without holding database lock
        if index % check_cancel_frequency == 0:
            if is_job_cancelled(job_id):
                logger.info(f"Job {job_id} cancelled at photo {index}/{len(file_path_batch)}")
                cancel_count = len(file_path_batch) - index
                break

        logger.debug(f"Processing photo {file_path}")
        result = process_photo(Path(file_path))
        if result is None:
            logger.warning(f"Failed to process photo {file_path}")
            error_count += 1
            continue

        # Store result for later database insertion
        processed_results.append(result)

    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        # Bulk insert all photos, faces, and tags
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
            session.flush()  # Get the photo.id for faces and tags

            # Add all faces for this photo
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

            # Add all tags for this photo
            for tag_data in result.get("tags", []):
                tag = Tag(
                    photo_id=photo.id,
                    tag_name=tag_data['tag_name'],
                    tag_value=tag_data['tag_value']
                )
                session.add(tag)

        processed_count = len(processed_results)

        session.query(Job).filter_by(id=job_id).update({
            'completed_count': Job.completed_count + processed_count,
            'error_count': Job.error_count + error_count,
            'cancelled_count': Job.cancelled_count + cancel_count,
        })
        session.commit()
        logger.info(f"Completed job {job_id} batch: processed={processed_count}, errors={error_count}, cancelled={cancel_count}")

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
    processed_count  = 0
    error_count = 0
    cancel_count = 0
    matches = []
    if is_job_cancelled(job_id):
        cancel_count = len(face_id_batch)
    else:
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
        session.query(Job).filter_by(id=job_id).update({
            'completed_count': Job.completed_count + processed_count,
            'error_count': Job.error_count + error_count,
            'cancelled_count': Job.cancelled_count + cancel_count,
        })
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