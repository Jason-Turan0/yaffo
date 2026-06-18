from datetime import datetime

from yaffo.taskq import crontab

from yaffo.background_tasks.automation_dispatch import invoke_automation
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.schedule import compute_next_run
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import Automation, AutomationTrigger, TRIGGER_TYPE_SCHEDULE
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


@task_queue.periodic_task(crontab(minute='*'))
def dispatch_scheduled_tasks():
    """Fire every schedule trigger whose next_run_at has passed, then advance it.

    The one registered periodic task: the task-queue host enqueues it every minute,
    and it drives all schedule-triggered automations. Firing keys off
    `next_run_at` (not exact cron-matching) so a trigger still runs on the first
    tick after its slot even if this dispatcher is delayed by queue latency. A
    freshly enabled trigger (next_run_at NULL) is initialised to its next slot
    here rather than firing immediately. Event triggers are dispatched elsewhere
    (a later step), not here."""
    now = datetime.utcnow()
    session = SessionFactory()
    try:
        triggers = (
            session.query(AutomationTrigger)
            .join(Automation)
            .filter(
                AutomationTrigger.trigger_type == TRIGGER_TYPE_SCHEDULE,
                AutomationTrigger.enabled.is_(True),
                Automation.enabled.is_(True),
            )
            .all()
        )
        for trigger in triggers:
            try:
                if trigger.next_run_at is None:
                    trigger.next_run_at = compute_next_run(trigger.cron, now)
                    continue
                if trigger.next_run_at > now:
                    continue

                if invoke_automation(trigger.automation, None):
                    trigger.last_run_at = now
                    logger.info(f"Dispatched automation '{trigger.automation.slug}'")

                trigger.next_run_at = compute_next_run(trigger.cron, now)
            except Exception:
                logger.exception(
                    f"Failed to dispatch trigger {trigger.id} "
                    f"(cron={trigger.cron!r}); leaving it for the next tick"
                )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("dispatch_scheduled_tasks failed")
    finally:
        session.close()
        SessionFactory.remove()