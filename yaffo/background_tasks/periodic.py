"""Declarative list of periodic tasks for the host to enqueue.

Kept deliberately free of task-code imports: the host reads this to schedule ticks
without importing the task modules (and thus without ever loading dlib). The task
functions themselves are still registered for execution via @task_queue.periodic_task
in their own modules; the worker children run them."""
from yaffo.taskq import CronSpec, crontab

# (registered task name, cron spec). The only periodic task drives schedule-based
# automations every minute.
PERIODIC_TASKS: list[tuple[str, CronSpec]] = [
    ("dispatch_scheduled_tasks", crontab(minute="*")),
]
