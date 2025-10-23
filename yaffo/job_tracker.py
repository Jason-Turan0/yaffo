import uuid
from datetime import datetime
from typing import Dict, Optional
from enum import Enum


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job:
    def __init__(self, job_type: str, data: dict):
        self.id = str(uuid.uuid4())
        self.job_type = job_type
        self.status = JobStatus.PENDING
        self.data = data
        self.progress = 0
        self.total = 100
        self.message = ""
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.error: Optional[str] = None
        self.cancel_requested = False

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'job_type': self.job_type,
            'status': self.status.value,
            'progress': self.progress,
            'total': self.total,
            'message': self.message,
            'created_at': self.created_at.isoformat(),
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error': self.error
        }


class JobTracker:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}

    def create_job(self, job_type: str, data: dict) -> Job:
        job = Job(job_type, data)
        self.jobs[job.id] = job
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def update_job(self, job_id: str, **kwargs):
        job = self.jobs.get(job_id)
        if job:
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)

    def get_all_jobs(self):
        return list(self.jobs.values())

    def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job and job.status in [JobStatus.PENDING, JobStatus.RUNNING]:
            job.cancel_requested = True
            return True
        return False


job_tracker = JobTracker()