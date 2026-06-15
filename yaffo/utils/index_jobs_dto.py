from dataclasses import dataclass


@dataclass(frozen=True)
class IndexJobs:
    """The pair of Job ids enqueue_index_jobs creates (import + index).

    Its own module, free of heavy imports, so callers that only need the return
    type (e.g. utils.file_sync) don't import the task-dispatching enqueue module
    -- which would drag the background_tasks package into a util-layer cycle."""
    import_job_id: str
    index_job_id: str
