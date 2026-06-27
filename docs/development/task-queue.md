# Task Queue Standards

Yaffo uses a purpose-built SQLite-backed task queue in `yaffo/taskq/`. The queue
runs background work in `spawn`-started child processes so CPU-bound native code
such as InsightFace/onnxruntime can run concurrently without risking the Flask
process or the queue host.

This document is the reference for defining, enqueueing, composing, running, and
testing background tasks.

## Architecture

The task queue has three process roles:

```text
Flask app / watcher / task code
  producer only: enqueue task rows
        |
        v
queue.db SQLite store
        |
        v
taskq host
  scheduler, dispatcher, coordinator, supervisor
        |
        v
spawn worker children
  import task registry and run task functions
```

Key modules:

- `yaffo/taskq/core.py` — `TaskQueue`, task decorators, immediate mode, and graph
  coordination.
- `yaffo/taskq/signatures.py` — `task.s(...)`, `chord(...)`, and `.then(...)`
  composition primitives.
- `yaffo/taskq/store.py` — SQLite queue schema and persistence operations.
- `yaffo/taskq/host.py` — host process, scheduler, dispatcher, supervisor, and
  worker recycling.
- `yaffo/taskq/worker.py` — spawned child worker entry point.
- `yaffo/taskq/cron.py` — minute-granularity cron spec used by periodic tasks.
- `yaffo/background_tasks/config.py` — process-wide `task_queue` object.
- `yaffo/background_tasks/tasks/` — task definitions.
- `yaffo/background_tasks/periodic.py` — declarative periodic task list the host
  reads without importing task modules.

The host does not run task code and must not import `yaffo.background_tasks.tasks`.
Only worker children import the task registry. This keeps native ML libraries out
of the supervisor process, so a worker crash can be recorded and recovered without
killing the host.

## Running the Queue

Start only the task host:

```shell
inv start-tasks
inv start-tasks --workers=8 --recycle=200
```

Start the local app, task host, and watcher together:

```shell
inv app-local
```

Run the host directly:

```shell
python -m yaffo.taskq.host -w 4 -r 100
```

Host flags:

- `-w`, `--workers` — number of spawned worker children.
- `-r`, `--recycle` — number of tasks a child handles before exiting cleanly and
  being replaced.

The host defaults come from `config.toml` `[tasks] workers` and `recycle`, then
fall back to CPU count and `100`. CLI flags override config values.

The host logs to the `background_tasks` logger and records startup details,
worker count, recycle interval, periodic task names, task failures, worker
crashes, and shutdown events.

## Persistence Model

The durable queue file is `QUEUE_DB_PATH` from `yaffo.common`, normally
`queue.db` under the configured Yaffo data directory.

`Store` creates these tables:

- `task` — queued/running/completed task rows.
- `task_group` — chord barrier state and member results.
- `task_lock` — named locks for `@task_queue.lock_task(...)`.
- `periodic_state` — single-fire minute tracking for periodic tasks.
- `watcher_suppression` — file watcher self-write loop guard.

SQLite is opened with WAL mode, `busy_timeout=30000`, `synchronous=NORMAL`, and
thread-local connections.

The process boundary is strict:

- producers insert new `ready` task rows only;
- the host is the sole mutator of task status;
- worker children execute task functions and report results or exceptions back
  to the host over IPC.

Because the host is the only status mutator, dispatch is race-free without
cross-process status locking.

Task statuses:

- `ready`
- `running`
- `done`
- `error`
- `skipped`

On host startup, `running` tasks stranded by a previous host crash are requeued
to `ready`, and held task locks are cleared. This gives the queue at-least-once
execution semantics.

## Defining Tasks

Define tasks in `yaffo/background_tasks/tasks/` and decorate them with the
shared queue:

```python
from yaffo.background_tasks.config import task_queue


@task_queue.task()
def generate_example_task(example_id: int) -> None:
    ...
```

For tasks that need the queue row id, use `context=True`:

```python
from yaffo.taskq.core import TaskContext


@task_queue.task(context=True)
def find_duplicates_task(job_id: int, *, task: TaskContext) -> None:
    ...
```

`TaskContext.id` is the queue row id and is the value stored in `JobResult.task_id`
when a durable link between app job data and queue task data is needed.

Rules for task definitions:

- Imports go at module top level, following project style.
- Task arguments, keyword arguments, and return values must be JSON-safe:
  strings, numbers, booleans, `None`, lists, and dictionaries.
- Pass database ids and file paths as strings/numbers. Do not pass ORM objects,
  sessions, `Path` objects, datetimes, functions, classes, or open handles.
- Open database sessions inside the task and close them normally.
- A task must not depend on Flask request context.
- A task may enqueue more tasks, but it must preserve the same JSON-safe payload
  contract.
- A task must return `None` unless a downstream pipeline/chord step genuinely
  needs a JSON result.

The queue validates JSON-safe args at enqueue time and JSON-safe return values
immediately after task execution. Violations are task errors, not host crashes.

## Registering Tasks

Every task module that should run in workers must be imported by
`yaffo/background_tasks/tasks/__init__.py`. Worker children import that package
at startup for registration side effects.

The host uses `yaffo/background_tasks/periodic.py` for periodic scheduling and
must not import the task registry. When adding a periodic task, add both:

1. the decorated task function in a task module;
2. the `(task_name, cron_spec)` entry in `PERIODIC_TASKS`.

`task_name` is the Python function name captured by the decorator.

## Enqueueing Tasks

Calling a decorated task enqueues it and returns a lightweight `Result` handle:

```python
result = generate_example_task(example_id)
task_id = result.id
```

The app does not block on queue results. User-visible progress belongs in the
`Job` and `JobResult` tables, not in queue result polling.

Delayed execution:

```python
complete_job_task.schedule(args=(job_id,), delay=3)
```

Delayed tasks become claimable when their `eta` has passed.

## Composition

Use signatures for deferred calls:

```python
import_sig = import_photo_task.s(path, job_id)
index_sig = index_photo_task.s(path, job_id)
```

Use `.then(...)` for pipelines:

```python
pipeline = import_photo_task.s(path, job_id).then(index_photo_task, path, job_id)
task_queue.enqueue(pipeline)
```

Pipeline semantics:

- each step runs after the previous step finishes;
- the previous step result is appended positionally to the next step's args;
- tasks that do not need the previous result should accept an optional trailing
  argument, such as `prev_result=None`, when they are used in a pipeline.

Use `chord(members, callback)` for a group plus barrier callback:

```python
from yaffo.taskq import chord

members = [index_photo_task.s(path, job_id) for path in paths]
pipeline = chord(members, complete_job_callback.s(job_id))
task_queue.enqueue(pipeline)
```

Chord semantics:

- every member runs independently;
- the callback fires once after all members finish;
- the member result list is appended positionally to the callback args;
- an empty member list fires the callback immediately with an empty result list;
- chord pipelines can continue with `.then(...)`.

Failures do not wedge a graph. If a task raises or a worker crashes, the host
marks that row `error`, advances the graph with `None`, and logs the failure.
Callbacks and continuations must be written to tolerate `None` results when they
can follow a failed task.

## Locks

Use `@task_queue.lock_task(name)` together with `@task_queue.task()` when only one
task with a given logical lock may run at a time:

```python
@task_queue.task()
@task_queue.lock_task("file-sync")
def file_sync_task() -> None:
    ...
```

If the lock is already held, the host marks the later task `skipped`. It does not
wait, retry, or queue behind the lock holder. Use this only for work where
dropping overlapping runs is correct.

The host releases a held lock when the worker reports a result or when the worker
dies. Startup recovery clears all stale locks.

## Periodic Tasks

Periodic scheduling is minute-granularity only. The host claims each
`(task_name, minute)` once in `periodic_state` and inserts the corresponding task
row.

The queue-level cron support is intentionally small. Application-level schedules,
including automation cron expressions and `next_run_at`, are evaluated by the
automation scheduler task. The periodic queue task only drives that scheduler
once per minute.

## Idempotency and Replay Safety

The queue is at-least-once. A host crash can requeue a task that had already
started in a worker. Every task must therefore be replay-safe.

Acceptable idempotency patterns:

- write through unique constraints;
- delete-then-insert for derived rows;
- check whether the target work is already complete before writing;
- persist a stable task id or operation id and guard on it;
- make repeated file operations harmless with existence checks.

Do not rely on "this task only runs once." Chords, delayed tasks, periodic tasks,
and worker crash recovery all depend on tasks being safe to replay.

For media indexing, this rule is especially important because generated
thumbnails and derived face rows can otherwise duplicate or leak files. Prefer a
clear cleanup/write sequence over trying to merge partial derived data.

## Cancellation

There is no queue-level revoke. Cancellation is cooperative through app state,
usually `Job.status`.

Long-running tasks must poll the relevant app status helper, such as
`get_job_status(job_id)`, and exit cleanly when cancellation is observed.

## Error Handling

Worker behavior:

- task exceptions are caught in the child and sent to the host with a traceback;
- non-JSON return values become task failures;
- native crashes kill only the child process.

Host behavior:

- successful tasks are marked `done` and coordinated;
- failed tasks are marked `error` and coordinated with `None`;
- crashed workers are respawned;
- result-recording failures are logged and converted to task errors when
  possible.

Task code should still record user-facing failure state in the app database when
that state is part of the feature contract. The queue status is operational
state, not the primary UI progress model.

## Immediate Mode for Tests

Set `task_queue.immediate = True` in tests that need synchronous in-process
execution of real task graphs. Immediate mode:

- does not use `queue.db`;
- runs task functions in the current process;
- preserves pipeline/chord result passing;
- creates synthetic context ids for `context=True` tasks;
- still validates JSON-safe return values.

Always restore `task_queue.immediate` after the test.

Use immediate mode for composition-level tests and route tests where the work
must complete before assertions. Use persistent queue/host tests for store,
coordination, spawn, crash, and lock behavior.

## Testing Standards

Task changes should include tests proportional to the behavior changed.

Use these existing test areas as anchors:

- `tests/yaffo/taskq/test_coordinator.py` — persistent store coordination.
- `tests/yaffo/taskq/test_host_spawn.py` — spawn pool and crash isolation.
- `tests/yaffo/utils/test_index_jobs.py` — immediate-mode import/index graph.
- feature route/task tests near the feature being changed.

Required coverage patterns:

- task payloads are JSON-safe;
- app-visible progress lands in `Job` / `JobResult` as expected;
- composed graphs fire callbacks and continuations exactly once;
- empty groups behave intentionally;
- task replay is safe for writes the task performs;
- cancellation paths leave the app in a coherent state;
- failures are recorded without wedging downstream coordination;
- lock-protected work skips overlapping runs when that is the intended behavior.

Windows remains a target platform for Yaffo, but the spawn host and IPC behavior
have primarily been exercised on macOS. Changes to signal handling, process
termination, queue file locking, or multiprocessing setup should be validated on
Windows before treating that path as supported.

## Operational Boundaries

The queue is single-host and embedded. It is not a distributed task system.

Supported:

- durable local SQLite queue;
- local Flask producer, watcher producer, and worker children sharing the same
  queue file;
- delayed tasks;
- minute periodic tasks;
- pipelines and chords;
- named skip locks;
- cooperative cancellation through app tables;
- worker crash isolation and recycling.

Not supported:

- multi-machine workers;
- external brokers;
- task priorities;
- automatic retries;
- result polling as a product API;
- queue-level revoke;
- greenlet/thread worker pools;
- arbitrary cron evaluation in the queue layer.

## Historical Note

This queue replaced Huey after Huey thread and process workers proved unsuitable
for Yaffo's native ML workload on the target platforms. The important enduring
constraint is process isolation with `spawn`: native inference runs in worker
children, while the host stays small, durable, and recoverable.
