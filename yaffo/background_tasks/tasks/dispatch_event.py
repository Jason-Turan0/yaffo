from yaffo.background_tasks.automation_dispatch import invoke_automation
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Automation, AutomationTrigger, TRIGGER_TYPE_EVENT
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


@huey.task()
def dispatch_event_task(event_type: str, payload: dict):
    """Fan a domain event out to every enabled automation subscribed to it.

    The push counterpart of the schedule dispatcher: instead of polling
    next_run_at, it matches event triggers by `event_type` and invokes each
    automation's handler with an EventContext. Enqueued by events.emit_event when
    something happens (e.g. a job completes)."""
    context = EventContext(
        event_type=event_type,
        job_id=payload.get('job_id'),
        photo_ids=payload.get('photo_ids', []),
        groups=payload.get('groups', []),
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
            try:
                if invoke_automation(automation, context):
                    logger.info(
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