from typing import Sequence, Callable, TypeVar

from sqlalchemy.orm import Session

from yaffo.db.models import Job

T = TypeVar('T')


class ProgressReporter:
    def __init__(self, session: Session, job_id: int):
        self.session = session
        self.job_id = job_id

    def progress_update(self, task_count: int, completed_count: int, cancelled: int, error_count: int):
        # Mutate the identity-mapped Job (not a bulk query.update): a caller that holds
        # its own Job object across the run (automation_runs.run_and_record) would
        # otherwise clobber these counts with its stale copy on its final commit.
        job = self.session.get(Job, self.job_id)
        if job is None:
            return
        job.task_count = task_count
        job.completed_count = completed_count
        job.cancelled_count = cancelled
        job.error_count = error_count
        self.session.commit()

    def run_with_progress(self,
            items: Sequence[T],
            item_processor: Callable[[T], None],
            percentage=0.05):
        completed = 0
        errors = 0
        processed = 0
        total_tasks = len(items)
        report_interval = max(1, int(total_tasks * percentage))
        self.progress_update(total_tasks, completed, 0, errors)
        for item in items:
            try:
                item_processor(item)
                completed += 1
            except Exception:
                errors += 1
            processed += 1
            if processed % report_interval == 0 or processed == total_tasks:
                self.progress_update(total_tasks, completed, 0, errors)