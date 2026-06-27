from yaffo.background_tasks.automation_dispatch import invoke_automation
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Automation, AutomationTrigger, TRIGGER_TYPE_EVENT
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


@task_queue.task()
def dispatch_event_task(event_type: str, payload: dict):
    """Fan a domain event out to every enabled automation subscribed to it.

    The push counterpart of the schedule dispatcher: instead of polling
    next_run_at, it matches event triggers by `event_type` and invokes each
    automation's handler with an EventContext. Enqueued by events.emit_event when
    something happens (e.g. a job completes)."""
    context = EventContext(
        event_type=event_type,
        job_id=payload.get('job_id'),
        media_item_ids=payload.get('media_item_ids', []),
        groups=payload.get('groups', []),
        origin_automation_ids=payload.get('origin_automation_ids', []),
    )
    session = SessionFactory()
    try:
        triggers = (
            session.query(AutomationTrigger)
            .join(Automation)
            .filter(
                AutomationTrigger.trigger_type == TRIGGER_TYPE_EVENT,
                AutomationTrigger.event_type == event_type,
                AutomationTrigger.enabled.is_(True),
                Automation.enabled.is_(True),
            )
            .all()
        )
        for trigger in triggers:
            automation = trigger.automation
            # Loop guard: this automation already fired earlier in the chain of events
            # that led here, so firing it again would be (the start of) a cycle. Skip
            # it — other subscribers not in the chain still run. See docs/development/automations.md.
            if automation.id in context.origin_automation_ids:
                logger.warning(
                    f"loop guard: skipping '{automation.slug}' for event {event_type} "
                    f"(already in causal chain {context.origin_automation_ids})"
                )
                continue
            try:
                if invoke_automation(automation, context):
                    logger.debug(
                        f"Dispatched automation '{automation.slug}' for event {event_type}"
                    )
            except Exception:
                logger.exception(
                    f"Dispatching automation '{automation.slug}' failed on event "
                    f"{event_type}"
                )
    finally:
        session.close()
        SessionFactory.remove()