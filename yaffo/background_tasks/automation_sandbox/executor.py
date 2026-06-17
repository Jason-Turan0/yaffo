from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.automation_host import build_host_functions
from yaffo.background_tasks.automation_sandbox.starlark_runner import StarlarkResult, run_starlark
from yaffo.db.models import Automation
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


def context_globals(context) -> dict:
    """The `ctx` global a script reads: what triggered this run. `context` is an
    EventContext for event-driven runs, or None for a schedule (then ctx fields
    are empty)."""
    return {
        "event_type": context.event_type if context else None,
        "job_id": context.job_id if context else None,
        "photo_ids": list(context.photo_ids) if context else [],
        "groups": [list(g) for g in context.groups] if context else [],
    }


def run_automation_code(
    session: Session,
    code: str,
    context=None,
    *,
    functions=None,
    filename: str = "automation.star",
) -> StarlarkResult:
    """Run a Starlark `code` string in the sandbox with the trigger `context` as
    `ctx` and the host API as `functions` (defaults to the live, executing host
    functions; pass recording ones to capture a test run's actions)."""
    return run_starlark(
        code,
        inputs={"ctx": context_globals(context)},
        functions=functions if functions is not None else build_host_functions(session),
        filename=filename,
    )


def run_automation(session: Session, automation: Automation, context=None) -> StarlarkResult:
    """Execute a custom automation's Starlark `code` in the sandbox.

    The script reads `ctx` (the trigger context) and calls the host API
    (data_query, ...) bound to `session`; it can't reach anything else. Returns
    the StarlarkResult -- a bad script is data (success=False), never an
    exception, so the caller can record/log it without crashing the worker."""
    if not automation.published_code:
        logger.warning(f"automation '{automation.slug}' has no published code to run")
        return StarlarkResult(success=False, error="automation has no published code")

    result = run_automation_code(
        session, automation.published_code, context, filename=f"{automation.slug}.star"
    )
    if result.success:
        logger.info(f"automation '{automation.slug}' ran; output={result.output}")
    else:
        logger.warning(f"automation '{automation.slug}' failed: {result.error}")
    return result
