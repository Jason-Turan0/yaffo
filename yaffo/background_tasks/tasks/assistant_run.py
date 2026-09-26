"""Background task: answer the latest message in an assistant conversation.

The messages route records the user's message, marks the conversation RUNNING,
and enqueues this task. It replays the earlier turns, runs the assistant agent
(docs tools, plus the diagnostics the user enabled in Settings), and appends each assistant reply, tool call, and error to the
transcript as it happens, then settles the conversation to IDLE (or FAILED). The
browser follows the run by polling the conversation, so it survives a closed
dialog or a page change.

Cancel is cooperative: the route flips the conversation off RUNNING and the agent
stops at its next iteration. Errors are recorded with a machine code in the
payload so the browser can show them in the user's language.
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory, get_assistant_status
from yaffo.db.models import (
    ASSISTANT_EVENT_ASSISTANT,
    ASSISTANT_EVENT_ERROR,
    ASSISTANT_EVENT_TOOL,
    ASSISTANT_STATUS_FAILED,
    ASSISTANT_STATUS_IDLE,
    ASSISTANT_STATUS_RUNNING,
)
from yaffo.db.repositories import assistant_repository as repo
from yaffo.i18n import DEFAULT_LOCALE, get_saved_locale
from yaffo.logging_config import get_logger
from yaffo.site_agents.agent import create_assistant_agent
from yaffo.site_agents.assistant import settings as assistant_settings
from yaffo.site_agents.assistant.call_logs import conversation_log_dir
from yaffo.site_agents.assistant.history import ROLE_USER, transcript_turns
from yaffo.site_agents.assistant.prompt_generator.prompt import build_assistant_user_message
from yaffo.site_agents.assistant.redact import redactor_for

logger = get_logger(__name__, 'background_tasks')

# Error codes the browser localizes (assistant:errors.<code>).
ERROR_API_KEY_MISSING = "api_key_missing"
ERROR_MODEL = "model_error"
ERROR_OUTPUT_LIMIT = "output_limit"
ERROR_ITERATION_LIMIT = "iteration_limit"
ERROR_RUN_FAILED = "run_failed"

_ERROR_FOR_STOP_REASON = {
    "max_tokens": ERROR_OUTPUT_LIMIT,
    "max_iterations": ERROR_ITERATION_LIMIT,
}


def _fail(session: Session, conversation_id: int, code: str, text: str, **details: str) -> None:
    repo.add_event(session, conversation_id, ASSISTANT_EVENT_ERROR, text, {"code": code, **details})
    repo.set_status(session, conversation_id, ASSISTANT_STATUS_FAILED)


def run_assistant_turn(
    session: Session,
    conversation_id: int,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> None:
    """Answer the conversation's latest user message. Split out from the task
    wrapper so a test can drive it."""
    if should_cancel is None:
        def default_should_cancel() -> bool:
            return get_assistant_status(conversation_id) != ASSISTANT_STATUS_RUNNING

        should_cancel = default_should_cancel

    if repo.get_conversation(session, conversation_id) is None:
        logger.warning(f"assistant_run: conversation {conversation_id} not found")
        return

    diagnostics = assistant_settings.enabled_diagnostics(session)
    redactor = redactor_for(session, redact_people=assistant_settings.redact_people(session))
    turns = transcript_turns(repo.list_events(session, conversation_id), diagnostics, redactor)
    if not turns or turns[-1][0] != ROLE_USER:
        repo.set_status(session, conversation_id, ASSISTANT_STATUS_IDLE)
        return

    model = assistant_settings.resolve_model(session)
    api_key = assistant_settings.api_key(session)
    if not api_key:
        provider = assistant_settings.provider_label(session)
        _fail(
            session, conversation_id, ERROR_API_KEY_MISSING,
            f"No API key configured. Add your {provider} API key in Settings → AI Generation.",
            provider=provider,
        )
        return

    history, (_, message) = turns[:-1], turns[-1]
    user_message = build_assistant_user_message(
        message, locale=get_saved_locale(session) or DEFAULT_LOCALE,
        context=repo.latest_user_context(session, conversation_id))
    try:
        agent = create_assistant_agent(
            model=model, api_key=api_key, history=history, session=session,
            log_dir=conversation_log_dir(conversation_id),
            diagnostics=diagnostics,
            redactor=redactor,
            model_label=f"{assistant_settings.provider_label(session)} · {assistant_settings.model_label(session)}",
        )
        for event in agent.run_events(user_message, should_cancel=should_cancel):
            if event.type == "cancelled" or should_cancel():
                logger.info(f"assistant_run: conversation {conversation_id} cancelled")
                return
            if event.type == "assistant" and event.text.strip():
                repo.add_event(session, conversation_id, ASSISTANT_EVENT_ASSISTANT, event.text.strip())
            elif event.type == "tool":
                payload = event.tool_result_data or {"tool": event.name, "error": event.is_error}
                repo.add_event(session, conversation_id, ASSISTANT_EVENT_TOOL, "", payload)
            elif event.type == "error":
                code = _ERROR_FOR_STOP_REASON.get(event.stop_reason or "", ERROR_MODEL)
                _fail(session, conversation_id, code, event.text)
                return
        repo.set_status(session, conversation_id, ASSISTANT_STATUS_IDLE)
    except Exception as exc:  # record the failure on the conversation; don't crash the worker
        logger.error(f"assistant_run: conversation {conversation_id} failed: {exc}", exc_info=True)
        session.rollback()
        _fail(session, conversation_id, ERROR_RUN_FAILED, f"The assistant stopped with an error: {exc}")


@task_queue.task()
def assistant_run_task(conversation_id: int) -> None:
    session = SessionFactory()
    try:
        run_assistant_turn(session, conversation_id)
    finally:
        session.close()
        SessionFactory.remove()
