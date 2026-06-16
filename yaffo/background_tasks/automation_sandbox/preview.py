"""Test/preview a custom automation from the UI: run its current code in the
sandbox against a synthetic trigger context, intercepting host-API calls so the
result lists the actions the script performed. No Job is recorded and (today) the
host surface is read-only, so a test changes nothing.

Code source mirrors the builder's published/working split: if a draft is being
worked on (working_code set) it's tested, otherwise the published code. Context
mirrors the automation's triggers: an event trigger runs against the first ten
photo ids in the DB; otherwise it's a schedule run with empty ctx.
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
from yaffo.db.models import Automation, Photo, TRIGGER_TYPE_EVENT

_PREVIEW_PHOTO_LIMIT = 10


@dataclass
class TestRunResult:
    """Browser-facing outcome of a test run: whether it succeeded, which code and
    context were used, the host-API actions intercepted, the captured print output,
    the trailing value, and any error."""
    success: bool
    code_source: str            # "working" | "published"
    context: dict               # {"type": "schedule"} | {"type": "event", ...}
    actions: list[dict] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    value: Any = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _preview_context(session: Session, automation: Automation) -> tuple[Any, dict]:
    """An event context (first ten photo ids) if the automation has an event
    trigger, else a schedule run (None). Returns (context, summary-for-the-UI)."""
    event_trigger = next(
        (t for t in automation.triggers if t.trigger_type == TRIGGER_TYPE_EVENT and t.event_type),
        None,
    )
    if event_trigger is None:
        return None, {"type": "schedule"}
    photo_ids = [
        pid for (pid,) in session.query(Photo.id).order_by(Photo.id).limit(_PREVIEW_PHOTO_LIMIT)
    ]
    return (
        EventContext(event_type=event_trigger.event_type, job_id=None, photo_ids=photo_ids),
        {"type": "event", "event_type": event_trigger.event_type, "photo_ids": photo_ids},
    )


def preview_automation(session: Session, automation: Automation) -> TestRunResult:
    """Run the automation's working (if any) or published code with a synthetic
    context, recording host calls. Assumes the caller checked there is code to run."""
    code = automation.working_code or automation.published_code
    code_source = "working" if automation.working_code else "published"
    context, summary = _preview_context(session, automation)

    functions, calls = build_recording_host_functions(session)
    result = run_automation_code(
        session, code, context, functions=functions, filename=f"{automation.slug}.star"
    )
    return TestRunResult(
        success=result.success,
        code_source=code_source,
        context=summary,
        actions=[{"summary": summarize_call(c), "name": c.name, "args": c.args} for c in calls],
        output=result.output,
        value=result.value,
        error=result.error,
    )