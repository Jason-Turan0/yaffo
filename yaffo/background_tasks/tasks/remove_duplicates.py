from pathlib import Path
import shutil
import os
import send2trash

from yaffo.db.models import Job, JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING, JOB_STATUS_PENDING, JOB_STATUS_COMPLETED
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.utils import SessionFactory, get_job_status

logger = get_logger(__name__, 'background_tasks')


@huey.task()
def remove_duplicates_task(job_id: str, file_paths: list[str], action_type: str, destination_folder: str = None):
    """Huey task to remove duplicate photos by trash, delete, or move."""
    logger.info(f"Starting remove_duplicates_task for job {job_id} with {len(file_paths)} files, action={action_type}")

    check_cancel_frequency = 10
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
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                logger.warning(f"File not found: {file_path}")
                error_count += 1
                continue

            if action_type == 'trash':
                send2trash.send2trash(str(file_path_obj))
                logger.debug(f"Moved to trash: {file_path}")
            elif action_type == 'delete':
                os.remove(str(file_path_obj))
                logger.debug(f"Deleted: {file_path}")
            elif action_type == 'moveFolder':
                dest_dir = Path(destination_folder)
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / file_path_obj.name
                if dest_path.exists():
                    base = dest_path.stem
                    ext = dest_path.suffix
                    counter = 1
                    while dest_path.exists():
                        dest_path = dest_dir / f"{base}_{counter}{ext}"
                        counter += 1
                shutil.move(str(file_path_obj), str(dest_path))
                logger.debug(f"Moved to folder: {file_path} -> {dest_path}")

            processed_count += 1
        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")
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

    session = SessionFactory()
    try:
        session.query(Job).filter_by(id=job_id).update({
            'completed_count': processed_count,
            'error_count': error_count,
            'cancelled_count': cancel_count,
            'status': JOB_STATUS_COMPLETED
        })
        session.commit()
        logger.info(
            f"Completed job {job_id}: processed={processed_count}, errors={error_count}, cancelled={cancel_count}"
        )
    except Exception as e:
        logger.error(f"Error in remove_duplicates_task for job {job_id}: {e}", exc_info=True)
        session.rollback()
    finally:
        session.close()
        SessionFactory.remove()