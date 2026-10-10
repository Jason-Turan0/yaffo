"""Earlier turns of a builder conversation (page, theme, automation), replayed to the
model so a follow-up keeps what was said before, not only what reached the code.

A builder's transcript holds user / assistant / status / error lines, recorded as the
run goes. Only user and assistant lines are conversation (CONVERSATION_MODEL_TYPES);
status and error lines are display-only. The latest user line is the request being
answered: it's sent as this run's own user turn, wrapped with the current code, so
it's left out here.
"""
from __future__ import annotations

from typing import Iterable

from yaffo.db.models import CONVERSATION_MODEL_TYPES, CONVERSATION_TYPE_ASSISTANT, CONVERSATION_TYPE_USER

# Earlier turns are context for the request, not its subject: the current code
# carries the state. Oldest turns are dropped first once over this many characters.
MAX_HISTORY_CHARS = 24000

# Stands in for the reply of a request whose run failed or was cancelled, so turns
# still alternate when the user sent another message after it.
NO_REPLY = "(No reply: that request didn't finish.)"


def builder_history(entries: Iterable[tuple[str, str]],
                    max_chars: int = MAX_HISTORY_CHARS) -> list[tuple[str, str]]:
    """(role, text) turns for `load_history`, from a transcript's (type, content)
    entries in order, the current request last. Turns alternate, starting with the
    user and ending with the assistant."""
    lines = [(kind, content) for kind, content in entries
             if kind in CONVERSATION_MODEL_TYPES and content and content.strip()]
    for index in range(len(lines) - 1, -1, -1):
        if lines[index][0] == CONVERSATION_TYPE_USER:
            del lines[index]
            break

    turns: list[tuple[str, str]] = []
    for kind, content in lines:
        if turns and turns[-1][0] == kind:
            if kind == CONVERSATION_TYPE_USER:
                turns.append((CONVERSATION_TYPE_ASSISTANT, NO_REPLY))
                turns.append((kind, content))
            else:
                turns[-1] = (kind, f"{turns[-1][1]}\n\n{content}")
        elif turns or kind == CONVERSATION_TYPE_USER:
            turns.append((kind, content))
    if turns and turns[-1][0] == CONVERSATION_TYPE_USER:
        turns.append((CONVERSATION_TYPE_ASSISTANT, NO_REPLY))

    # Keep whole user/assistant pairs from the newest back, within the budget.
    kept: list[tuple[str, str]] = []
    used = 0
    for start in range(len(turns) - 2, -1, -2):
        pair = turns[start:start + 2]
        size = sum(len(text) for _, text in pair)
        if kept and used + size > max_chars:
            break
        kept[:0] = pair
        used += size
    return kept
