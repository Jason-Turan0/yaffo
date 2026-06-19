from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.registry import HANDLERS
from yaffo.db.models import Automation
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


def invoke_automation(automation: Automation, context: EventContext | None) -> bool:
    """Fire an automation, routing by tier: a system automation goes through its
    registered HANDLERS entry; a custom (code-backed) one is enqueued on the
    Starlark executor. Returns whether it fired (False for a misconfigured row,
    so the schedule dispatcher won't stamp last_run_at).

    Shared by the schedule and event dispatchers. The executor-task import is
    in-function so this module never imports the tasks package at load time."""
    handler = HANDLERS.get(automation.handler)
    if handler is not None:
        handler(automation, context)
        return True

    if automation.published_code:
        from yaffo.background_tasks.tasks.run_automation import run_automation_code_task
        payload = None if context is None else {
            "event_type": context.event_type,
            "job_id": context.job_id,
            "photo_ids": context.photo_ids,
            "groups": context.groups,
            "origin_automation_ids": context.origin_automation_ids,
        }
        run_automation_code_task(automation.id, payload)
        return True

    logger.warning(
        f"automation '{automation.slug}' has neither a known handler "
        f"('{automation.handler}') nor published code; skipping"
    )
    return False
