"""Background task: run the service builder against one custom adapter.

Like generate_automation: the chat route records the request, marks the adapter's
builder IN_PROGRESS and enqueues this task; the task runs the agent, records each
reply / status / error line on the adapter's conversation, and ends READY or FAILED.
The browser polls the status. write_adapter_code saves the draft as a side effect.
Cancel is cooperative: the agent stops once the builder is no longer IN_PROGRESS.
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

from yaffo.background_tasks.config import task_queue
from yaffo.background_tasks.utils import SessionFactory
from yaffo.db.models import (
    AUTOMATION_STATUS_FAILED, AUTOMATION_STATUS_IN_PROGRESS, AUTOMATION_STATUS_READY, CONVERSATION_TYPE_ASSISTANT,
    CONVERSATION_TYPE_ERROR, CONVERSATION_TYPE_STATUS, CONVERSATION_TYPE_TEST, SyncAdapter,
)
from yaffo.db.repositories import service_builder_repository as services
from yaffo.i18n import DEFAULT_LOCALE, get_saved_locale
from yaffo.logging_config import get_logger
from yaffo.site_agents import llm_config
from yaffo.site_agents.adapter.prompt_generator.adapter_user_prompt import build_adapter_user_message
from yaffo.site_agents.agent import create_adapter_builder_agent
from yaffo.site_agents.common.history import builder_history
from yaffo.sync.adapter_static import analyze_adapter
from yaffo.taskq import PRIORITY_INTERACTIVE

logger = get_logger(__name__, 'background_tasks')

_TOOL_STATUS = {
    "write_adapter_code": "Writing the service…",
    "test_adapter": "Testing the service…",
    "fetch_docs": "Reading the service's documentation…",
}


def run_adapter_generation(session: Session, adapter_id: int, message: str,
                           should_cancel: Optional[Callable[[], bool]] = None) -> None:
    """Run the agent and record its conversation. Split from the task for tests."""
    if should_cancel is None:
        def default_should_cancel() -> bool:
            # Its own session, not the task's scoped one, which this mustn't close.
            check = SessionFactory.session_factory()
            try:
                return services.builder_status(check, adapter_id) != AUTOMATION_STATUS_IN_PROGRESS
            finally:
                check.close()
        should_cancel = default_should_cancel

    row = session.get(SyncAdapter, adapter_id)
    if row is None:
        logger.warning(f"generate_adapter: adapter {adapter_id} not found")
        return
    model = llm_config.get_model(session)
    api_key = llm_config.get_api_key(llm_config.selected_model_provider(session))
    if not api_key:
        services.add_message(session, adapter_id, CONVERSATION_TYPE_ERROR,
                             f"No API key configured. Add your {llm_config.selected_provider_label(session)} "
                             "API key in Settings → AI Generation.")
        services.set_builder_status(session, adapter_id, AUTOMATION_STATUS_FAILED)
        return

    entries = services.messages(session, adapter_id)
    last_test = next((m.content for m in reversed(entries) if m.type == CONVERSATION_TYPE_TEST), "")
    code = row.working_code or row.published_code or ""
    problems = [str(p) for p in analyze_adapter(code).problems] if code and not row.working else []
    user_message = build_adapter_user_message(
        message, name=row.name, locale=get_saved_locale(session) or DEFAULT_LOCALE, current_code=code,
        problems=problems, published=bool(row.published), last_test=last_test)
    try:
        agent = create_adapter_builder_agent(adapter_id, model=model, api_key=api_key, session=session,
                                             history=builder_history((m.type, m.content) for m in entries))
        for event in agent.run_events(user_message, should_cancel=should_cancel):
            if event.type == "assistant":
                services.add_message(session, adapter_id, CONVERSATION_TYPE_ASSISTANT, event.text)
            elif event.type == "tool":
                services.add_message(session, adapter_id, CONVERSATION_TYPE_STATUS,
                                     _TOOL_STATUS.get(event.name, "Working…"))
            elif event.type == "error":
                services.add_message(session, adapter_id, CONVERSATION_TYPE_ERROR, event.text)
                services.set_builder_status(session, adapter_id, AUTOMATION_STATUS_FAILED)
                return
            elif event.type == "cancelled":
                return
        services.set_builder_status(session, adapter_id, AUTOMATION_STATUS_READY)
    except Exception as exc:  # surface failures on the conversation, don't crash the worker
        logger.error(f"generate_adapter: adapter {adapter_id} failed: {exc}", exc_info=True)
        session.rollback()
        services.add_message(session, adapter_id, CONVERSATION_TYPE_ERROR, f"Generation error: {exc}")
        services.set_builder_status(session, adapter_id, AUTOMATION_STATUS_FAILED)


@task_queue.task(priority=PRIORITY_INTERACTIVE)
def generate_adapter_task(adapter_id: int, message: str) -> None:
    session = SessionFactory()
    try:
        run_adapter_generation(session, adapter_id, message)
    finally:
        session.close()
        SessionFactory.remove()
