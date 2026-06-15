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
    Conversation,
    AUTOMATION_STATUS_ACCEPTED,
)


def get_by_slug(session: Session, slug: str) -> Automation | None:
    return session.query(Automation).filter_by(slug=slug).first()


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
