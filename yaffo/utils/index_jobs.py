import json
import uuid
from itertools import batched

from sqlalchemy.orm import Session

from yaffo.db.models import MediaItem, Job, JOB_STATUS_PENDING, MEDIA_STATUS_INDEXED
from yaffo.utils.index_jobs_dto import IndexJobs
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')

IMPORT_BATCH_SIZE = 250


def reindex_media_items(session: Session, media_items: list[MediaItem]) -> IndexJobs:
    """Rebuild the given media items from their files: the single action behind both
    reindex buttons (one photo, whole library).

    The rows are reset before the work is queued, not by the job when it eventually
    runs: status goes back to IMPORTED and the faces (with their person assignments
    and thumbnail files) are deleted. So a photo waiting in the queue reads as
    un-indexed and shows no stale faces, rather than displaying boxes and names from
    a detection pass that is about to be thrown away.

    Deleting those faces invalidates the medoid gallery of every person who had one on
    these photos -- it was built from faces that no longer exist -- so their embeddings
    are rebuilt too (which also rescores their remaining faces; see
    person_repository.update_person_embedding).
    """
    from yaffo.db.repositories.person_repository import update_person_embedding
    from yaffo.utils.index_photos import reset_media_items_for_reindex, unlink_face_thumbnails

    reset = reset_media_items_for_reindex(session, [item.id for item in media_items])
    session.commit()
    # After the commit: a rollback must never leave live faces pointing at files we
    # already deleted.
    unlink_face_thumbnails(reset.thumbnails)
    for person_id in reset.person_ids:
        update_person_embedding(person_id, session)

    # The reset put these rows back to IMPORTED, so they'd be picked up anyway; `force`
    # states the intent outright rather than relying on that as a side effect.
    return enqueue_index_jobs(
        session, [item.full_file_path for item in media_items], force=True
    )


def enqueue_index_jobs(
    session: Session, files_to_index: list[str], automation_id: int | None = None,
    force: bool = False,
) -> IndexJobs:
    """Create import/index Jobs for the given files and dispatch the queue tasks.

    Files not already present are imported; files not already INDEXED are
    (re)indexed. Shared by the index-photos route and the file-system watcher so
    both schedule work identically. The caller owns the session and any other
    work (orphan cleanup); this commits the two Job rows before dispatching.

    `force` re-indexes files that are already INDEXED -- what the reindex actions
    ask for. Their faces are re-detected, which drops any person assignments on
    them (see clear_faces_for_media_items), so only ask for it deliberately.

    `automation_id` tags the Jobs as a run of that automation (NULL for
    user-initiated syncs), so the job machinery doubles as the run history.

    Work is dispatched as a single chord pipeline: the import members run as a
    group, their completion finalizes the import job and then triggers
    start_index_stage, which dispatches the index members as a second group whose
    completion finalizes the index job. Indexing therefore can't begin until
    every import task has finished (the index tasks read the Photo rows the
    import tasks create). The task queue, chord primitive and task functions
    are imported in-function so this module never imports the background_tasks
    package at load time -- which is what would form a util<->tasks import cycle.
    """
    from yaffo.taskq import chord
    from yaffo.background_tasks.config import task_queue
    from yaffo.background_tasks.tasks.import_photo import import_photo_task
    from yaffo.background_tasks.tasks.complete_job import complete_job_callback
    from yaffo.background_tasks.tasks.index_stage import start_index_stage

    existing = {
        full_path: status
        for _id, full_path, status in session.query(
            MediaItem.id, MediaItem.full_file_path, MediaItem.status
        ).all()
    }

    files_to_import = [fp for fp in files_to_index if fp not in existing]
    files_needing_indexing = files_to_index if force else [
        fp for fp in files_to_index
        if fp not in existing or existing[fp] != MEDIA_STATUS_INDEXED
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

    import_members = [
        import_photo_task.s(import_job_id, list(batch))
        for batch in batched(files_to_import, IMPORT_BATCH_SIZE)
    ]
    pipeline = chord(import_members, complete_job_callback.s(import_job_id))
    pipeline.then(start_index_stage, index_job_id)
    task_queue.enqueue(pipeline)

    logger.info(
        f"Scheduled import_job={import_job_id} ({len(files_to_import)} files), "
        f"index_job={index_job_id} ({len(files_needing_indexing)} files)"
    )
    return IndexJobs(import_job_id=import_job_id, index_job_id=index_job_id)