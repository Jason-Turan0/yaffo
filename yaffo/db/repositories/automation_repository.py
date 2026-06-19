"""Persistence helpers for the automation builder: the chat transcript, the
generation status, the working draft, and publishing.

Mirrors the theme builder, but an automation is a real `automations` row (not a
JSON blob): the chat lives in `conversations` rows keyed by `automation_id`, the
draft the agent writes is `working_code`, and publishing copies it into
`published_code` (the only code the dispatchers run). Callers pass the session
(a request's db.session or a worker's SessionFactory session)."""
from sqlalchemy.orm import Session

from yaffo.db.models import (
    Automation,
    AutomationTrigger,
    Conversation,
    Job,
    AUTOMATION_STATUS_ACCEPTED,
    TRIGGER_TYPE_EVENT,
    TRIGGER_TYPE_SCHEDULE,
)


def get_by_slug(session: Session, slug: str) -> Automation | None:
    return session.query(Automation).filter_by(slug=slug).first()


def add_schedule_trigger(session: Session, slug: str, cron: str) -> AutomationTrigger | None:
    """Add an enabled schedule trigger (caller validates `cron` first). next_run_at
    is left NULL so the dispatcher initialises it from the cron on its next tick.
    Returns None when the automation is gone."""
    automation = get_by_slug(session, slug)
    if automation is None:
        return None
    trigger = AutomationTrigger(
        automation_id=automation.id, trigger_type=TRIGGER_TYPE_SCHEDULE,
        enabled=True, cron=cron,
    )
    session.add(trigger)
    session.commit()
    return trigger


def add_event_trigger(session: Session, slug: str, event_type: str) -> AutomationTrigger | None:
    """Add an enabled event trigger (caller validates `event_type` against EVENTS).
    Returns None when the automation is gone."""
    automation = get_by_slug(session, slug)
    if automation is None:
        return None
    trigger = AutomationTrigger(
        automation_id=automation.id, trigger_type=TRIGGER_TYPE_EVENT,
        enabled=True, event_type=event_type,
    )
    session.add(trigger)
    session.commit()
    return trigger


def remove_schedule_trigger(session: Session, slug: str, cron: str) -> int:
    """Delete the automation's schedule trigger(s) matching `cron`. Returns the
    number removed (0 if the automation is gone or nothing matched)."""
    automation = get_by_slug(session, slug)
    if automation is None:
        return 0
    matches = [
        t for t in automation.triggers
        if t.trigger_type == TRIGGER_TYPE_SCHEDULE and t.cron == cron
    ]
    for trigger in matches:
        session.delete(trigger)
    session.commit()
    return len(matches)


def remove_event_trigger(session: Session, slug: str, event_type: str) -> int:
    """Delete the automation's event trigger(s) for `event_type`. Returns the number
    removed (0 if the automation is gone or nothing matched)."""
    automation = get_by_slug(session, slug)
    if automation is None:
        return 0
    matches = [
        t for t in automation.triggers
        if t.trigger_type == TRIGGER_TYPE_EVENT and t.event_type == event_type
    ]
    for trigger in matches:
        session.delete(trigger)
    session.commit()
    return len(matches)


def get_recent_jobs(session: Session, automation_id: int, limit: int = 10) -> list[Job]:
    """The automation's most recent runs (Jobs tagged with its id), newest first.
    Reuses the Job table as the run history (jobs.automation_id)."""
    return (
        session.query(Job)
        .filter(Job.automation_id == automation_id)
        .order_by(Job.created_at.desc())
        .limit(limit)
        .all()
    )


def get_status(session: Session, slug: str) -> str | None:
    row = session.query(Automation.status).filter_by(slug=slug).first()
    return row[0] if row is not None else None


def add_message(session: Session, automation_id: int, entry_type: str, content: str) -> None:
    """Append one conversation entry (user / assistant / status / error)."""
    session.add(Conversation(automation_id=automation_id, type=entry_type, content=content))
    session.commit()


def set_status(session: Session, slug: str, status: str) -> None:
    session.query(Automation).filter_by(slug=slug).update({"status": status})
    session.commit()


def write_working_code(session: Session, slug: str, code: str) -> bool:
    """Save the agent's draft into working_code. Returns False if the automation is
    gone (e.g. deleted mid-run)."""
    updated = session.query(Automation).filter_by(slug=slug).update({"working_code": code})
    session.commit()
    return bool(updated)


def publish(session: Session, slug: str) -> bool:
    """Promote the working draft to live: copy working_code -> published_code and
    mark ACCEPTED. Returns False when there's nothing to publish (no draft)."""
    automation = get_by_slug(session, slug)
    if automation is None or not automation.working_code:
        return False
    automation.published_code = automation.working_code
    automation.status = AUTOMATION_STATUS_ACCEPTED
    session.commit()
    return True


def discard_draft(session: Session, slug: str) -> None:
    """Drop the working draft, keeping the published code as-is."""
    automation = get_by_slug(session, slug)
    if automation is None:
        return
    automation.working_code = None
    automation.status = AUTOMATION_STATUS_ACCEPTED
    session.commit()
