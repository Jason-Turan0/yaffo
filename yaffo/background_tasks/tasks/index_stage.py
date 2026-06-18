import json
from itertools import batched

from huey import chord

from yaffo.db.models import Job
from yaffo.logging_config import get_logger
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.utils import SessionFactory
from yaffo.background_tasks.tasks.index_photo import index_photo_task
from yaffo.background_tasks.tasks.complete_job import complete_job_callback, finalize_job

logger = get_logger(__name__, 'background_tasks')

INDEX_BATCH_SIZE = 10


@huey.task()
def start_index_stage(index_job_id: str, prev_result=None) -> None:
    """Dispatch the index chord once the import job has finished.

    Runs as the continuation of the import completion step (so the Photo rows
    the index tasks read are guaranteed to exist). The files to index are read
    back from the index Job's job_data rather than threaded through the pipeline.
    `index_job_id` is the bound argument; `prev_result` only receives a value
    when the upstream step returns non-None (Huey appends the previous result to
    a pipeline step's args) -- the completion steps return None, so it stays
    unused. An empty index set finalizes the job directly, since a chord callback
    never fires for an empty group.
    """
    session = SessionFactory()
    try:
        job = session.query(Job).filter_by(id=index_job_id).first()
        if not job:
            logger.error(f"Index job {index_job_id} not found in start_index_stage")
            return
        files_to_index = json.loads(job.job_data).get('files_to_index', [])
    finally:
        session.close()
        SessionFactory.remove()

    members = [
        index_photo_task.s(index_job_id, list(batch))
        for batch in batched(files_to_index, INDEX_BATCH_SIZE)
    ]
    if members:
        huey.enqueue(chord(members, complete_job_callback.s(index_job_id)))
        logger.info(f"Dispatched index chord for job {index_job_id} ({len(files_to_index)} files)")
    else:
        finalize_job(index_job_id)
        logger.info(f"Index job {index_job_id} had no files; finalized directly")