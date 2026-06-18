"""Guardrails for the two orchestration footguns that otherwise fail deep in a
worker or the store instead of at the call site:

1. **JSON-safety**: task args/kwargs must be JSON-serializable (they're persisted
   as JSON and results are JSON-appended downstream). `enqueue` asserts this.
2. **Result-append contract**: a chord callback is called with the member-results
   list appended, and a `.then(...)` step with the previous step's result appended.
   Every task used as a callback / pipeline continuation must therefore accept one
   extra trailing positional arg. This drift guard pins the known composition
   edges so dropping that trailing param fails here, not at runtime.
"""
import inspect

import pytest

import yaffo.background_tasks.tasks as tasks  # registers the real registry
from yaffo.taskq import TaskQueue, chord
from yaffo.taskq.signatures import Pipeline, SingleLink

pytestmark = pytest.mark.unit


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(filename=str(tmp_path / "queue.db"), immediate=False)


def test_enqueue_rejects_non_json_args(queue):
    @queue.task()
    def t(payload):
        return None

    with pytest.raises(TypeError, match="non-JSON-serializable"):
        queue.enqueue(Pipeline([SingleLink(t.s(object()))]))


def test_enqueue_rejects_non_json_in_chord_member(queue):
    @queue.task()
    def member(x):
        return None

    @queue.task()
    def cb(job, results=None):
        return None

    with pytest.raises(TypeError, match="non-JSON-serializable"):
        queue.enqueue(chord([member.s({1, 2, 3})], cb.s("job")))  # set isn't JSON


def test_non_json_return_raises_in_immediate_mode(tmp_path):
    """The return-value guardrail: a task returning a non-JSON value fails loudly
    (in immediate mode it surfaces as a TypeError to the caller; in the worker it
    becomes a clean task failure)."""
    q = TaskQueue(filename=str(tmp_path / "queue.db"), immediate=True)

    @q.task()
    def bad():
        return {1, 2, 3}  # a set isn't JSON-serializable

    with pytest.raises(TypeError, match="returned a non-JSON-serializable"):
        bad()


def _accepts_appended_result(fn, n_bound: int) -> bool:
    """Can `fn` be called with n_bound positional args plus one appended result?"""
    params = list(inspect.signature(fn).parameters.values())
    if any(p.kind == p.VAR_POSITIONAL for p in params):
        return True
    positional = [
        p for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.name != "task"
    ]
    return len(positional) >= n_bound + 1


# (task, number of args bound at the call site) for every composition edge where a
# result is appended. Extend this when adding a new chord callback or .then() step.
APPENDED_RESULT_EDGES = [
    (tasks.complete_job_callback, 1),  # chord callback: gets [member results]
    (tasks.start_index_stage, 1),      # .then continuation: gets prev_result
]


@pytest.mark.parametrize("fn, n_bound", APPENDED_RESULT_EDGES)
def test_composition_targets_accept_appended_result(fn, n_bound):
    assert _accepts_appended_result(fn.fn, n_bound), (
        f"{fn.name} is used as a chord callback / pipeline step but cannot accept "
        f"the appended result as a trailing positional arg"
    )
