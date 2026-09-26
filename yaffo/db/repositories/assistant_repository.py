"""Persistence for the in-app assistant: conversations, their transcript events,
and the change plans their scripts recorded."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from yaffo.db.models import (
    ASSISTANT_EVENT_USER,
    ASSISTANT_MODEL_EVENTS,
    ASSISTANT_STATUS_IDLE,
    ASSISTANT_STATUS_RUNNING,
    PLAN_STATUS_PENDING,
    AssistantChangePlan,
    AssistantConversation,
    AssistantEvent,
)
from yaffo.utils.time import utcnow
from yaffo.site_agents.assistant.call_logs import delete_logs, delete_all_logs

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
    session.query(AssistantChangePlan).filter_by(conversation_id=conversation_id).delete()
    session.query(AssistantEvent).filter_by(conversation_id=conversation_id).delete()
    deleted = session.query(AssistantConversation).filter_by(id=conversation_id).delete()
    session.commit()
    delete_logs(conversation_id)
    return bool(deleted)


def delete_all_conversations(session: Session) -> int:
    session.query(AssistantChangePlan).delete()
    session.query(AssistantEvent).delete()
    deleted = session.query(AssistantConversation).delete()
    session.commit()
    delete_all_logs()
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


# ---- change plans --------------------------------------------------------------

def create_plan(session: Session, conversation_id: int, *, script: str, steps: list[dict],
                risk: str, ttl: timedelta) -> AssistantChangePlan:
    now = utcnow()
    plan = AssistantChangePlan(
        conversation_id=conversation_id, script=script, steps_json=json.dumps(steps),
        risk=risk, created_at=now, expires_at=now + ttl,
    )
    session.add(plan)
    session.commit()
    return plan


def get_plan(session: Session, plan_id: int) -> Optional[AssistantChangePlan]:
    return session.get(AssistantChangePlan, plan_id)


def list_plans(session: Session, conversation_id: int) -> list[AssistantChangePlan]:
    return (
        session.query(AssistantChangePlan)
        .filter(AssistantChangePlan.conversation_id == conversation_id)
        .order_by(AssistantChangePlan.id)
        .all()
    )


def pending_plan_counts(session: Session) -> dict[int, int]:
    """Conversation id → how many of its plans still wait for a decision (the
    conversation list flags them). Expired ones are counted until someone acts."""
    rows = (
        session.query(AssistantChangePlan.conversation_id, func.count(AssistantChangePlan.id))
        .filter(AssistantChangePlan.status == PLAN_STATUS_PENDING,
                AssistantChangePlan.expires_at > utcnow())
        .group_by(AssistantChangePlan.conversation_id)
        .all()
    )
    return {conversation_id: count for conversation_id, count in rows}


def claim_plan(session: Session, plan_id: int, from_statuses: tuple[str, ...], status: str, **values: Any) -> bool:
    """Move a plan to `status` only if it is still in one of `from_statuses`, in one
    UPDATE, so two clicks (or two tabs) can't both approve or undo it."""
    updated = (
        session.query(AssistantChangePlan)
        .filter(AssistantChangePlan.id == plan_id, AssistantChangePlan.status.in_(from_statuses))
        .update({"status": status, **values}, synchronize_session=False)
    )
    session.commit()
    return bool(updated)


def save_plan(session: Session, plan: AssistantChangePlan, *, steps: list[dict], **values: Any) -> None:
    plan.steps_json = json.dumps(steps)
    for name, value in values.items():
        setattr(plan, name, value)
    session.commit()
