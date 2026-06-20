from typing import Sequence, Callable, TypeVar

from sqlalchemy.orm import Session

from yaffo.db.models import Job

T = TypeVar('T')


class ProgressReporter:
    def __init__(self, session: Session, job_id: int):
        self.session = session
        self.job_id = job_id

    def progress_update(self, task_count: int, completed_count: int, cancelled: int, error_count: int):
        update_job_params = {
            'task_count': task_count,
            'completed_count': completed_count,
            'cancelled_count': cancelled,
            'error_count': error_count,
        }
        self.session.query(Job).filter_by(id=self.job_id).update(update_job_params)
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