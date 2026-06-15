import json
import uuid
from itertools import batched

from sqlalchemy.orm import Session

from yaffo.db.models import Photo, Job, JOB_STATUS_PENDING, PHOTO_STATUS_INDEXED
from yaffo.background_tasks.utils import schedule_job_completion
from yaffo.utils.index_jobs_dto import IndexJobs
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')

IMPORT_BATCH_SIZE = 250
INDEX_BATCH_SIZE = 10


def enqueue_index_jobs(
    session: Session, files_to_index: list[str], automation_id: int | None = None
) -> IndexJobs:
    """Create import/index Jobs for the given files and dispatch the Huey tasks.

    Files not already present are imported; files not already INDEXED are
    (re)indexed. Shared by the index-photos route and the file-system watcher so
    both schedule work identically. The caller owns the session and any other
    work (orphan cleanup); this commits the two Job rows before dispatching.

    `automation_id` tags the Jobs as a run of that automation (NULL for
    user-initiated syncs), so the job machinery doubles as the run history.

    The import/index task functions are imported in-function (like the sibling
    schedule_job_completion) so this module never imports the background_tasks
    package at load time -- which is what would form a util<->tasks import cycle.
    """
    from yaffo.background_tasks.tasks.index_photo import index_photo_task
    from yaffo.background_tasks.tasks.import_photo import import_photo_task

    existing = {
        full_path: status
        for _id, full_path, status in session.query(
            Photo.id, Photo.full_file_path, Photo.status
        ).all()
    }

    files_to_import = [fp for fp in files_to_index if fp not in existing]
    files_needing_indexing = [
        fp for fp in files_to_index
        if fp not in existing or existing[fp] != PHOTO_STATUS_INDEXED
    ]

    import_job_id = str(uuid.uuid4())
    index_job_id = str(uuid.uuid4())
    session.add(Job(
        id=import_job_id,
        name='import_photos',
        status=JOB_STATUS_PENDING,
        automation_id=automation_id,
        task_count=len(files_to_import),
        message='Imported {totalCount}/{taskCount} photos',
        completed_count=0,
        error_count=0,
        cancelled_count=0,
        job_data=json.dumps({'files_to_import': files_to_import}),
    ))
    session.add(Job(
        id=index_job_id,
        name='index_photos',
        status=JOB_STATUS_PENDING,
        automation_id=automation_id,
        task_count=len(files_needing_indexing),
        message='Indexed {totalCount}/{taskCount} photos',
        completed_count=0,
        error_count=0,
        cancelled_count=0,
        job_data=json.dumps({'files_to_index': files_needing_indexing}),
    ))
    session.commit()

    for batch in batched(files_to_import, IMPORT_BATCH_SIZE):
        import_photo_task(import_job_id, list(batch))
    schedule_job_completion(import_job_id)

    for batch in batched(files_needing_indexing, INDEX_BATCH_SIZE):
        index_photo_task(index_job_id, list(batch))
    schedule_job_completion(index_job_id)

    logger.info(
        f"Scheduled import_job={import_job_id} ({len(files_to_import)} files), "
        f"index_job={index_job_id} ({len(files_needing_indexing)} files)"
    )
    return IndexJobs(import_job_id=import_job_id, index_job_id=index_job_id)