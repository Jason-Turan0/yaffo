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

import json
from typing import Any

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.automation_host import (
    build_recording_host_functions,
    summarize_call,
)
from yaffo.background_tasks.automation_sandbox.executor import run_automation_code
from yaffo.background_tasks.automation_sandbox.starlark_runner import validate_starlark
from yaffo.background_tasks.events import EventContext
from yaffo.db.models import TRIGGER_TYPE_EVENT
from yaffo.db.repositories import automation_repository as repo
from yaffo.db.repositories import data_query_repository
from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)


class AutomationToolProvider(ToolProvider):
    WRITE = "write_automation_code"
    TEST = "test_automation_code"

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
            RawToolDefinition(
                name=self.TEST,
                description=(
                    "Test an automation Starlark script non-destructively against indexed "
                    "media under a media directory/path. Read-only host calls run so the "
                    "script gets real data; mutating host calls are recorded but not "
                    "performed. Use this before saving when you need to see the actions "
                    "that would occur."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "The Starlark script to test. This is not saved. It reads "
                                "`ctx` and calls the automation host API."
                            ),
                        },
                        "media_dir_id": {
                            "type": "string",
                            "description": (
                                "The media directory id to test against. Obtain it from "
                                "media_dirs or from media item rows' media_dir_id."
                            ),
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "Relative path within the media directory. Use an exact file "
                                "path to test one item, a folder path to test that subtree, "
                                "or an empty string for the media directory root."
                            ),
                        },
                    },
                    "required": ["code", "media_dir_id", "path"],
                    "additionalProperties": False,
                },
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name == self.WRITE:
            return self._write(args or {})
        if name == self.TEST:
            return self._test(args or {})
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

    def _test(self, args: dict) -> CallToolReturn:
        code = (args.get("code") or "").strip()
        media_dir_id = (args.get("media_dir_id") or "").strip()
        path = (args.get("path") or "").strip().strip("/")
        if not code:
            return "Automation not tested — `code` is required."
        if not media_dir_id:
            return "Automation not tested — `media_dir_id` is required."

        error = validate_starlark(code)
        if error is not None:
            return (
                "Automation not tested — the code did not parse. Fix this and call "
                f"test_automation_code again:\n{error}"
            )

        automation = repo.get_by_slug(self.session, self.slug)
        if automation is None:
            return f"Automation {self.slug!r} not found."

        try:
            media_item_ids = _media_item_ids_for_test_path(self.session, media_dir_id, path)
        except ValueError as exc:
            return f"Automation not tested — {exc}"

        event_type = next(
            (
                trigger.event_type
                for trigger in automation.triggers
                if trigger.trigger_type == TRIGGER_TYPE_EVENT and trigger.event_type
            ),
            None,
        )
        context = EventContext(event_type=event_type, job_id=None, media_item_ids=media_item_ids)
        functions, calls = build_recording_host_functions(self.session)
        result = run_automation_code(
            self.session, code, context, functions=functions, filename=f"{self.slug}.star"
        )
        actions = [
            {"summary": summarize_call(call, self.session), "name": call.name, "args": call.args}
            for call in calls
        ]
        action_lines = "\n".join(_format_action_for_model(action) for action in actions) or "- None"
        output_lines = "\n".join(result.output) or "(none)"
        model_text = (
            f"Tested {self.slug!r} against {len(media_item_ids)} media item(s) from "
            f"media_dir_id={media_dir_id!r}, path={path!r}.\n"
            f"Success: {result.success}\n"
            f"Host actions that would have occurred:\n{action_lines}\n"
            f"Print output:\n{output_lines}"
        )
        if result.error:
            model_text += f"\nError:\n{result.error}"

        return ToolResult(
            model_text=model_text,
            host_data={
                "slug": self.slug,
                "success": result.success,
                "media_dir_id": media_dir_id,
                "path": path,
                "media_item_ids": media_item_ids,
                "actions": actions,
                "output": result.output,
                "value": result.value,
                "error": result.error,
            },
        )


def _media_item_ids_for_test_path(session: Session, media_dir_id: str, path: str) -> list[int]:
    query: dict[str, Any] = {
        "source": "media_items",
        "media_dir_id": {"eq": media_dir_id},
    }
    if path:
        exact_query = {**query, "relative_path": {"eq": path}}
        rows = data_query_repository.resolve_query(session, exact_query)
        if rows:
            return [row["id"] for row in rows]
        query = {**query, "relative_path": {"prefix": f"{path.rstrip('/')}/"}}
    rows = data_query_repository.resolve_query(session, query)
    return [row["id"] for row in rows]


def _format_action_for_model(action: dict) -> str:
    args = json.dumps(action["args"], default=str)
    return f"- {action['summary']} ({action['name']} args={args})"
