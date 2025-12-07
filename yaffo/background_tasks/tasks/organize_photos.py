from pathlib import Path
import shutil

from yaffo.db.models import Job, Photo, Face, JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING, JOB_STATUS_PENDING
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.utils import SessionFactory, get_job_status

logger = get_logger(__name__, 'background_tasks')


@huey.task()
def organize_photos_task(job_id: str, file_operations: list[dict[str, str]]):
    """Huey task to organize photos by moving/copying files."""
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