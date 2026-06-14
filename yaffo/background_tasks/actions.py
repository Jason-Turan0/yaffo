from typing import Callable

from yaffo.db.models import TASK_ACTION_FILE_SYNC
from yaffo.background_tasks.tasks.file_sync import file_sync_task

# Maps a TaskSchedule.action to a function that enqueues its concrete huey task.
# Each handler receives the row's parsed `args` (a dict, or {} when null) and is
# responsible only for enqueueing -- the dispatcher owns scheduling/bookkeeping.
# Adding a new schedulable action = register one huey task + add a line here.
ScheduledAction = Callable[[dict], None]


def _enqueue_file_sync(args: dict) -> None:
    file_sync_task()


ACTIONS: dict[str, ScheduledAction] = {
    TASK_ACTION_FILE_SYNC: _enqueue_file_sync,
}