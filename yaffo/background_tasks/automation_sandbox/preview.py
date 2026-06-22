"""Test/preview a custom automation from the UI: run its current code in the
sandbox against the user-selected photos, intercepting host-API calls so the
result lists the actions the script performed. No Job is recorded and mutating
actions are recorded but not performed, so a test changes nothing.

Code source mirrors the builder's published/working split: if a draft is being
worked on (working_code set) it's tested, otherwise the published code. The run
context is an event context over the given media_item_ids (a user-picked file/folder),
with the event_type taken from the automation's first event trigger if any.
"""
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.automation_host import (
    build_recording_host_functions,
    summarize_call,
)
from yaffo.background_tasks.automation_sandbox.executor import run_automation_code
from yaffo.background_tasks.events import EventContext
from yaffo.db.models import Automation, TRIGGER_TYPE_EVENT


@dataclass
class TestRunResult:
    """Browser-facing outcome of a test run: whether it succeeded, which code and
    context were used, the host-API actions intercepted, the captured print output,
    the trailing value, and any error."""
    success: bool
    code_source: str            # "working" | "published"
    context: dict               # {"type": "files", "media_item_ids": [...]}
    actions: list[dict] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    value: Any = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _first_event_type(automation: Automation) -> str | None:
    trigger = next(
        (t for t in automation.triggers if t.trigger_type == TRIGGER_TYPE_EVENT and t.event_type),
        None,
    )
    return trigger.event_type if trigger is not None else None


def preview_automation(
    session: Session, automation: Automation, media_item_ids: list[int], version: str | None = None
) -> TestRunResult:
    """Run the automation's code against `media_item_ids` (the user-selected file/folder),
    recording host calls. `version` ("working" | "published") selects which code to
    run — the version the user is currently viewing in the code panel — and falls back
    to whichever exists when the requested one is absent. With no `version`, prefers
    working over published. Assumes the caller checked there is code to run."""
    if version == "published" and automation.published_code:
        code, code_source = automation.published_code, "published"
    elif version == "working" and automation.working_code:
        code, code_source = automation.working_code, "working"
    else:
        code = automation.working_code or automation.published_code
        code_source = "working" if automation.working_code else "published"
    context = EventContext(
        event_type=_first_event_type(automation), job_id=None, media_item_ids=media_item_ids
    )

    functions, calls = build_recording_host_functions(session)
    result = run_automation_code(
        session, code, context, functions=functions, filename=f"{automation.slug}.star"
    )
    return TestRunResult(
        success=result.success,
        code_source=code_source,
        context={"type": "files", "media_item_ids": media_item_ids},
        actions=[
            {"summary": summarize_call(c, session), "name": c.name, "args": c.args}
            for c in calls
        ],
        output=result.output,
        value=result.value,
        error=result.error,
    )