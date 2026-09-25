"""Shapes a stored transcript into turns a model accepts."""
from __future__ import annotations

from yaffo.db.models import ASSISTANT_EVENT_ASSISTANT, ASSISTANT_EVENT_USER

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
