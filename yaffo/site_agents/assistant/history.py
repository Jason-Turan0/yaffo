"""Shapes a stored transcript into turns a model accepts."""
from __future__ import annotations

import json
from collections.abc import Callable
from xml.sax.saxutils import escape

from yaffo.db.models import ASSISTANT_EVENT_ASSISTANT, ASSISTANT_EVENT_USER, AssistantEvent
from yaffo.site_agents.assistant.prompt_generator.prompt import CONTEXT_FIELDS
from yaffo.site_agents.assistant.tool_providers.diagnostics.diagnostics import tool_names
from yaffo.site_agents.assistant.settings import DIAG_LIBRARY

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

_ROLE_FOR_KIND = {ASSISTANT_EVENT_USER: ROLE_USER, ASSISTANT_EVENT_ASSISTANT: ROLE_ASSISTANT}


def normalize_turns(events: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(kind, text) transcript entries → alternating (role, text) turns starting
    with the user. Consecutive entries from the same side are joined: several
    assistant texts from one run, or a user message whose run failed before any
    reply. Blank entries and any leading assistant text are dropped."""
    turns: list[tuple[str, str]] = []
    for kind, text in events:
        role = _ROLE_FOR_KIND.get(kind)
        text = text.strip()
        if role is None or not text:
            continue
        if not turns and role != ROLE_USER:
            continue
        if turns and turns[-1][0] == role:
            turns[-1] = (role, f"{turns[-1][1]}\n\n{text}")
        else:
            turns.append((role, text))
    return turns


def _payload(event: AssistantEvent) -> dict:
    try:
        payload = json.loads(event.payload) if event.payload else {}
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _historical_context(event: AssistantEvent, redact: Callable[[str], str]) -> str:
    """An earlier message's attached context (an "Ask Yaffo" button's job, page and
    error), replayed so follow-up turns still know what the conversation is about."""
    context = _payload(event).get("context")
    if not isinstance(context, dict):
        return ""
    fields = [f"{key}: {redact(str(context[key]))}" for key in CONTEXT_FIELDS if context.get(key)]
    if not fields:
        return ""
    return f"<historical_context>{escape('; '.join(fields))}</historical_context>"


def transcript_turns(events: list[AssistantEvent], groups: frozenset[str],
                     redact: Callable[[str], str]) -> list[tuple[str, str]]:
    """Replay bounded historical evidence as quoted data, never as new tool calls.

    Recheck enabled groups and redaction so disabling a diagnostic group stops
    its old raw results from being resent on follow-up turns. Earlier messages keep
    the context attached to them; the latest message's context is sent with that
    turn instead (build_assistant_user_message), so it isn't repeated here.
    """
    allowed = {"search_docs", "read_doc"} | set(tool_names(groups))
    if DIAG_LIBRARY in groups:
        allowed |= {"run_script", "describe_data_source", "link_to_page", "link_to_photos"}
    remaining = 24000
    evidence: dict[int, str] = {}
    for event in reversed(events):
        if event.kind != "tool" or not event.payload or remaining <= 0:
            continue
        try:
            payload = json.loads(event.payload)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        name = payload.get("tool")
        detail = payload.get("detail")
        if name == "media_item_report" and "metadata" not in groups:
            continue
        if name not in allowed or not isinstance(detail, str) or not detail:
            continue
        detail = redact(detail)[:min(6000, remaining)]
        remaining -= len(detail)
        evidence[event.seq] = (
            f'<historical_tool_result source="{escape(name)}">'
            f'{escape(detail)}</historical_tool_result>'
        )
    latest_user_seq = max((e.seq for e in events if e.kind == ASSISTANT_EVENT_USER), default=None)
    turns = []
    for event in events:
        if event.kind == ASSISTANT_EVENT_USER and event.seq != latest_user_seq:
            context = _historical_context(event, redact)
            turns.append((event.kind, f"{event.content}\n\n{context}" if context else event.content))
        elif event.kind in _ROLE_FOR_KIND:
            turns.append((event.kind, event.content))
        elif event.seq in evidence:
            turns.append((ROLE_ASSISTANT, evidence[event.seq]))
    return normalize_turns(turns)
