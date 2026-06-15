from dataclasses import dataclass, field
from typing import Any, Callable

import starlark

from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')

# Builtins available to sandboxed code. Starlark's core is already hermetic (no
# file/network I/O, no imports, no `while`/recursion); these only add pure
# helpers (JSON, map/filter). `print` is supplied per-run as a capturing callable,
# so the Print extension is deliberately omitted. Nothing here can reach host
# state -- the only host surface is the callables passed into run_starlark.
_EXTENSIONS = [
    starlark.LibraryExtension.Json,
    starlark.LibraryExtension.Map,
    starlark.LibraryExtension.Filter,
]

# The "extended" dialect allows top-level control flow (for/if), f-strings and
# lambda -- so authored scripts read naturally -- while staying hermetic: `load`
# is inert because run_starlark passes no file loader, and there is still no
# `while`/recursion or any I/O builtin.
_DIALECT = starlark.Dialect.extended()


@dataclass(frozen=True)
class StarlarkResult:
    """Outcome of one sandboxed run. `value` is the snippet's trailing expression
    (None when it ends on a statement); `output` is captured print() lines; on
    failure `success` is False and `error` carries the Starlark message."""
    success: bool
    value: Any = None
    output: list[str] = field(default_factory=list)
    error: str | None = None


def run_starlark(
    code: str,
    *,
    inputs: dict[str, Any] | None = None,
    functions: dict[str, Callable[..., Any]] | None = None,
    filename: str = "automation.star",
) -> StarlarkResult:
    """Evaluate a Starlark snippet in a sandbox and capture its result.

    Starlark is deterministic and hermetic: no file/network I/O, no imports, no
    `while` loops or recursion -- so host state is unreachable except through the
    callables passed in `functions`. `inputs` are injected as module globals (e.g.
    a context dict the snippet reads); the returned `value` is the trailing
    expression. `print(...)` is captured into `output` instead of reaching stdout.

    Any parse or evaluation failure is returned as `success=False` with `error`
    set -- this function never raises StarlarkError, so callers (e.g. the
    automation executor) can treat a bad script as data, not an exception.
    """
    output: list[str] = []

    def _capture_print(*args: Any) -> None:
        output.append(" ".join(str(a) for a in args))

    try:
        ast = starlark.parse(filename, code, _DIALECT)
        module = starlark.Module()
        for name, value in (inputs or {}).items():
            module[name] = value
        module.add_callable("print", _capture_print)
        for name, fn in (functions or {}).items():
            module.add_callable(name, fn)
        globals_ = starlark.Globals.extended_by(_EXTENSIONS)
        value = starlark.eval(module, ast, globals_)
        return StarlarkResult(success=True, value=value, output=output)
    except starlark.StarlarkError as exc:
        return StarlarkResult(success=False, output=output, error=str(exc))
