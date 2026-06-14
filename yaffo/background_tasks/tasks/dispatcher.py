from datetime import datetime

from huey import crontab

from yaffo.background_tasks.actions import ACTIONS
from yaffo.background_tasks.config import huey
from yaffo.background_tasks.schedule import compute_next_run
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import TaskSchedule
from yaffo.logging_config import get_logger

logger = get_logger(__name__, 'background_tasks')


@huey.periodic_task(crontab(minute='*'))
def dispatch_scheduled_tasks():
    """Fire every TaskSchedule whose next_run_at has passed, then advance it.

    The one registered periodic task: huey's scheduler enqueues it every minute,
    and it drives all runtime-created schedules. Firing keys off `next_run_at`
    (not exact cron-matching) so a row still runs on the first tick after its
    slot even if this dispatcher is delayed by queue latency. A freshly
    enabled/created row (next_run_at NULL) is initialised to its next slot here
    rather than firing immediately."""
    now = datetime.utcnow()
    session = SessionFactory()
    try:
        schedules = session.query(TaskSchedule).filter(TaskSchedule.enabled.is_(True)).all()
        for schedule in schedules:
            try:
                if schedule.next_run_at is None:
                    schedule.next_run_at = compute_next_run(schedule.cron, now)
                    continue
                if schedule.next_run_at > now:
                    continue

                action = ACTIONS.get(schedule.action)
                if action is None:
                    logger.warning(
                        f"schedule '{schedule.key}' has unknown action "
                        f"'{schedule.action}'; skipping"
                    )
                else:
                    action(schedule.args or {})
                    schedule.last_run_at = now
                    logger.info(f"Dispatched scheduled task '{schedule.key}'")

                schedule.next_run_at = compute_next_run(schedule.cron, now)
            except Exception:
                logger.exception(
                    f"Failed to dispatch schedule '{schedule.key}' "
                    f"(cron={schedule.cron!r}); leaving it for the next tick"
                )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("dispatch_scheduled_tasks failed")
    finally:
        session.close()
        SessionFactory.remove()