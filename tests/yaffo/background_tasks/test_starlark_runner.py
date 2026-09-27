"""Unit tests for the Starlark sandbox wrapper (background_tasks/starlark_runner).

These run real Starlark through the wrapper -- no mocking of the interpreter. They
pin the two things the automation executor relies on: the happy-path contract
(value/output/inputs/host functions) and the sandbox guarantees (failures come
back as data, and host state is unreachable except via injected callables).
"""
import pytest

from yaffo.background_tasks.automation_sandbox.starlark_runner import run_starlark, StarlarkResult

pytestmark = pytest.mark.unit


def test_returns_trailing_expression_value():
    result = run_starlark("x = 1 + 2\nx * 10")
    assert result.success is True
    assert result.value == 30
    assert result.error is None


def test_statement_ending_yields_none_value_but_succeeds():
    result = run_starlark("x = 5")
    assert result.success is True
    assert result.value is None


def test_inputs_are_injected_as_globals():
    result = run_starlark(
        "ctx['media_item_ids'][0] + offset",
        inputs={"ctx": {"media_item_ids": [10, 20]}, "offset": 5},
    )
    assert result.success is True
    assert result.value == 15


def test_host_functions_are_callable_and_receive_args():
    recorded = []

    def tag_photo(media_item_id, name):
        recorded.append((media_item_id, name))
        return True

    result = run_starlark(
        "tag_photo(42, 'beach')\ntag_photo(43, 'beach')",
        functions={"tag_photo": tag_photo},
    )
    assert result.success is True
    assert recorded == [(42, "beach"), (43, "beach")]


def test_print_is_captured_not_raised(capsys):
    result = run_starlark("print('hello', 1 + 2)\nprint('again')")
    assert result.success is True
    assert result.output == ["hello 3", "again"]
    # nothing leaked to stdout
    assert capsys.readouterr().out == ""


def test_top_level_control_flow_allowed():
    # The extended dialect permits top-level for/if (no def wrapper needed), so
    # authored automation scripts read naturally.
    result = run_starlark(
        "out = []\nfor n in [1, 2, 3]:\n    if n != 2:\n        out.append(n)\nout"
    )
    assert result.success is True
    assert result.value == [1, 3]


def test_standard_builtins_available():
    result = run_starlark("len([1, 2, 3]) + len('ab')")
    assert result.success is True
    assert result.value == 5


def test_json_extension_available():
    result = run_starlark("json.encode({'a': 1})")
    assert result.success is True
    assert result.value == '{"a":1}'


def test_parse_error_returned_as_data():
    result = run_starlark("def (:")
    assert isinstance(result, StarlarkResult)
    assert result.success is False
    assert result.error  # non-empty message
    assert result.value is None


def test_runtime_error_returned_as_data():
    result = run_starlark("1 // 0")
    assert result.success is False
    assert result.error


def test_partial_output_retained_on_error():
    result = run_starlark("print('before')\n1 // 0")
    assert result.success is False
    assert result.output == ["before"]


@pytest.mark.parametrize("snippet", [
    "while True:\n    pass",        # unbounded loops are disallowed by Starlark
    "load('other.star', 'x')",      # no module loading (no file loader supplied)
    "open('/etc/passwd')",          # no host I/O builtins
    "__import__('os')",             # no Python import escape
    "print.__class__",              # no attribute-walking to host internals
])
def test_sandbox_blocks_escapes(snippet):
    result = run_starlark(snippet)
    assert result.success is False
    assert result.error


def test_host_function_exception_surfaces_as_failure():
    def boom():
        raise ValueError("kaboom")

    result = run_starlark("boom()", functions={"boom": boom})
    assert result.success is False
    assert result.error


def test_runs_are_isolated_no_shared_globals():
    run_starlark("leaked = 99")
    result = run_starlark("leaked")
    assert result.success is False  # 'leaked' must not persist across runs


def test_should_cancel_kills_a_running_script_and_reports_cancelled():
    started = []

    result = run_starlark(
        "def spin():\n    n = 0\n    for i in range(1000000000):\n        n += i\n    return n\n"
        "print('start')\nmark_started()\nspin()",
        functions={"mark_started": lambda: started.append(True)},
        should_cancel=lambda: bool(started),
        cancel_poll_seconds=0.05,
    )

    assert result.cancelled is True and result.success is False
    assert result.error == "Cancelled"
    assert result.output == ["start"]


def test_should_cancel_false_lets_the_script_finish():
    result = run_starlark("1 + 1", should_cancel=lambda: False, cancel_poll_seconds=0.01)
    assert result.success is True and result.cancelled is False and result.value == 2


def test_a_cancel_during_a_host_call_lets_the_call_finish_then_stops_the_script():
    """Scenario 6: a host call in progress is never interrupted (killing a DB write
    mid-commit is unsafe); the cancel is seen on the next message, so the script's
    next host call never runs."""
    import time
    calls = []
    cancelled = []

    def slow_write():
        cancelled.append(True)  # the cancel lands while this call is running
        time.sleep(0.3)
        calls.append("slow_write finished")

    result = run_starlark(
        "slow_write()\nafter()",
        functions={"slow_write": slow_write, "after": lambda: calls.append("after ran")},
        should_cancel=lambda: bool(cancelled),
        cancel_poll_seconds=0.05,
    )

    assert calls == ["slow_write finished"]
    assert result.cancelled is True
