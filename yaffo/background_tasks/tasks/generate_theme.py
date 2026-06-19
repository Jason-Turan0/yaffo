"""Background task: run the theme-builder agent against a custom theme.

A chat request records the prompt, flips the theme to IN_PROGRESS, and enqueues this
task (see routes/themes_page.themes_chat). It runs the agent, persisting each
assistant / status / error line onto the theme's transcript, then transitions it to
READY (clean finish) or FAILED (error or token cap). The theme is the durable draft --
the run survives tab close / disconnect, and the browser observes by polling the theme
(routes/themes_page.themes_status) instead of holding an in-request stream.

The agent's theme tool writes the generated CSS into the theme's working_theme as a
side effect (the same way the page builder's widget tool writes widgets); this task
only records the conversation and drives the status transitions.

DB access follows the other background tasks: a `SessionFactory` session, no Flask app
context. themes persistence takes that session explicitly. Cancel is cooperative -- the
agent polls `get_theme_status` between iterations and stops when the theme is no longer
IN_PROGRESS (cancelled or deleted).
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

from yaffo import themes
from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory, get_theme_status
from yaffo.db.models import (
    CONVERSATION_TYPE_ASSISTANT,
    CONVERSATION_TYPE_ERROR,
    CONVERSATION_TYPE_STATUS,
    PAGE_VERSION_STATUS_FAILED,
    PAGE_VERSION_STATUS_IN_PROGRESS,
    PAGE_VERSION_STATUS_READY,
)
from yaffo.logging_config import get_logger
from yaffo.site_agents import llm_config
from yaffo.site_agents.agent import create_theme_builder_agent
from yaffo.site_agents.prompt_generator.theme_user_prompt import build_theme_user_message

logger = get_logger(__name__, 'background_tasks')

# Friendly progress text per tool, shown as a status line in the conversation feed;
# anything not listed falls back to a generic "Working…".
_TOOL_STATUS = {
    "write_theme": "Designing the theme…",
    "list_themes": "Browsing themes…",
    "get_theme": "Studying a theme…",
}


def run_theme_generation(
    session: Session,
    slug: str,
    message: str,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> None:
    """Run the agent against a custom theme and persist its conversation / status.
    Split out from the task wrapper so it can be driven directly from a test or
    script. `should_cancel` defaults to polling the theme's status."""
    if should_cancel is None:
        should_cancel = lambda: get_theme_status(slug) != PAGE_VERSION_STATUS_IN_PROGRESS

    theme = themes.get_custom_theme(slug, session)
    if theme is None:
        logger.warning(f"generate_theme: theme {slug!r} not found")
        return

    # Resolve the model + key here (the worker has no app context, so the agent and
    # client take them explicitly rather than reaching for db.session / a global).
    api_key = llm_config.get_api_key()
    if not api_key:
        message_text = "No API key configured. Add your Anthropic API key in Settings → AI Generation."
        themes.add_theme_message(slug, CONVERSATION_TYPE_ERROR, message_text, session)
        themes.set_theme_status(slug, PAGE_VERSION_STATUS_FAILED, session)
        return
    model = llm_config.get_model(session)

    # Edit with sight of the current CSS: the working draft if a generation already
    # produced one, else the published theme (empty for a brand-new theme).
    current = theme.working_theme or theme.published_theme
    user_message = build_theme_user_message(
        message,
        slug=slug,
        label=theme.label,
        tokens_css=current.tokens_css if current else "",
        skin_css=current.skin_css if current else "",
    )

    try:
        agent = create_theme_builder_agent(slug, model=model, api_key=api_key, session=session)
        for event in agent.run_events(user_message, should_cancel=should_cancel):
            if event.type == "assistant":
                themes.add_theme_message(slug, CONVERSATION_TYPE_ASSISTANT, event.text, session)
            elif event.type == "tool":
                # The theme tool persists its CSS into the theme's working_theme; the
                # task just records the progress line.
                themes.add_theme_message(
                    slug, CONVERSATION_TYPE_STATUS,
                    _TOOL_STATUS.get(event.name, "Working…"), session,
                )
            elif event.type == "error":
                themes.add_theme_message(slug, CONVERSATION_TYPE_ERROR, event.text, session)
                themes.set_theme_status(slug, PAGE_VERSION_STATUS_FAILED, session)
                return
            elif event.type == "cancelled":
                # The cancel route flags the theme; nothing to persist here.
                logger.info(f"generate_theme: theme {slug!r} cancelled")
                return
        themes.set_theme_status(slug, PAGE_VERSION_STATUS_READY, session)
    except Exception as exc:  # surface failures onto the theme, don't crash the worker
        logger.error(f"generate_theme: theme {slug!r} failed: {exc}", exc_info=True)
        session.rollback()
        themes.add_theme_message(slug, CONVERSATION_TYPE_ERROR, f"Generation error: {exc}", session)
        themes.set_theme_status(slug, PAGE_VERSION_STATUS_FAILED, session)


@task_queue.task()
def generate_theme_task(slug: str, message: str) -> None:
    session = SessionFactory()
    try:
        run_theme_generation(session, slug, message)
    finally:
        session.close()
        SessionFactory.remove()