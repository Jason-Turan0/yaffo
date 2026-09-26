"""The run_script tool: a Starlark script over the library, in the automation sandbox.

The script runs hermetically (no I/O, no imports, time/call/output limited) with
only the READ host functions of the `assistant` profile bound (data_query,
match_people, face_similarity), so it can look things up but has no way to
change anything. Change plans (recording mutations for the user to approve) come
in a later phase; until then a script that calls a mutating function simply
fails with an unknown-name error.

The model gets the script's value, print output and error, redacted and capped.
The chat gets the same text plus the script source ("Show script").
"""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.automation_host import build_host_functions
from yaffo.background_tasks.automation_sandbox.starlark_runner import RunLimits, run_starlark
from yaffo.db.repositories.data_query_repository import AGGREGATE_OPS, SOURCES, source_schema
from yaffo.site_agents.assistant.redact import Redactor
from yaffo.site_agents.assistant.schemas import ToolActivity
from yaffo.site_agents.common.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)
from yaffo.site_agents.common.tool_providers.utils import truncate_tool_result

RUN_SCRIPT = "run_script"
DESCRIBE_SOURCE = "describe_data_source"
PROFILE = "assistant"
MAX_CODE_CHARS = 20000
MAX_PURPOSE_CHARS = 120
MAX_VALUE_CHARS = 8000
MAX_RESULT_CHARS = 12000
LIMITS = RunLimits(timeout_seconds=30.0, max_host_calls=200, max_output_chars=20000)

_DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "enum": list(SOURCES), "description": "The data_query source to describe."},
    },
    "required": ["source"],
    "additionalProperties": False,
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "A Starlark script. Its last expression is returned as the value; print() output is returned too.",
        },
        "purpose": {
            "type": "string",
            "description": "A short label shown to the user, e.g. 'Count photos from 2019 by month'.",
        },
    },
    "required": ["code", "purpose"],
    "additionalProperties": False,
}


def read_host_functions(session: Session) -> dict[str, Any]:
    """The assistant profile's read-only host functions, bound to `session`."""
    return build_host_functions(session, profile=PROFILE, include_mutating=False)


def _render_value(value: Any) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = repr(value)
    if len(rendered) > MAX_VALUE_CHARS:
        rendered = rendered[:MAX_VALUE_CHARS] + f"… [value truncated, {len(rendered)} chars]"
    return rendered


class ScriptToolProvider(ToolProvider):
    def __init__(self, session: Session, *, redactor: Optional[Redactor] = None, limits: RunLimits = LIMITS):
        self.session = session
        self.redactor = redactor or Redactor()
        self.limits = limits

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(
                DESCRIBE_SOURCE,
                "List the fields (name, type, description) of a data_query source, so a script "
                f"queries real columns. Sources: {', '.join(SOURCES)}. Aggregate ops: "
                f"{', '.join(AGGREGATE_OPS)}.",
                _DESCRIBE_SCHEMA,
            ),
            RawToolDefinition(
                RUN_SCRIPT,
                "Run a read-only Starlark script against the library (see <scripts>) and get back its value, "
                "printed output and any error. Use it for questions about the user's photos, people, tags, "
                "albums and locations. It can't change anything.",
                _SCHEMA,
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name == DESCRIBE_SOURCE:
            return self._describe(str(args.get("source") or ""))
        if name != RUN_SCRIPT:
            raise ValueError(f"Unknown tool: {name}")
        code = str(args.get("code") or "")
        purpose = " ".join(str(args.get("purpose") or "").split())[:MAX_PURPOSE_CHARS]
        if not code.strip():
            return self._result("The script is empty.", code, purpose, error=True)
        if len(code) > MAX_CODE_CHARS:
            return self._result(f"The script is longer than {MAX_CODE_CHARS} characters.", code, purpose, error=True)

        result = run_starlark(
            code, functions=read_host_functions(self.session), filename="assistant.star", limits=self.limits)
        parts: list[str] = []
        if result.success:
            parts.append(f"Value: {_render_value(result.value)}")
        else:
            parts.append(f"Error: {result.error}")
        if result.output:
            parts.append("Printed:\n" + "\n".join(result.output))
        count = len(result.value) if result.success and isinstance(result.value, (list, dict)) else 0
        return self._result("\n".join(parts), code, purpose, error=not result.success, count=count)

    def _describe(self, source: str) -> ToolResult:
        try:
            fields = source_schema(source)
            text, error = json.dumps({"source": source, "fields": fields}, indent=1), False
        except ValueError as exc:
            text, error = f"Invalid source: {exc}", True
        activity = ToolActivity(tool=DESCRIBE_SOURCE, title=source, error=error)
        return ToolResult(model_text=truncate_tool_result(text, MAX_RESULT_CHARS), host_data=activity.to_dict())

    def _result(self, text: str, code: str, purpose: str, *, error: bool, count: int = 0) -> ToolResult:
        text = truncate_tool_result(self.redactor(text), MAX_RESULT_CHARS)
        activity = ToolActivity(
            tool=RUN_SCRIPT, purpose=purpose, script=code, detail=text, error=error, count=count)
        return ToolResult(
            model_text=f"<data source=\"{RUN_SCRIPT}\">\n{text}\n</data>",
            host_data=activity.to_dict(),
        )
