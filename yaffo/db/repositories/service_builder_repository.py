"""Custom adapters ("services") and their builder: the draft, its conversation, and
publishing.

A custom adapter is a `sync_adapters` row (not a preset, no built-in handler) with the
slug `custom.<name>` and the provider `custom:<slug>`, the name its connections are
stored under. The builder writes only the draft (`working_code` and `working`, the
statically checked version); syncs run the published version, which changes only
when the user publishes and approves the endpoint list (the grants).

The direction (source or target) comes from the code. It can change freely until the
first publish; after that, syncs depend on it and the draft has to keep it.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from yaffo.db.models import (
    AUTOMATION_STATUS_READY, CONVERSATION_TYPE_PERMISSION, Conversation, SYNC_ADAPTER_STATUS_READY, SYNC_ROLE_SOURCE,
    SYNC_ROLE_TARGET, SyncAdapter,
)
from yaffo.db.repositories.automation_repository import slugify
from yaffo.sync.adapter_static import CURRENT_CONTRACT, AdapterAnalysis, AdapterProblem, analyze_adapter
from yaffo.sync.preset_seed import grants_from_manifest
from yaffo.sync.providers import CUSTOM_PREFIX
from yaffo.sync.schemas import AdapterVersion

SLUG_PREFIX = "custom."


def _unique_slug(session: Session, name: str) -> str:
    base = SLUG_PREFIX + (slugify(name) or "service")
    slug, n = base, 2
    while session.query(SyncAdapter.id).filter(SyncAdapter.slug == slug).first() is not None:
        slug, n = f"{base}-{n}", n + 1
    return slug


def create_service(session: Session, name: str) -> SyncAdapter:
    """A new, empty custom adapter for the builder. Commits."""
    slug = _unique_slug(session, name)
    row = SyncAdapter(slug=slug, name=name.strip() or "New service", role=SYNC_ROLE_TARGET,
                      provider=CUSTOM_PREFIX + slug, is_preset=False, grants=[], doc_domains=[],
                      status=SYNC_ADAPTER_STATUS_READY, builder_status=AUTOMATION_STATUS_READY)
    session.add(row)
    session.commit()
    return row


def get_service(session: Session, slug: str) -> SyncAdapter | None:
    """A custom adapter by slug; None for presets, built-ins, and unknown slugs."""
    row = session.query(SyncAdapter).filter(SyncAdapter.slug == slug).first()
    if row is None or row.is_preset or row.handler:
        return None
    return row


def list_services(session: Session) -> list[SyncAdapter]:
    """Every custom adapter, drafts too, by name (the Services section of the sidebar)."""
    return (session.query(SyncAdapter)
            .filter(SyncAdapter.is_preset.is_(False), SyncAdapter.handler.is_(None))
            .order_by(SyncAdapter.name).all())


def messages(session: Session, adapter_id: int) -> list[Conversation]:
    return (session.query(Conversation).filter(Conversation.sync_adapter_id == adapter_id)
            .order_by(Conversation.id).all())


def add_message(session: Session, adapter_id: int, entry_type: str, content: str) -> None:
    session.add(Conversation(sync_adapter_id=adapter_id, type=entry_type, content=content))
    session.commit()


def set_builder_status(session: Session, adapter_id: int, status: str) -> None:
    session.query(SyncAdapter).filter(SyncAdapter.id == adapter_id).update({"builder_status": status})
    session.commit()


def builder_status(session: Session, adapter_id: int) -> str | None:
    return session.query(SyncAdapter.builder_status).filter(SyncAdapter.id == adapter_id).scalar()


def save_draft(session: Session, adapter_id: int, code: str) -> AdapterAnalysis:
    """Check and store the builder's draft. The code is kept even when it fails the
    check (so the model can fix it in place); only a passing draft gets a `working`
    version, which Test and Publish use. Commits."""
    row = session.get(SyncAdapter, adapter_id)
    analysis = analyze_adapter(code)
    role = analysis.metadata.get("role")
    if analysis.ok and row.published and role != row.role:
        analysis.problems.append(AdapterProblem(
            "role_changed", f"this service is published as a {row.role}; syncs depend on that, so the draft "
            f"must stay a {row.role}"))
    if analysis.ok and analysis.metadata.get("provider") != "custom":
        analysis.problems.append(AdapterProblem(
            "provider_not_custom", 'a service built here declares "provider": "custom" and its own connection block'))
    row.working_code = code
    row.working = AdapterVersion(code_hash=analysis.code_hash, contract=CURRENT_CONTRACT, metadata=analysis.metadata,
                                 manifest=analysis.manifest).to_dict() if analysis.ok else None
    if analysis.ok and not row.published and role in (SYNC_ROLE_SOURCE, SYNC_ROLE_TARGET):
        row.role = role
    session.commit()
    return analysis


def has_unpublished_draft(row: SyncAdapter) -> bool:
    return bool(row.working) and (not row.published or row.working.get("code_hash") != row.published.get("code_hash"))


def publish(session: Session, adapter_id: int) -> bool:
    """Make the checked draft live, granting the endpoints its check found (the user
    approved that list in the publish dialog). Returns False with no passing draft.
    Commits."""
    row = session.get(SyncAdapter, adapter_id)
    if row is None or not row.working or not row.working_code:
        return False
    version = AdapterVersion.from_dict(row.working)
    if row.published and version.metadata.get("role") != row.role:
        return False
    row.published_code = row.working_code
    row.published = row.working
    row.role = version.metadata.get("role", row.role)
    row.grants = grants_from_manifest(version.manifest, version.code_hash)
    row.status = SYNC_ADAPTER_STATUS_READY
    session.commit()
    return True


# ---- the builder's permissions -----------------------------------------------------------
#
# The builder asks before reading documentation from a new domain (the batch prompt)
# and before reading the user's account live while building. A request is a
# `permission` conversation entry whose JSON content is its state, shown as a card:
#   {"kind": "docs", "status": ..., "domains": {domain: [urls]}, "allowed": [...], "denied": [...]}
#   {"kind": "live_reads", "status": ..., "connection": key, "account": label}
# status: pending, then allowed / denied / partial (some domains allowed), or
# superseded (a newer request of the same kind replaced a pending one).

PERMISSION_DOCS = "docs"
PERMISSION_LIVE_READS = "live_reads"
PENDING, ALLOWED, DENIED, PARTIAL, SUPERSEDED = "pending", "allowed", "denied", "partial", "superseded"


def _payload(message: Conversation) -> dict:
    try:
        data = json.loads(message.content)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def permissions(session: Session, adapter_id: int) -> list[tuple[Conversation, dict]]:
    return [(m, _payload(m)) for m in messages(session, adapter_id) if m.type == CONVERSATION_TYPE_PERMISSION]


def pending_permission(session: Session, adapter_id: int, kind: str) -> tuple[Conversation, dict] | None:
    return next(((m, p) for m, p in reversed(permissions(session, adapter_id))
                 if p.get("kind") == kind and p.get("status") == PENDING), None)


def denied_domains(session: Session, adapter_id: int) -> set[str]:
    """Domains the user said no to in this service's conversation, unless allowed since."""
    row = session.get(SyncAdapter, adapter_id)
    denied: set[str] = set()
    for _, payload in permissions(session, adapter_id):
        if payload.get("kind") == PERMISSION_DOCS:
            denied |= set(payload.get("denied") or [])
    return denied - set(row.doc_domains or [])


def ask_for_docs(session: Session, adapter_id: int, domains: dict[str, list[str]]) -> None:
    """Ask once for every domain a batch needs; a still-pending request is folded in."""
    previous = pending_permission(session, adapter_id, PERMISSION_DOCS)
    merged: dict[str, list[str]] = {}
    if previous is not None:
        message, payload = previous
        merged = {d: list(u) for d, u in (payload.get("domains") or {}).items()}
        message.content = json.dumps({**payload, "status": SUPERSEDED})
    for domain, urls in domains.items():
        merged[domain] = list(dict.fromkeys(merged.get(domain, []) + urls))
    session.add(Conversation(sync_adapter_id=adapter_id, type=CONVERSATION_TYPE_PERMISSION,
                             content=json.dumps({"kind": PERMISSION_DOCS, "status": PENDING, "domains": merged,
                                                 "allowed": [], "denied": []})))
    session.commit()


def ask_for_live_reads(session: Session, adapter_id: int, connection_key: str, account: str) -> bool:
    """Ask to read the test account live. False when that request is already waiting."""
    pending = pending_permission(session, adapter_id, PERMISSION_LIVE_READS)
    if pending is not None and pending[1].get("connection") == connection_key:
        return False
    if pending is not None:
        pending[0].content = json.dumps({**pending[1], "status": SUPERSEDED})
    session.add(Conversation(sync_adapter_id=adapter_id, type=CONVERSATION_TYPE_PERMISSION,
                             content=json.dumps({"kind": PERMISSION_LIVE_READS, "status": PENDING,
                                                 "connection": connection_key, "account": account})))
    session.commit()
    return True


def live_reads_allowed(row: SyncAdapter) -> bool:
    return bool(row.test_connection_key) and row.live_reads_key == row.test_connection_key


def resolve_permission(session: Session, adapter_id: int, message_id: int, allow: bool,
                       domains: list[str] | None = None) -> str | None:
    """Record the user's answer to a pending request and apply it. Returns the
    sentence that tells the builder what was decided, or None when there's no such
    pending request. `domains` narrows a docs approval to some of its domains."""
    row = session.get(SyncAdapter, adapter_id)
    message = session.get(Conversation, message_id)
    if row is None or message is None or message.sync_adapter_id != adapter_id \
            or message.type != CONVERSATION_TYPE_PERMISSION:
        return None
    payload = _payload(message)
    if payload.get("status") != PENDING:
        return None
    if payload.get("kind") == PERMISSION_LIVE_READS:
        if allow and payload.get("connection") == row.test_connection_key:
            row.live_reads_key = row.test_connection_key
        message.content = json.dumps({**payload, "status": ALLOWED if allow else DENIED})
        session.commit()
        return ("I allowed live read-only tests with my account. Continue." if allow else
                "I didn't allow live reads of my account. Continue with recorded tests; I'll press Test myself.")
    asked = list((payload.get("domains") or {}).keys())
    allowed = [d for d in asked if allow and (domains is None or d in domains)]
    refused = [d for d in asked if d not in allowed]
    row.doc_domains = sorted(set(row.doc_domains or []) | set(allowed))
    status = ALLOWED if allowed and not refused else PARTIAL if allowed else DENIED
    message.content = json.dumps({**payload, "status": status, "allowed": allowed, "denied": refused})
    session.commit()
    parts = []
    if allowed:
        parts.append("I allowed reading pages from " + ", ".join(allowed) + ".")
    if refused:
        parts.append("I didn't allow " + ", ".join(refused) + ".")
    return " ".join(parts) + " Continue."
