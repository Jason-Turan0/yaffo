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

from yaffo.db.models import AssistantConversation, AssistantEvent
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

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationSummary:
    id: int
    title: str
    status: str
    updated_at: Optional[str]

    @classmethod
    def from_model(cls, conversation: AssistantConversation) -> "ConversationSummary":
        return cls(
            id=conversation.id,
            title=conversation.title,
            status=conversation.status,
            updated_at=_iso(conversation.updated_at),
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
    def from_model(cls, event: AssistantEvent) -> "TranscriptEvent":
        return cls(
            seq=event.seq,
            type=event.kind,
            content=event.content,
            payload=json.loads(event.payload) if event.payload else None,
        )


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


def conversation_status(
    conversation: AssistantConversation,
    events: list[AssistantEvent],
    queue: Optional[RunQueueStatus] = None,
) -> ConversationStatus:
    return ConversationStatus(
        conversation=ConversationSummary.from_model(conversation),
        status=conversation.status,
        started_at=_iso(conversation.run_started_at),
        messages=[TranscriptEvent.from_model(e) for e in events],
        queue=queue,
    )
