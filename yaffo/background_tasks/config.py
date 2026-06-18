from yaffo.common import QUEUE_DB_PATH
from yaffo.taskq import TaskQueue

# The process-wide queue every task module decorates against. Producers (Flask,
# the watcher, worker children) share the same SQLite file; the host supervises
# the spawn worker pool that actually runs the tasks. See yaffo/taskq.
task_queue = TaskQueue(
    filename=str(QUEUE_DB_PATH),
    immediate=False,
)
