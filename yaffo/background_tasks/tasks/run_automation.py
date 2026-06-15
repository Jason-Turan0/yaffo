from yaffo.background_tasks.automation_sandbox.executor import run_automation
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.events import EventContext
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Automation
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


@huey.task()
def run_automation_code_task(automation_id: int, context_payload: dict | None = None):
    """Run a custom automation's Starlark code in a worker.

    Enqueued by the dispatchers for code-backed automations (handler is None).
    `context_payload` is the EventContext fields for an event-triggered run, or
    None for a schedule. The sandbox returns failures as data, so a bad script is
    logged, not raised."""
    session = SessionFactory()
    try:
        automation = session.query(Automation).filter_by(id=automation_id).first()
        if automation is None:
            logger.warning(f"run_automation_code_task: automation {automation_id} not found")
            return
        context = EventContext(**context_payload) if context_payload else None
        run_automation(session, automation, context)
    finally:
        session.close()
        SessionFactory.remove()
