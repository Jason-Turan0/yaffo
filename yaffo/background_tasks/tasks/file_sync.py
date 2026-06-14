from yaffo.background_tasks.config import huey
from yaffo.background_tasks.utils import SessionFactory
from yaffo.utils.file_sync import run_file_sync


@huey.task()
@huey.lock_task('file-sync')
def file_sync_task():
    """Reconcile the photo index with disk. Enqueued by the schedule dispatcher
    (action 'file_sync') or directly; runs the same sync as the manual
    index-photos button (via run_file_sync), so the import/index Jobs it creates
    appear in the UI exactly like a hand-triggered sync. `lock_task` skips the run
    if a previous file-sync is still going, so slow scans can't pile up."""
    session = SessionFactory()
    try:
        run_file_sync(session)
    finally:
        session.close()
        SessionFactory.remove()