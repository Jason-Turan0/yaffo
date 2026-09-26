"""Persistence for the in-app assistant: conversations and their transcript events."""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from yaffo.db.models import (
    ASSISTANT_EVENT_USER,
    ASSISTANT_MODEL_EVENTS,
    ASSISTANT_STATUS_IDLE,
    ASSISTANT_STATUS_RUNNING,
    AssistantConversation,
    AssistantEvent,
)
from yaffo.utils.time import utcnow

# Titles come from the first message; long ones are cut at a word boundary.
TITLE_MAX_LENGTH = 60


def title_from_message(message: str) -> str:
    flat = " ".join(message.split())
    if len(flat) <= TITLE_MAX_LENGTH:
        return flat
    cut = flat[:TITLE_MAX_LENGTH].rsplit(" ", 1)[0] or flat[:TITLE_MAX_LENGTH]
    return cut + "…"


def create_conversation(session: Session, title: str) -> AssistantConversation:
    conversation = AssistantConversation(title=title, status=ASSISTANT_STATUS_IDLE)
    session.add(conversation)
    session.commit()
    return conversation


def get_conversation(session: Session, conversation_id: int) -> Optional[AssistantConversation]:
    return session.get(AssistantConversation, conversation_id)


def list_conversations(session: Session) -> list[AssistantConversation]:
    """Most recently active first."""
    return (
        session.query(AssistantConversation)
        .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id.desc())
        .all()
    )


def count_conversations(session: Session) -> int:
    return session.query(func.count(AssistantConversation.id)).scalar() or 0


def rename_conversation(session: Session, conversation_id: int, title: str) -> bool:
    updated = (
        session.query(AssistantConversation)
        .filter_by(id=conversation_id)
        .update({"title": title})
    )
    session.commit()
    return bool(updated)


def delete_conversation(session: Session, conversation_id: int) -> bool:
    """Delete a conversation and its events. Explicit event delete as well as the
    FK cascade, because SQLite only enforces ON DELETE CASCADE when foreign keys
    are switched on for the connection."""
    session.query(AssistantEvent).filter_by(conversation_id=conversation_id).delete()
    deleted = session.query(AssistantConversation).filter_by(id=conversation_id).delete()
    session.commit()
    return bool(deleted)


def delete_all_conversations(session: Session) -> int:
    session.query(AssistantEvent).delete()
    deleted = session.query(AssistantConversation).delete()
    session.commit()
    return deleted


def add_event(
    session: Session,
    conversation_id: int,
    kind: str,
    content: str = "",
    payload: Optional[dict[str, Any]] = None,
) -> AssistantEvent:
    """Append one transcript entry at the next sequence number and mark the
    conversation as recently active."""
    last = (
        session.query(func.max(AssistantEvent.seq))
        .filter(AssistantEvent.conversation_id == conversation_id)
        .scalar()
    )
    event = AssistantEvent(
        conversation_id=conversation_id,
        seq=0 if last is None else last + 1,
        kind=kind,
        content=content,
        payload=json.dumps(payload) if payload is not None else None,
    )
    session.add(event)
    session.query(AssistantConversation).filter_by(id=conversation_id).update({"updated_at": utcnow()})
    session.commit()
    return event


def list_events(
    session: Session, conversation_id: int, after_seq: Optional[int] = None,
) -> list[AssistantEvent]:
    query = session.query(AssistantEvent).filter(AssistantEvent.conversation_id == conversation_id)
    if after_seq is not None:
        query = query.filter(AssistantEvent.seq > after_seq)
    return query.order_by(AssistantEvent.seq).all()


def model_turns(session: Session, conversation_id: int) -> list[tuple[str, str]]:
    """The (kind, text) turns replayed to the model: user and assistant only."""
    rows = (
        session.query(AssistantEvent.kind, AssistantEvent.content)
        .filter(
            AssistantEvent.conversation_id == conversation_id,
            AssistantEvent.kind.in_(ASSISTANT_MODEL_EVENTS),
        )
        .order_by(AssistantEvent.seq)
        .all()
    )
    return [(kind, content) for kind, content in rows]


def latest_user_context(session: Session, conversation_id: int) -> Optional[dict[str, Any]]:
    """The context attached to the conversation's latest user message ("Help me
    with this"), if any."""
    row = (
        session.query(AssistantEvent.payload)
        .filter(AssistantEvent.conversation_id == conversation_id, AssistantEvent.kind == ASSISTANT_EVENT_USER)
        .order_by(AssistantEvent.seq.desc())
        .first()
    )
    if row is None or not row[0]:
        return None
    context = json.loads(row[0]).get("context")
    return context if isinstance(context, dict) else None


def start_run(session: Session, conversation_id: int, model_id: str) -> bool:
    """Mark the conversation RUNNING unless a run is already active. Conditional in
    one UPDATE, so two quick sends can't both start a run."""
    updated = (
        session.query(AssistantConversation)
        .filter(
            AssistantConversation.id == conversation_id,
            AssistantConversation.status != ASSISTANT_STATUS_RUNNING,
        )
        .update({
            "status": ASSISTANT_STATUS_RUNNING,
            "model_id": model_id,
            "run_started_at": utcnow(),
            "updated_at": utcnow(),
        })
    )
    session.commit()
    return bool(updated)


def set_status(session: Session, conversation_id: int, status: str) -> None:
    session.query(AssistantConversation).filter_by(id=conversation_id).update({"status": status})
    session.commit()
