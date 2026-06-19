"""Builds the per-request user turn for the theme-builder agent.

This is the VOLATILE half of the prompt — the theme being designed (its slug, so the
model writes the exact `[data-theme="<slug>"]` selector), the user's request, and the
theme's current CSS so a follow-up edits with full sight of what's there (not blind).
It deliberately lives in the user turn (not the system prompt) so the cached system
prefix stays byte-identical across generations and across themes.

Sections are XML-delimited to match the system prompt; empty sections are omitted to
keep the turn tight.
"""
from __future__ import annotations

from typing import Optional

from yaffo.site_agents.prompt_generator.xml_helpers import block, el


def build_theme_user_message(
    request: str,
    *,
    slug: str,
    label: str = "",
    tokens_css: str = "",
    skin_css: str = "",
) -> str:
    """Assemble the user turn.

    Args:
        request: what the user asked for (the chat message).
        slug: the theme's slug — the model wraps its tokens in `[data-theme="<slug>"]`.
        label: the theme's display name, for context.
        tokens_css / skin_css: the theme's current CSS (its working draft if a
            generation is mid-flight, else the published theme). Empty for a brand-new
            theme; present on a follow-up so the model revises rather than restarts.
    """
    selector = f'[data-theme="{slug}"]'
    parts: list[str] = [block("request", (request.strip() or "").splitlines() or [""])]

    theme_children = [el("slug", slug), el("selector", selector)]
    if label:
        theme_children.insert(1, el("label", label))
    theme_children.append(
        el("instruction", f"Set your design tokens inside {selector} {{ … }} and pass them to write_theme.")
    )
    parts.append(block("theme", theme_children))

    current_children = []
    if tokens_css.strip():
        current_children.append(block("tokens_css", tokens_css.splitlines()))
    if skin_css.strip():
        current_children.append(block("skin_css", skin_css.splitlines()))
    if current_children:
        parts.append(
            block(
                "current_theme",
                [el("instructions", "The theme's current CSS — revise it to honor the request; keep what still fits.")]
                + current_children,
            )
        )

    return "\n\n".join(parts)