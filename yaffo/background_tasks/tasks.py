from pathlib import Path
import shutil
from calendar import month_name

import numpy as np
from sklearn.cluster import DBSCAN
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, joinedload
from yaffo.db.models import Job, Photo, JOB_STATUS_CANCELLED, Face, Tag, FACE_STATUS_UNASSIGNED, Person, \
    JobResult, JOB_STATUS_RUNNING, JOB_STATUS_PENDING, PHOTO_STATUS_INDEXED, PHOTO_STATUS_SYNCED, JOB_STATUS_COMPLETED
from yaffo.utils.index_photos import index_photo, import_photo
from yaffo.common import DB_PATH, THUMBNAIL_DIR, PHOTO_EXTENSIONS
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import huey
from yaffo.domain.compare_utils import calculate_similarity, load_embedding
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
                date_taken=result["date_taken"],
                latitude=result.get("latitude"),
                longitude=result.get("longitude"),
                location_name=result.get("location_name")
            )
            session.add(photo)
            session.flush()
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
        if index > 0 and index % check_cancel_frequency == 0:
            job_status = get_job_status(job_id)
            if job_status == JOB_STATUS_CANCELLED:
                logger.info(f"Job {job_id} cancelled at photo {index}/{len(file_path_batch)}")
                cancel_count = len(file_path_batch) - index
                break

        logger.debug(f"Processing photo {file_path}")
        index_results = index_photo(Path(file_path), THUMBNAIL_DIR)
        if index_results is None:
            logger.warning(f"Failed to process faces for photo {file_path}")
            error_count += 1
            continue

        # Store result for later database insertion
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
        # Bulk insert all faces
        for result in processed_results:
            full_file_path = result["full_file_path"]
            index_results = result["index_results"]
            faces_data = index_results["faces_data"]
            latitude = index_results["latitude"]
            longitude = index_results["longitude"]
            location_name = index_results["location_name"]
            tags = index_results["tags"]
            photo = next(photo for photo in photos_in_batch if photo.full_file_path == full_file_path)
            if photo is None:
                logger.error(f"Failed to find photo in db for {full_file_path}")
                error_count += 1
                continue
            photo.latitude = latitude
            photo.longitude = longitude
            photo.location_name = location_name
            # Add all tags for this photo
            for tag_data in tags:
                tag = Tag(
                    photo_id=photo.id,
                    tag_name=tag_data['tag_name'],
                    tag_value=tag_data['tag_value']
                )
                session.add(tag)
            photo.status =PHOTO_STATUS_INDEXED
            # Add all faces for this photo
            for face_data in faces_data:
                face = Face(
                    embedding=face_data['embedding'].tobytes(),
                    full_file_path=face_data['full_file_path'],
                    status=FACE_STATUS_UNASSIGNED,
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


@huey.task()
def sync_metadata_task(job_id: str, photo_id_batch: list[int]):
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
        updated_count = (
            session.query(Photo)
            .filter(Photo.id.in_(success_photo_ids))
            .update({
                'status': PHOTO_STATUS_SYNCED
            })
        )
        session.query(Job).filter_by(id=job_id).update(update_job_params)
        session.commit()
        logger.info(
            f"Completed job {job_id} batch: processed={processed_count}, errors={error_count}, cancelled={cancel_count}. Updated count={updated_count}"
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


@huey.task()
def organize_photos_task(job_id: str, file_operations: list[dict[str, str]]):
    logger.info(f"Starting organize_photos_task for job {job_id} with {len(file_operations)} files")
    processed_count = 0
    error_count = 0
    cancel_count = 0
    check_cancel_frequency = 10
    path_updates = []

    job_status = get_job_status(job_id)
    if job_status == JOB_STATUS_CANCELLED:
        return

    for index, operation in enumerate(file_operations):
        if index > 0 and index % check_cancel_frequency == 0:
            job_status = get_job_status(job_id)
            if job_status == JOB_STATUS_CANCELLED:
                logger.info(f"Job {job_id} cancelled at file {index}/{len(file_operations)}")
                cancel_count = len(file_operations) - index
                break

        source_path = Path(operation['source'])
        dest_path = Path(operation['destination'])
        operation_type = operation['type']

        try:
            if not source_path.exists():
                logger.warning(f"Source file not found: {source_path}")
                error_count += 1
                continue

            dest_path.parent.mkdir(parents=True, exist_ok=True)

            counter = 1
            original_dest = dest_path
            while dest_path.exists() and dest_path.resolve() != source_path.resolve():
                dest_path = original_dest.parent / f"{original_dest.stem}_{counter}{original_dest.suffix}"
                counter += 1

            if dest_path.resolve() != source_path.resolve():
                if operation_type == 'copy':
                    shutil.copy2(str(source_path), str(dest_path))
                    logger.debug(f"Copied: {source_path} -> {dest_path}")
                else:
                    source_dir = source_path.parent
                    shutil.move(str(source_path), str(dest_path))
                    logger.debug(f"Moved: {source_path} -> {dest_path}")
                    path_updates.append({
                        'old_path': str(source_path),
                        'new_path': str(dest_path)
                    })

                    # Clean up empty directories after moving
                    try:
                        current_dir = source_dir
                        while current_dir != current_dir.parent:
                            if current_dir.exists() and not any(current_dir.iterdir()):
                                current_dir.rmdir()
                                logger.debug(f"Removed empty directory: {current_dir}")
                                current_dir = current_dir.parent
                            else:
                                break
                    except Exception as cleanup_error:
                        logger.warning(f"Error cleaning up empty directory {source_dir}: {cleanup_error}")

            processed_count += 1

        except Exception as e:
            logger.error(f"Error processing {source_path}: {e}", exc_info=True)
            error_count += 1

    session = SessionFactory()
    try:
        if path_updates:
            old_paths = [update['old_path'] for update in path_updates]
            photos = session.query(Photo).filter(Photo.full_file_path.in_(old_paths)).all()
            photos_by_path = {photo.full_file_path: photo for photo in photos}

            photo_ids = [photo.id for photo in photos]
            faces = session.query(Face).filter(Face.photo_id.in_(photo_ids)).all() if photo_ids else []
            faces_by_photo_id = {}
            for face in faces:
                if face.photo_id not in faces_by_photo_id:
                    faces_by_photo_id[face.photo_id] = []
                faces_by_photo_id[face.photo_id].append(face)

            for path_update in path_updates:
                old_path = path_update['old_path']
                new_path = path_update['new_path']

                photo = photos_by_path.get(old_path)
                if photo:
                    old_photo_path = Path(photo.full_file_path)
                    photo.full_file_path = new_path
                    logger.debug(f"Updated photo path in database: {old_path} -> {new_path}")

                    photo_faces = faces_by_photo_id.get(photo.id, [])
                    for face in photo_faces:
                        old_face_path = Path(face.full_file_path)
                        if old_face_path.parent == old_photo_path.parent:
                            new_face_path = Path(new_path).parent / old_face_path.name
                            face.full_file_path = str(new_face_path)
                            logger.debug(f"Updated face path in database: {old_face_path} -> {new_face_path}")

        update_job_params = {
            'completed_count': Job.completed_count + processed_count,
            'error_count': Job.error_count + error_count,
            'cancelled_count': Job.cancelled_count + cancel_count,
        }
        if job_status == JOB_STATUS_PENDING:
            update_job_params['status'] = JOB_STATUS_RUNNING

        session.query(Job).filter_by(id=job_id).update(update_job_params)
        session.commit()
        logger.info(
            f"Completed job {job_id} batch: processed={processed_count}, errors={error_count}, cancelled={cancel_count}, path_updates={len(path_updates)}"
        )

    except Exception as e:
        logger.error(f"Error updating job {job_id}: {e}", exc_info=True)
        session.rollback()
        session.query(Job).filter_by(id=job_id).update({
            'error_count': Job.error_count + len(file_operations)
        })
        session.commit()
    finally:
        session.close()
        SessionFactory.remove()