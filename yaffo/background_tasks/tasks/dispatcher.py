from yaffo.taskq import crontab

from yaffo.utils.time import utcnow

from yaffo.background_tasks.automation_dispatch import invoke_automation
from yaffo.background_tasks.automation_runs import record_dispatch_failure
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.schedule import compute_next_run
from yaffo.background_tasks.schedule_scope import selected_paths, media_item_ids
from yaffo.background_tasks.events import EventContext
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
    here rather than firing immediately. Event triggers are dispatched elsewhere."""
    now = utcnow()
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
                    session.commit()
                    continue
                if trigger.next_run_at > now:
                    continue

                paths = selected_paths(session, trigger.config)
                context = EventContext(
                    event_type=None,
                    media_item_ids=media_item_ids(session, paths),
                    scope_paths=[str(path) for path in paths],
                )
                if not invoke_automation(trigger.automation, context):
                    raise ValueError("Automation has no runnable handler or published code")
                trigger.last_run_at = now
                logger.info(f"Dispatched automation '{trigger.automation.slug}'")

                trigger.next_run_at = compute_next_run(trigger.cron, now)
                session.commit()
            except Exception as exc:
                logger.exception(
                    f"Failed to dispatch trigger {trigger.id} (cron={trigger.cron!r})"
                )
                trigger_id = trigger.id
                session.rollback()
                try:
                    trigger = session.get(AutomationTrigger, trigger_id)
                    if trigger is not None:
                        record_dispatch_failure(session, trigger.automation, trigger.id,
                                                trigger.next_run_at, exc)
                        trigger.next_run_at = compute_next_run(trigger.cron, now)
                        session.commit()
                except Exception:
                    session.rollback()
                    logger.exception(f"Could not record dispatch failure for trigger {trigger_id}")
    except Exception:
        session.rollback()
        logger.exception("dispatch_scheduled_tasks failed")
    finally:
        session.close()
        SessionFactory.remove()
