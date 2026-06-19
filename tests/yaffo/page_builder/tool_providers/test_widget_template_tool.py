"""Unit tests for the widget template tools (list_widget_templates / get_widget_template)."""
import json

import pytest

from yaffo.site_agents.tool_providers.widget_template_tool import WidgetTemplateToolProvider
from yaffo.site_agents.widget_templates import TEMPLATES, TEMPLATES_BY_NAME

pytestmark = pytest.mark.unit


@pytest.fixture
def provider():
    return WidgetTemplateToolProvider()


class TestTools:
    def test_advertises_list_and_get(self, provider):
        tools = {t.name: t for t in provider.get_tools()}
        assert set(tools) == {"list_widget_templates", "get_widget_template"}
        assert tools["get_widget_template"].input_schema["required"] == ["name"]
        assert tools["list_widget_templates"].input_schema["properties"] == {}

    def test_unknown_tool(self, provider):
        assert "Unknown tool" in provider.call_tool("nope", {})


class TestList:
    def test_lists_every_template_name_and_description(self, provider):
        out = provider.call_tool("list_widget_templates", {})
        for t in TEMPLATES:
            assert t.name in out
            assert t.description[:30] in out


class TestGet:
    def test_returns_full_template_as_json(self, provider):
        name = next(iter(TEMPLATES_BY_NAME))
        payload = json.loads(provider.call_tool("get_widget_template", {"name": name}))
        template = TEMPLATES_BY_NAME[name]
        assert payload["name"] == name
        assert payload["data_query"] == template.data_query
        assert payload["html"] == template.html
        assert payload["css"] == template.css
        assert payload["js"] == template.js

    def test_unknown_name_reports_available(self, provider):
        out = provider.call_tool("get_widget_template", {"name": "Nonexistent"})
        assert "No template named" in out
        assert next(iter(TEMPLATES_BY_NAME)) in out  # lists what's available

    def test_missing_name_is_reported(self, provider):
        assert "requires a name" in provider.call_tool("get_widget_template", {})
