"""Automation tool: write_automation_code.

The tool the automation-builder agent calls to author a custom automation. The
provider is scoped to a single automation **slug** at construction, so the model
can only touch that automation. Like the theme/widget tools it **persists directly
into the automation's `working_code`** (the durable draft) so a generation survives
disconnect and the browser observes it by polling; `published_code` is untouched
until the user publishes.

The code is sandboxed Starlark (see background_tasks/automation_sandbox). The tool
parse-checks it and returns any syntax error to the model to fix and retry, so a
draft is never saved unparseable.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.starlark_runner import validate_starlark
from yaffo.db.repositories import automation_repository as repo
from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)


class AutomationToolProvider(ToolProvider):
    WRITE = "write_automation_code"

    def __init__(self, slug: str, session: Session):
        self.slug = slug
        self.session = session

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(
                name=self.WRITE,
                description=(
                    "Save the automation's Starlark code as the working draft. Call again "
                    "to refine — each call replaces the draft. The code is parse-checked; "
                    "fix any reported error and call again."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "The Starlark script. It reads `ctx` (the trigger context) and "
                                "calls the host API (e.g. data_query); print() to log. No while "
                                "loops, recursion, imports, or I/O."
                            ),
                        },
                    },
                    "required": ["code"],
                    "additionalProperties": False,
                },
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name == self.WRITE:
            return self._write(args or {})
        return f"Unknown tool: {name}"

    def _write(self, args: dict) -> CallToolReturn:
        code = (args.get("code") or "").strip()
        if not code:
            return "Automation not saved — `code` is required."

        error = validate_starlark(code)
        if error is not None:
            return (
                "Automation not saved — the code did not parse. Fix this and call "
                f"write_automation_code again:\n{error}"
            )

        if not repo.write_working_code(self.session, self.slug, code):
            return f"Automation {self.slug!r} not found."

        return ToolResult(
            model_text=f"Saved the working draft for {self.slug!r} ({len(code)} chars).",
            host_data={"slug": self.slug, "working_code": code},
        )
