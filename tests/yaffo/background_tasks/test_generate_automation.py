"""Guard for the automation-builder progress labels.

The conversation feed shows a friendly status line per tool the agent calls
(_TOOL_STATUS); anything unmapped falls back to a generic "Working…". This test
keeps that map in sync with the agent's real tool set, so a newly added tool (or a
renamed one — the data-query tool is `run_data_query`, not the sandbox's `data_query`)
gets a label instead of silently defaulting to "Working…".
"""
import pytest

from yaffo.background_tasks.tasks.generate_automation import _TOOL_STATUS
from yaffo.site_agents.tool_providers.automation_tool import AutomationToolProvider
from yaffo.site_agents.tool_providers.automation_trigger_tool import AutomationTriggerToolProvider
from yaffo.site_agents.tool_providers.data_query_tool import DataQueryToolProvider

pytestmark = pytest.mark.unit


def _agent_tool_names() -> set[str]:
    # The same providers create_automation_builder_agent wires; get_tools doesn't
    # touch the session, so a sentinel is fine.
    session = object()
    providers = [
        DataQueryToolProvider(session=session),
        AutomationToolProvider("slug", session=session),
        AutomationTriggerToolProvider("slug", session=session),
    ]
    return {tool.name for provider in providers for tool in provider.get_tools()}


def test_every_agent_tool_has_a_progress_label():
    missing = _agent_tool_names() - set(_TOOL_STATUS)
    assert not missing, f"_TOOL_STATUS is missing progress text for: {sorted(missing)}"
