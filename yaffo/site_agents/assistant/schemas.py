"""Wire contracts for the in-app assistant.

Browser-facing shapes only. What the model reads from a tool is plain text built
in tools.py; these are what the chat UI parses (conversation lists, the polled
transcript, and a tool event's activity line and sources).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from yaffo.db.models import PLAN_STATUS_PENDING, AssistantChangePlan, AssistantConversation, AssistantEvent
from yaffo.site_agents.assistant import plans
from yaffo.site_agents.assistant.run_queue import RunQueueStatus


@dataclass(frozen=True)
class DocSource:
    """A doc section the assistant used, linked on the published docs site."""
    title: str
    heading: str
    url: str
    scope: str


@dataclass(frozen=True)
class AppLink:
    """A link into the app the assistant made (link_to_photos /
    link_to_page), shown under its answer. `url` is app-relative."""
    title: str
    url: str


@dataclass(frozen=True)
class OpenLink:
    """A button the assistant made (link_to_file) that opens a file or folder on
    the user's computer. `target` holds only ids (see file_targets.FileTarget);
    the server finds the path again when it's clicked."""
    title: str
    show: str
    target: dict


@dataclass(frozen=True)
class ToolActivity:
    """A tool event's payload. The browser formats the activity line from `tool`
    plus the fields that tool sets (query/count for search_docs, title for
    read_doc, args/count for a diagnostic, purpose for run_script), so the text is
    localized on the client. `detail` is exactly the (redacted) text the model
    received, shown when the line is expanded; `script` is run_script's source."""
    tool: str
    sources: list[DocSource] = field(default_factory=list)
    query: str = ""
    count: int = 0
    title: str = ""
    error: bool = False
    args: dict = field(default_factory=dict)
    detail: str = ""
    purpose: str = ""
    script: str = ""
    links: list[AppLink] = field(default_factory=list)
    opens: list[OpenLink] = field(default_factory=list)
    # A run_script that recorded changes: the plan the card shows.
    plan_id: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PlanStepView:
    """One step on a plan card. The browser words it from `name` + `count` +
    `facts` in the user's language; `summary` is the server's English fallback.
    `state` is pending, done, failed, not_run or undone."""
    seq: int
    name: str
    summary: str
    count: int
    facts: dict
    risk: str
    reversible: bool
    state: str
    error: Optional[str]
    # Background work: the Job id once the step ran, and where it shows in the app.
    starts_job: bool = False
    job_id: Optional[str] = None
    job_page: Optional[str] = None


@dataclass(frozen=True)
class PlanView:
    """A change plan's card, from the plan row (never from model text). `status`
    reads EXPIRED once a pending plan is past `expires_at`. `confirm` is how
    Approve must be confirmed: "type" the count (high risk), "check" a box (more
    items than the Settings threshold), or None."""
    id: int
    status: str
    risk: str
    count: int
    reversible: bool
    # Nothing in it changes the library (a scan): no undo is needed.
    read_only: bool
    confirm: Optional[str]
    steps: list[PlanStepView]
    error: Optional[str]
    created_at: Optional[str]
    expires_at: Optional[str]
    finished_at: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PlanDecided:
    """The approve / decline / undo response body."""
    plan: PlanView

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationSummary:
    id: int
    title: str
    status: str
    updated_at: Optional[str]
    # Plans still waiting for Approve or Decline, flagged in the conversation list.
    pending_plans: int = 0

    @classmethod
    def from_model(cls, conversation: AssistantConversation, pending_plans: int = 0) -> "ConversationSummary":
        return cls(
            id=conversation.id,
            title=conversation.title,
            status=conversation.status,
            updated_at=_iso(conversation.updated_at),
            pending_plans=pending_plans,
        )


@dataclass(frozen=True)
class ConversationList:
    conversations: list[ConversationSummary]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationStarted:
    """A 202 body: the conversation a new run belongs to."""
    conversation: ConversationSummary

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationsDeleted:
    deleted: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AssistantNotice:
    """The one-line notice at the top of an empty conversation: the provider and
    model, and whether the assistant may check this computer (any diagnostics
    group on)."""
    provider: str
    model_label: str
    checks_enabled: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AssistantError:
    """The standard error envelope plus a machine code for the client."""
    error: str
    code: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TranscriptEvent:
    seq: int
    type: str
    content: str
    payload: Optional[dict]

    @classmethod
    def from_model(cls, event: AssistantEvent, plans: Optional[dict[int, "PlanView"]] = None) -> "TranscriptEvent":
        payload = json.loads(event.payload) if event.payload else None
        # A tool event that recorded a plan carries the plan's current state, so
        # the card follows approval, replay and undo without a transcript rewrite.
        if isinstance(payload, dict) and plans and payload.get("plan_id") in plans:
            payload = {**payload, "plan": plans[payload["plan_id"]].to_dict()}
        return cls(seq=event.seq, type=event.kind, content=event.content, payload=payload)


@dataclass(frozen=True)
class ConversationStatus:
    """The chat dialog's poll body (`status`, `started_at`, `messages`), plus the
    conversation it belongs to, and `queue` while the run waits for a worker."""
    conversation: ConversationSummary
    status: str
    started_at: Optional[str]
    messages: list[TranscriptEvent]
    queue: Optional[RunQueueStatus] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(value: Optional[datetime]) -> Optional[str]:
    # Stored naive UTC; mark it so the browser's Date parses it as UTC.
    return value.replace(tzinfo=timezone.utc).isoformat() if value is not None else None


def plan_view(plan: AssistantChangePlan, threshold: int) -> PlanView:
    steps = plans.load_steps(plan)
    return PlanView(
        id=plan.id,
        status=plans.effective_status(plan),
        risk=plan.risk,
        count=plans.plan_count(steps),
        reversible=all(s.undoable for s in steps),
        read_only=all(s.facts.get("read_only") for s in steps),
        confirm=plans.confirmation(plan, steps, threshold),
        steps=[PlanStepView(
            seq=s.seq, name=s.name, summary=s.summary, count=s.count, facts=s.facts, risk=s.risk,
            reversible=s.reversible, state=s.state, error=s.error,
            starts_job=s.starts_job, job_page=s.job_page,
            job_id=s.result if s.starts_job and s.state == plans.STEP_DONE else None,
        ) for s in steps],
        error=plan.error,
        created_at=_iso(plan.created_at),
        expires_at=_iso(plan.expires_at),
        finished_at=_iso(plan.finished_at),
    )


def conversation_status(
    conversation: AssistantConversation,
    events: list[AssistantEvent],
    queue: Optional[RunQueueStatus] = None,
    plan_views: Optional[list[PlanView]] = None,
) -> ConversationStatus:
    by_id = {view.id: view for view in plan_views or []}
    return ConversationStatus(
        conversation=ConversationSummary.from_model(
            conversation, pending_plans=sum(1 for v in by_id.values() if v.status == PLAN_STATUS_PENDING)),
        status=conversation.status,
        started_at=_iso(conversation.run_started_at),
        messages=[TranscriptEvent.from_model(e, by_id) for e in events],
        queue=queue,
    )
