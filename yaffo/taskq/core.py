"""The task queue facade and execution coordinator.

`TaskQueue` is the per-process object every task module decorates against (the
analogue of the old `huey` singleton). Calling a decorated task enqueues it;
`task.s(...)` / `chord(...)` / `.then(...)` compose graphs; `enqueue(pipeline)`
persists (or, in immediate mode, runs in-process synchronously for tests).

Two execution paths share the composition contract:
- **immediate** (tests): recursive, depth-first, in-process; no store, no spawn.
- **persistent** (host): rows in `queue.db`; the host runs leaves in spawn workers
  and calls the coordinator here (`on_task_finished`) to fire chord callbacks and
  pipeline continuations.

The result of each step is appended to the next step's args; a chord callback gets
the list of member results appended -- matching Huey's positional contract (our
tasks return None and ignore the appended value, but the signatures rely on it).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from yaffo.taskq.cron import CronSpec
from yaffo.taskq.signatures import (
    ChordLink, Link, Pipeline, Signature, SingleLink, links_from_json, links_to_json,
)
from yaffo.taskq.store import Store, TaskRow

_NO_PREV = object()


@dataclass
class TaskContext:
    """Passed as the `task=` kwarg to tasks declared with `context=True`; `.id` is
    the queue row id (the replacement for Huey's `task.id`)."""
    id: str


class Result:
    """Lightweight handle returned by enqueuing a task. The app never blocks on
    results (progress lives in the Job table), so this only exposes the row id."""
    def __init__(self, id: Optional[str], value: Any = None):
        self.id = id
        self._value = value

    def __call__(self, *args, **kwargs) -> Any:  # pragma: no cover - parity shim
        return self._value


class Task:
    def __init__(
        self,
        queue: "TaskQueue",
        fn: Callable,
        name: str,
        *,
        context: bool = False,
        lock_name: Optional[str] = None,
    ):
        self.queue = queue
        self.fn = fn
        self.name = name
        self.context = context
        self.lock_name = lock_name

    def s(self, *args, **kwargs) -> Signature:
        return Signature(
            task_name=self.name,
            args=tuple(args),
            kwargs=dict(kwargs),
            context=self.context,
            lock_name=self.lock_name,
        )

    def __call__(self, *args, **kwargs) -> Result:
        return self.queue.enqueue(Pipeline([SingleLink(self.s(*args, **kwargs))]))

    def schedule(self, args: tuple = (), delay: float = 0) -> Result:
        return self.queue.enqueue(
            Pipeline([SingleLink(self.s(*args))]), delay=delay
        )


class TaskQueue:
    def __init__(self, filename: str, immediate: bool = False):
        self.filename = filename
        self.immediate = immediate
        self.registry: dict[str, Task] = {}
        self.periodic: list[tuple[str, CronSpec]] = []
        self._store: Optional[Store] = None

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.filename)
        return self._store

    # ---- registration ---------------------------------------------------

    def task(self, context: bool = False) -> Callable[[Callable], Task]:
        def deco(fn: Callable) -> Task:
            t = Task(
                self, fn, fn.__name__,
                context=context,
                lock_name=getattr(fn, "_taskq_lock_name", None),
            )
            self.registry[t.name] = t
            return t
        return deco

    def periodic_task(self, cron: CronSpec) -> Callable[[Callable], Task]:
        def deco(fn: Callable) -> Task:
            t = Task(self, fn, fn.__name__)
            self.registry[t.name] = t
            self.periodic.append((t.name, cron))
            return t
        return deco

    def lock_task(self, name: str) -> Callable[[Callable], Callable]:
        def deco(fn: Callable) -> Callable:
            fn._taskq_lock_name = name
            return fn
        return deco

    # ---- enqueue --------------------------------------------------------

    def enqueue(self, pipeline: Pipeline, delay: float = 0) -> Result:
        if self.immediate:
            value = self._run_immediate(pipeline.links, _NO_PREV)
            return Result(None, value)
        import time
        eta = (time.time() + delay) if delay else None
        task_id = enqueue_pipeline_rows(self.store, pipeline.links, _NO_PREV, eta=eta)
        return Result(task_id)

    # ---- immediate (synchronous, in-process) ----------------------------

    def _run_immediate(self, links: list[Link], prev: Any) -> Any:
        for link in links:
            if isinstance(link, SingleLink):
                args = list(link.sig.args)
                if prev is not _NO_PREV:
                    args.append(prev)
                prev = self._run_one(link.sig.task_name, args, dict(link.sig.kwargs))
            elif isinstance(link, ChordLink):
                if not link.members:
                    # An empty group: Huey's chord callback never fires, so neither
                    # does ours, and the chain stops. (The app avoids this by
                    # finalising empty stages directly.)
                    prev = _NO_PREV
                    continue
                results = [
                    self._run_one(m.task_name, list(m.args), dict(m.kwargs))
                    for m in link.members
                ]
                if link.callback is not None:
                    cb = link.callback
                    prev = self._run_one(cb.task_name, list(cb.args) + [results], dict(cb.kwargs))
                else:
                    prev = results
        return None if prev is _NO_PREV else prev

    def _run_one(self, name: str, args: list, kwargs: dict) -> Any:
        task = self.registry[name]
        if task.context:
            kwargs["task"] = TaskContext(id=str(uuid.uuid4()))
        return task.fn(*args, **kwargs)


# ---- coordinator (shared by host; pure functions over a Store) ----------

def enqueue_pipeline_rows(
    store: Store, links: list[Link], prev: Any, eta: Optional[float] = None
) -> Optional[str]:
    """Materialise the head of a pipeline as queue rows. The tail (remaining
    links) rides along as the head's `continuation`, replayed by `on_task_finished`
    when the head completes. Returns the head task id (None for a chord head)."""
    if not links:
        return None
    head, rest = links[0], links[1:]

    if isinstance(head, SingleLink):
        sig = head.sig
        args = list(sig.args)
        if prev is not _NO_PREV:
            args.append(prev)
        return store.insert_task(
            sig.task_name, args, dict(sig.kwargs),
            context=sig.context, lock_name=sig.lock_name, eta=eta,
            continuation=links_to_json(rest) if rest else None,
        )

    # ChordLink
    members = head.members
    callback_json = json.dumps(head.callback.to_dict()) if head.callback else None
    continuation_json = json.dumps(links_to_json(rest)) if rest else None
    if not members:
        # Empty group -> callback never fires (Huey semantics). The app finalises
        # empty stages directly, so this graph is never enqueued in practice.
        return None
    group_id = store.create_group(callback_json, continuation_json, len(members))
    for m in members:
        store.insert_task(
            m.task_name, list(m.args), dict(m.kwargs),
            context=m.context, lock_name=m.lock_name, group_id=group_id,
        )
    return None


def on_task_finished(store: Store, row: TaskRow, result: Any) -> None:
    """Fire follow-ups after a leaf task completes: advance the chord group it
    belongs to (and run the callback once the barrier is met), or replay its
    pipeline continuation with `result` appended."""
    if row.group_id:
        group = store.add_group_result(row.group_id, result)
        if group.finished < group.total:
            return
        continuation = links_from_json(json.loads(group.continuation_json)) if group.continuation_json else []
        if group.callback_json:
            cb = Signature.from_dict(json.loads(group.callback_json))
            store.insert_task(
                cb.task_name, list(cb.args) + [group.results], dict(cb.kwargs),
                context=cb.context, lock_name=cb.lock_name,
                continuation=links_to_json(continuation) if continuation else None,
            )
        elif continuation:
            enqueue_pipeline_rows(store, continuation, group.results)
        return

    if row.continuation:
        enqueue_pipeline_rows(store, links_from_json(row.continuation), result)
