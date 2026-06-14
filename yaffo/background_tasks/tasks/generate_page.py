"""Background task: run the page-builder agent against a working PageVersion.

A chat request forks an IN_PROGRESS version (see
custom_page_repository.fork_version) and enqueues this task. It runs the agent,
persisting each generated widget and each assistant / status / error line onto the
version, then transitions it to READY (clean finish) or FAILED (error or token cap).
The version is the durable draft -- the run survives tab close / disconnect, and the
browser observes by polling the version instead of holding an in-request stream.

DB access follows the other background tasks: a `SessionFactory` session, no Flask
app context. Cancel is cooperative -- the agent polls `get_version_status` between
iterations and stops when the version is CANCELLED (or has been deleted).
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.config import huey
from yaffo.background_tasks.utils import SessionFactory, get_version_status
from yaffo.db.models import (
    CONVERSATION_TYPE_ASSISTANT,
    CONVERSATION_TYPE_ERROR,
    CONVERSATION_TYPE_STATUS,
    PAGE_VERSION_STATUS_CANCELLED,
    PAGE_VERSION_STATUS_FAILED,
    PAGE_VERSION_STATUS_READY,
)
from yaffo.db.repositories import custom_page_repository as page_repo
from yaffo.logging_config import get_logger
from yaffo.page_builder import llm_config
from yaffo.page_builder.agent import create_theme_builder_agent, create_page_builder_agent
from yaffo.page_builder.prompt_generator import build_user_message, build_system_prompt
from yaffo.page_builder.serializers import widget_draft

logger = get_logger(__name__, 'background_tasks')

# Friendly progress text per tool, shown as a status line in the conversation feed;
# anything not listed falls back to a generic "Working…".
_TOOL_STATUS = {
    "create_widget": "Creating widget…",
    "update_widget": "Updating widget…",
    "run_data_query": "Looking up information…",
    "list_widget_templates": "Browsing templates…",
    "get_widget_template": "Picking a template…",
}


def run_generation(
    session: Session,
    version_id: int,
    message: str,
    widget_errors: Optional[dict] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> None:
    """Run the agent against a version and persist its widgets / conversation /
    status. Split out from the huey wrapper so it can be driven directly from a test
    or script. `should_cancel` defaults to polling the version's status."""
    widget_errors = widget_errors or {}
    if should_cancel is None:
        should_cancel = lambda: get_version_status(version_id) == PAGE_VERSION_STATUS_CANCELLED

    version = page_repo.get_version(session, version_id)
    if version is None:
        logger.warning(f"generate_page: version {version_id} not found")
        return
    page = version.page

    # Resolve the model + key here (the worker has no app context, so the agent and
    # client take them explicitly rather than reaching for db.session / a global).
    api_key = llm_config.get_api_key()
    if not api_key:
        message_text = "No API key configured. Add your Anthropic API key in Settings → AI Generation."
        page_repo.add_version_message(session, version_id, CONVERSATION_TYPE_ERROR, message_text)
        page_repo.set_version_status(session, version_id, PAGE_VERSION_STATUS_FAILED, error=message_text, completed=True)
        return
    model = llm_config.get_model(session)

    current_widgets = [widget_draft(w) for w in version.widgets]
    user_message = build_user_message(
        message,
        page_title=page.title,
        page_subtitle=page.subtitle,
        widgets=current_widgets,
        widget_errors=widget_errors,
    )

    try:
        agent = create_page_builder_agent(version_id, model=model, api_key=api_key, session=session)
        for event in agent.run_events(user_message, should_cancel=should_cancel):
            if event.type == "assistant":
                page_repo.add_version_message(session, version_id, CONVERSATION_TYPE_ASSISTANT, event.text)
            elif event.type == "tool":
                # The widget tool persists its own changes into the version; the task
                # just records the progress line.
                page_repo.add_version_message(
                    session, version_id, CONVERSATION_TYPE_STATUS,
                    _TOOL_STATUS.get(event.name, "Working…"),
                )
            elif event.type == "error":
                page_repo.add_version_message(session, version_id, CONVERSATION_TYPE_ERROR, event.text)
                page_repo.set_version_status(
                    session, version_id, PAGE_VERSION_STATUS_FAILED, error=event.text, completed=True
                )
                return
            elif event.type == "cancelled":
                # The cancel route deletes the version and reverts; nothing to persist.
                logger.info(f"generate_page: version {version_id} cancelled")
                return
        page_repo.set_version_status(session, version_id, PAGE_VERSION_STATUS_READY, completed=True)
    except Exception as exc:  # surface failures onto the version, don't crash the worker
        logger.error(f"generate_page: version {version_id} failed: {exc}", exc_info=True)
        session.rollback()
        page_repo.add_version_message(session, version_id, CONVERSATION_TYPE_ERROR, f"Generation error: {exc}")
        page_repo.set_version_status(
            session, version_id, PAGE_VERSION_STATUS_FAILED, error=str(exc), completed=True
        )


@huey.task()
def generate_page_task(version_id: int, message: str, widget_errors: Optional[dict] = None):
    session = SessionFactory()
    try:
        run_generation(session, version_id, message, widget_errors)
    finally:
        session.close()
        SessionFactory.remove()