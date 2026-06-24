"""Tool providers for the page-builder agent."""
from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    ContentBlock,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
    to_anthropic_tools,
    to_openai_tools,
)
from yaffo.site_agents.tool_providers.utils import truncate_tool_result
from yaffo.site_agents.tool_providers.data_query_tool import DataQueryToolProvider
from yaffo.site_agents.tool_providers.widget_tool import WidgetToolProvider
from yaffo.site_agents.tool_providers.widget_template_tool import WidgetTemplateToolProvider
from yaffo.site_agents.tool_providers.theme_tool import ThemeToolProvider
from yaffo.site_agents.tool_providers.theme_catalog_tool import ThemeCatalogToolProvider
from yaffo.site_agents.tool_providers.automation_tool import AutomationToolProvider
from yaffo.site_agents.tool_providers.automation_trigger_tool import AutomationTriggerToolProvider

__all__ = [
    "CallToolReturn",
    "ContentBlock",
    "RawToolDefinition",
    "ToolProvider",
    "ToolResult",
    "to_anthropic_tools",
    "to_openai_tools",
    "truncate_tool_result",
    "DataQueryToolProvider",
    "WidgetToolProvider",
    "WidgetTemplateToolProvider",
    "ThemeToolProvider",
    "ThemeCatalogToolProvider",
    "AutomationToolProvider",
    "AutomationTriggerToolProvider",
]