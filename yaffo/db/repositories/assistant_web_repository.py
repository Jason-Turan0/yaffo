"""Conversation-scoped domain approvals for the assistant's web reads.

Requests and decisions live in the existing transcript. Deleting a conversation
also removes its approvals; other conversations start with no authorized domains.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from yaffo.db.models import ASSISTANT_EVENT_WEB_PERMISSION, AssistantEvent
from yaffo.db.repositories import assistant_repository as conversations
from yaffo.site_agents.assistant.schemas import WebPermission


def permissions(session: Session, conversation_id: int) -> list[WebPermission]:
    events = (session.query(AssistantEvent)
              .filter_by(conversation_id=conversation_id, kind=ASSISTANT_EVENT_WEB_PERMISSION)
              .order_by(AssistantEvent.seq).all())
    return [WebPermission.from_event(event) for event in events]


def domain_access(session: Session, conversation_id: int) -> tuple[set[str], set[str]]:
    allowed: set[str] = set()
    denied: set[str] = set()
    for permission in permissions(session, conversation_id):
        if permission.status in {"allowed", "denied", "partial"}:
            allowed.update(permission.allowed)
            allowed.difference_update(permission.denied)
            denied.update(permission.denied)
            denied.difference_update(permission.allowed)
    return allowed, denied


def ask(session: Session, conversation_id: int, domains: dict[str, list[str]]) -> WebPermission:
    pending = next((p for p in reversed(permissions(session, conversation_id)) if p.status == "pending"), None)
    merged = {domain: list(urls) for domain, urls in pending.domains.items()} if pending else {}
    for domain, urls in domains.items():
        merged[domain] = list(dict.fromkeys(merged.get(domain, []) + urls))
    if pending:
        event = session.get(AssistantEvent, pending.id)
        permission = WebPermission(domains=merged, id=event.id)
        event.payload = json.dumps(permission.to_dict())
        session.commit()
        return permission
    event = conversations.add_event(session, conversation_id, ASSISTANT_EVENT_WEB_PERMISSION,
                                    payload=WebPermission(domains=merged).to_dict())
    return WebPermission.from_event(event)


def resolve(session: Session, conversation_id: int, permission_id: int, allow: bool,
            domains: list[str] | None = None) -> WebPermission | None:
    """Stage a card decision, restricted to its domains. The caller commits it
    with the continuation, so a racing message cannot consume the approval."""
    event = session.get(AssistantEvent, permission_id)
    if event is None or event.conversation_id != conversation_id or event.kind != ASSISTANT_EVENT_WEB_PERMISSION:
        return None
    permission = WebPermission.from_event(event)
    if permission.status != "pending":
        return None
    asked = set(permission.domains)
    if domains is not None and not set(domains) <= asked:
        raise ValueError("Only domains in the permission request can be approved")
    selected = asked if domains is None else set(domains)
    allowed = sorted(asked & selected) if allow else []
    denied = sorted(asked - set(allowed))
    status = "partial" if allowed and denied else "allowed" if allowed else "denied"
    decided = WebPermission(domains=permission.domains, status=status, allowed=allowed, denied=denied, id=event.id)
    updated = (session.query(AssistantEvent)
               .filter_by(id=event.id, payload=event.payload)
               .update({"payload": json.dumps(decided.to_dict())}, synchronize_session=False))
    if not updated:
        return None
    session.expire(event)
    return decided
