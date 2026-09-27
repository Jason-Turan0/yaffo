from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.registry import register_handler
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.repositories.job_repository import run_job_id
from yaffo.db.models import Automation, AUTOMATION_HANDLER_FILE_SYNC
from yaffo.utils.file_sync import run_file_sync


@task_queue.task(context=True)
@task_queue.lock_task('file-sync')
def file_sync_task(automation_id: int | None = None, scope_paths: list[str] | None = None, task=None):
    """Reconcile the photo index with disk. Enqueued by the schedule dispatcher
    (the 'file_sync' automation handler) or directly; runs the same sync as the
    manual index-photos button (via run_file_sync), so the import/index Jobs it
    creates appear in the UI exactly like a hand-triggered sync. `lock_task` skips
    the run if a previous file-sync is still going, so slow scans can't pile up.
    `automation_id` tags the created Jobs as that automation's run. The run's Job
    id is the queue task id, so a crash retry records on the same Job."""
    session = SessionFactory()
    try:
        run_file_sync(session, automation_id=automation_id, scope_paths=scope_paths, job_id=run_job_id(task))
    finally:
        session.close()
        SessionFactory.remove()


@register_handler(AUTOMATION_HANDLER_FILE_SYNC)
def enqueue_file_sync(automation: Automation, context: EventContext | None = None) -> None:
    """Handler for the built-in file-sync automation: enqueue the task tagged
    with the automation's id so its run Jobs link back. Scheduled runs scan the
    context's selected roots; other callers keep the full reconcile."""
    file_sync_task(automation.id, context.scope_paths if context and context.event_type is None else None)
