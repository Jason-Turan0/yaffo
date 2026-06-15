"""Background task: run the automation-builder agent against a custom automation.

A chat request records the prompt, flips the automation to IN_PROGRESS, and enqueues
this task (see routes/utilities/automations.automations_chat). It runs the agent, persisting
each assistant / status / error line onto the automation's conversation, then
transitions it to READY (clean finish) or FAILED (error or token cap). The automation
is the durable draft -- the run survives tab close, and the browser observes by polling
the status instead of holding an in-request stream.

The write_automation_code tool writes the generated Starlark into the automation's
working_code as a side effect; this task only records the conversation and drives the
status transitions. Cancel is cooperative -- the agent polls get_automation_status
between iterations and stops when the automation is no longer IN_PROGRESS.
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.config import huey
from yaffo.background_tasks.utils import SessionFactory, get_automation_status
from yaffo.db.models import (
    AUTOMATION_STATUS_FAILED,
    AUTOMATION_STATUS_IN_PROGRESS,
    AUTOMATION_STATUS_READY,
    CONVERSATION_TYPE_ASSISTANT,
    CONVERSATION_TYPE_ERROR,
    CONVERSATION_TYPE_STATUS,
)
from yaffo.db.repositories import automation_repository as repo
from yaffo.logging_config import get_logger
from yaffo.page_builder import llm_config
from yaffo.page_builder.agent import create_automation_builder_agent
from yaffo.page_builder.prompt_generator.automation_user_prompt import build_automation_user_message

logger = get_logger(__name__, 'background_tasks')

# Friendly progress text per tool, shown as a status line in the conversation feed.
_TOOL_STATUS = {
    "write_automation_code": "Writing the automation…",
    "data_query": "Inspecting your data…",
}


def run_automation_generation(
    session: Session,
    slug: str,
    message: str,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> None:
    """Run the agent against a custom automation and persist its conversation /
    status. Split out from the huey wrapper so it can be driven from a test."""
    if should_cancel is None:
        should_cancel = lambda: get_automation_status(slug) != AUTOMATION_STATUS_IN_PROGRESS

    automation = repo.get_by_slug(session, slug)
    if automation is None:
        logger.warning(f"generate_automation: automation {slug!r} not found")
        return

    api_key = llm_config.get_api_key()
    if not api_key:
        text = "No API key configured. Add your Anthropic API key in Settings → AI Generation."
        repo.add_message(session, automation.id, CONVERSATION_TYPE_ERROR, text)
        repo.set_status(session, slug, AUTOMATION_STATUS_FAILED)
        return
    model = llm_config.get_model(session)

    automation_id = automation.id
    user_message = build_automation_user_message(
        message,
        slug=slug,
        name=automation.name,
        current_code=automation.working_code or automation.published_code or "",
    )

    try:
        agent = create_automation_builder_agent(slug, model=model, api_key=api_key, session=session)
        for event in agent.run_events(user_message, should_cancel=should_cancel):
            if event.type == "assistant":
                repo.add_message(session, automation_id, CONVERSATION_TYPE_ASSISTANT, event.text)
            elif event.type == "tool":
                repo.add_message(
                    session, automation_id, CONVERSATION_TYPE_STATUS,
                    _TOOL_STATUS.get(event.name, "Working…"),
                )
            elif event.type == "error":
                repo.add_message(session, automation_id, CONVERSATION_TYPE_ERROR, event.text)
                repo.set_status(session, slug, AUTOMATION_STATUS_FAILED)
                return
            elif event.type == "cancelled":
                logger.info(f"generate_automation: automation {slug!r} cancelled")
                return
        repo.set_status(session, slug, AUTOMATION_STATUS_READY)
    except Exception as exc:  # surface failures onto the automation, don't crash the worker
        logger.error(f"generate_automation: automation {slug!r} failed: {exc}", exc_info=True)
        session.rollback()
        repo.add_message(session, automation_id, CONVERSATION_TYPE_ERROR, f"Generation error: {exc}")
        repo.set_status(session, slug, AUTOMATION_STATUS_FAILED)


@huey.task()
def generate_automation_task(slug: str, message: str) -> None:
    session = SessionFactory()
    try:
        run_automation_generation(session, slug, message)
    finally:
        session.close()
        SessionFactory.remove()
