"""Read-only checks run for a contextual "Ask Yaffo" message before the model starts.

A button on a failed job card or a run history row already says what the question
is about, so the app runs the check the model would start with (job_detail for a
job, automation_runs for an automation) and puts the result in the user turn.
That saves the model a round.

The checks go through DiagnosticsToolProvider, so they follow the same Settings
switches, redaction and caps as when the model calls them. Each one is also stored
as a tool event: the chat shows it as an activity line, and later turns replay it
as historical evidence. A check that fails (e.g. the job was deleted) is left out;
the model can still look it up itself.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from yaffo.site_agents.assistant.redact import Redactor
from yaffo.site_agents.assistant.settings import DIAG_JOBS
from yaffo.site_agents.assistant.tool_providers.diagnostics.diagnostics import DiagnosticsToolProvider
from yaffo.site_agents.common.tool_providers.tool_provider_types import ToolResult

# Recent runs shown for an automation the user asked from.
AUTOMATION_RUNS = 3


def prefetch_checks(context: Optional[dict]) -> list[tuple[str, dict]]:
    """The (tool, args) calls a message's context asks for, before any switch."""
    if not context:
        return []
    calls: list[tuple[str, dict]] = []
    if context.get("job_id"):
        calls.append(("job_detail", {"job_id": str(context["job_id"])}))
    if context.get("automation"):
        calls.append(("automation_runs", {"slug": str(context["automation"]), "limit": AUTOMATION_RUNS}))
    return calls


def prefetch_for_context(
    session: Session, context: Optional[dict], diagnostics: frozenset[str], redactor: Redactor,
) -> list[ToolResult]:
    """Run the checks the context asks for, when their diagnostics group is on."""
    if DIAG_JOBS not in diagnostics:
        return []
    provider = DiagnosticsToolProvider(session, groups=diagnostics, redactor=redactor)
    results = []
    for name, args in prefetch_checks(context):
        result = provider.call_tool(name, args)
        if isinstance(result, ToolResult) and not result.host_data.get("error"):
            results.append(result)
    return results
