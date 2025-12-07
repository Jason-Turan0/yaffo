from yaffo.background_tasks.tasks.import_photo import import_photo_task
from yaffo.background_tasks.tasks.index_photo import index_photo_task
from yaffo.background_tasks.tasks.auto_assign_faces import auto_assign_faces_task
from yaffo.background_tasks.tasks.sync_metadata import sync_metadata_task
from yaffo.background_tasks.tasks.organize_photos import organize_photos_task
from yaffo.background_tasks.tasks.complete_job import complete_job_task
from yaffo.background_tasks.tasks.find_duplicates import find_duplicates_task
from yaffo.background_tasks.tasks.remove_duplicates import remove_duplicates_task

# Re-export utilities for backward compatibility
from yaffo.background_tasks.utils import (
    get_job_status,
    load_assign_faces_task_data,
    schedule_job_completion,
    SessionFactory,
)

__all__ = [
    # Tasks
    'import_photo_task',
    'index_photo_task',
    'auto_assign_faces_task',
    'sync_metadata_task',
    'organize_photos_task',
    'complete_job_task',
    'find_duplicates_task',
    'remove_duplicates_task',
    # Utilities (for backward compatibility)
    'get_job_status',
    'load_assign_faces_task_data',
    'schedule_job_completion',
    'SessionFactory',
]