"""Sandbox for running custom (AI-generated) automation code.

- starlark_runner: the generic, hermetic Starlark execution wrapper.
- automation_host: the declarative host API (data_query, ...) exposed to scripts,
  the single source for both the runtime bindings and the agent docs.
- executor: runs an Automation's `code` with the trigger context + host API.
"""
from yaffo.background_tasks.automation_sandbox.automation_host import (
    HOST_API,
    HostFunction,
    build_host_functions,
    render_host_api,
)
from yaffo.background_tasks.automation_sandbox.executor import run_automation
from yaffo.background_tasks.automation_sandbox.starlark_runner import (
    StarlarkResult,
    run_starlark,
)

__all__ = [
    "HOST_API",
    "HostFunction",
    "build_host_functions",
    "render_host_api",
    "run_automation",
    "StarlarkResult",
    "run_starlark",
]
