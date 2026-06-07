"""Builds the per-request user turn for the page-builder agent.

This is the VOLATILE half of the prompt — the page's theme, the user's request,
the widgets already on the page, and any runtime errors fed back for repair. It
deliberately lives in the user turn (not the system prompt) so the cached system
prefix stays byte-identical across generations.

Sections are XML-delimited to match the system prompt; structured sections use
nested elements; empty sections are omitted to keep the turn tight.
"""
from __future__ import annotations

import json
from typing import Optional

from yaffo.page_builder.prompt_generator.xml_helpers import block, el


def build_user_message(
    request: str,
    *,
    page_title: str = "",
    page_description: str = "",
    widgets: Optional[list[dict]] = None,
    widget_errors: Optional[dict] = None,
) -> str:
    """Assemble the user turn.

    Args:
        request: what the user asked for (the chat message).
        page_title / page_description: page-level context.
        widgets: existing widgets on the page, each {id, title} plus its current
            {layout, data_query, html, css, js} so the model edits with full sight
            of the code (not blind). Empty code fields are fine for new/empty ones.
        widget_errors: {widget_id: [error messages]} captured at runtime, fed
            back so the model can repair code that threw.
    """
    widgets = widgets or []
    widget_errors = widget_errors or {}
    # Request is free-form natural language; keep it raw for fidelity.
    parts: list[str] = [block("request", (request.strip() or "").splitlines() or [""])]

    page_children = []
    if page_title:
        page_children.append(el("title", page_title))
    if page_description:
        page_children.append(el("description", page_description))
    if page_children:
        parts.append(block("page", page_children))

    if widgets:
        parts.append(
            block(
                "existing_widgets",
                [
                    block(
                        "widget",
                        [
                            el("layout", "", x=w.get("grid_x"), y=w.get("grid_y"),
                               w=w.get("grid_w"), h=w.get("grid_h")),
                            el("data_query", json.dumps(w.get("data_query") or {})),
                            el("html", w.get("html") or ""),
                            el("css", w.get("css") or ""),
                            el("js", w.get("js") or ""),
                        ],
                        id=w.get("id"),
                        title=w.get("title") or "Untitled widget",
                    )
                    for w in widgets
                ],
            )
        )

    title_by_id = {str(w.get("id")): (w.get("title") or "") for w in widgets}
    error_blocks = [
        block(
            "widget",
            [el("error", m) for m in messages],
            id=widget_id,
            title=title_by_id.get(str(widget_id), ""),
        )
        for widget_id, messages in widget_errors.items()
        if messages
    ]
    if error_blocks:
        parts.append(
            block(
                "widget_errors",
                [el("instructions", "Runtime errors from widgets you generated — fix the code that caused them.")]
                + error_blocks,
            )
        )

    return "\n\n".join(parts)