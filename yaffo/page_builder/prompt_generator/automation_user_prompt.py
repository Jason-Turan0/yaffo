"""Builds the per-request user turn for the automation-builder agent.

The VOLATILE half of the prompt — the automation being built (slug + name) and its
current code so a follow-up edits with full sight of what's there. Lives in the
user turn (not the system prompt) so the cached system prefix stays byte-identical
across generations.
"""
from __future__ import annotations

from yaffo.page_builder.prompt_generator.xml_helpers import block, el


def build_automation_user_message(
    request: str,
    *,
    slug: str,
    name: str = "",
    current_code: str = "",
) -> str:
    """Assemble the user turn.

    Args:
        request: the chat message.
        slug: the automation's slug (passed to write_automation_code via the tool).
        name: the automation's display name, for context.
        current_code: the automation's current code (working draft if mid-flight,
            else published). Empty for a brand-new automation; present on a follow-up
            so the model revises rather than restarts.
    """
    parts: list[str] = [block("request", (request.strip() or "").splitlines() or [""])]

    automation_children = [el("slug", slug)]
    if name:
        automation_children.append(el("name", name))
    automation_children.append(
        el("instruction", "Write the Starlark script and pass it to write_automation_code.")
    )
    parts.append(block("automation", automation_children))

    if current_code.strip():
        parts.append(block(
            "current_code",
            [el("instructions", "The automation's current script — revise it to honor the request; keep what still fits.")]
            + [block("code", current_code.splitlines())],
        ))

    return "\n\n".join(parts)
