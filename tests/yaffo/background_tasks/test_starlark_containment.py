"""Containment tests for the Starlark sandbox (background_tasks/starlark_runner).

run_starlark must keep authored automation code hermetic: no Python eval/introspection
builtins, no filesystem or stdlib reach, no `import`, no attribute-walking to host
internals, and no host surface beyond the explicitly injected callables. Every escape
attempt must come back as data (success=False, error set) — never raise, never execute.

Complements the happy-path + basic-escape cases in test_starlark_runner.py.

Not asserted: this binding does NOT block recursion or bound CPU/time (a large bounded
loop or recursion still runs) — that's the documented "Deferred: hard CPU/time limit"
gap in docs/automations.md, so the only true hardening is a subprocess/resource limit.
"""
import pytest

from yaffo.background_tasks.automation_sandbox.starlark_runner import run_starlark

pytestmark = pytest.mark.unit


def _assert_contained(snippet, functions=None):
    """The snippet must fail closed: returned as data, not raised, with no value."""
    result = run_starlark(snippet, functions=functions)
    assert result.success is False, f"expected containment failure, got value={result.value!r}"
    assert result.error
    assert result.value is None
    return result


@pytest.mark.parametrize("snippet", [
    "eval('1 + 1')",
    "exec('x = 1')",
    "compile('1', 'x', 'eval')",
    "globals()",
    "locals()",
    "vars()",
    "dir()",
    "getattr(len, 'x')",
    "setattr([], 'x', 1)",
    "input()",
])
def test_python_eval_and_introspection_builtins_are_absent(snippet):
    _assert_contained(snippet)


@pytest.mark.parametrize("snippet", [
    "open('/etc/passwd')",
    "open('/tmp/escape', 'w')",
])
def test_no_filesystem_io(snippet):
    _assert_contained(snippet)


@pytest.mark.parametrize("module", ["os", "sys", "subprocess", "socket", "time", "random", "pathlib"])
def test_no_ambient_stdlib_modules(module):
    # The module names were never injected, so they're just unknown variables.
    _assert_contained(f"{module}.anything")


@pytest.mark.parametrize("snippet", [
    "socket.socket()",
    "urllib.request.urlopen('http://example.com')",
    "urllib2.urlopen('http://example.com')",
    "http.client.HTTPConnection('example.com')",
    "requests.get('http://example.com')",
    "ftplib.FTP('example.com')",
    "smtplib.SMTP('example.com')",
    "asyncio.open_connection('example.com', 80)",
])
def test_no_network_access(snippet):
    # Python has no *builtin* network function (unlike open() for files); networking
    # always goes through an importable module. With no import and none injected, every
    # network entry point is just an unknown variable — so blocking modules covers it.
    _assert_contained(snippet)


def test_import_statement_is_rejected():
    # `import` is a reserved keyword in the dialect — a parse error, so nothing runs.
    _assert_contained("import os")


@pytest.mark.parametrize("snippet", [
    "print.__class__",            # a builtin callable
    "json.encode.__globals__",    # an extension callable
    "[].__class__",               # a literal value
    "(1).__class__",
])
def test_cannot_walk_attributes_to_host_internals(snippet):
    _assert_contained(snippet)


def test_injected_function_does_not_expose_python_internals():
    def host_fn():
        return "ok"

    # The dunder that would reach the host process is unreachable...
    _assert_contained("host_fn.__globals__", functions={"host_fn": host_fn})
    # ...while the function itself is callable normally.
    assert run_starlark("host_fn()", functions={"host_fn": host_fn}).value == "ok"


def test_only_injected_host_functions_are_reachable():
    calls = []
    functions = {"tag_photo": lambda *args: calls.append(args)}

    # The injected name resolves and runs.
    assert run_starlark("tag_photo(1, 'x')", functions=functions).success is True
    assert calls == [(1, "x")]

    # A host function that was NOT injected is just an unknown variable — the script
    # can't reach the wider host API, only what this run was given.
    _assert_contained("data_query({'source': 'media_items'})", functions=functions)


def test_a_failed_escape_runs_no_later_statements():
    # Containment fails the whole run: a side effect after the blocked call must not fire.
    calls = []
    functions = {"record": lambda: calls.append(True)}

    _assert_contained("eval('1')\nrecord()", functions=functions)
    assert calls == []
