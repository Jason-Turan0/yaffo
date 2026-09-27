import time
from typing import Sequence, Callable, TypeVar
from sqlalchemy.orm import Session
from yaffo.db.models import Job
from yaffo.db.repositories.job_repository import estimate_completion, is_job_cancelled
from yaffo.utils.time import utcnow

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
        job.estimated_completed_at = estimate_completion(
            job.started_at, task_count, completed_count, error_count, cancelled, utcnow())
        self.session.commit()

    def is_cancelled(self) -> bool:
        return is_job_cancelled(self.session, self.job_id)

    def run_with_progress(self,
            items: Sequence[T],
            item_processor: Callable[[T], None],
            percentage: float = 0.05,
            time_interval_seconds: float = 30.0,
            cancel_check_every: int = 10) -> bool:
        """Run `item_processor` over `items`, reporting progress on the Job. Stops early
        when the Job is cancelled, counting the unprocessed items as cancelled. Returns
        False when it stopped for a cancel, True when it processed every item."""
        completed = 0
        errors = 0
        processed = 0
        total_tasks = len(items)
        report_interval = max(1, int(total_tasks * percentage))
        last_report_time = time.monotonic()
        self.progress_update(total_tasks, completed, 0, errors)
        for item in items:
            if processed > 0 and processed % cancel_check_every == 0 and self.is_cancelled():
                self.progress_update(total_tasks, completed, total_tasks - processed, errors)
                return False
            try:
                item_processor(item)
                completed += 1
            except Exception:
                errors += 1
            processed += 1
            current_time = time.monotonic()
            if (processed % report_interval == 0 or
                processed == total_tasks or
                (current_time - last_report_time) >= time_interval_seconds):
                self.progress_update(total_tasks, completed, 0, errors)
                last_report_time = current_time
        return True
