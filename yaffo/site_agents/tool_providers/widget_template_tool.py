"""Widget template tools: list_widget_templates and get_widget_template.

Exposes the curated catalog (yaffo/site_agents/widget_templates.py) to the agent so
it adapts a hand-styled, design-token-based reference design instead of inventing a
look from scratch (which drifts). The flow is: `list_widget_templates` to browse names
+ purposes, then `get_widget_template` to pull one template's full data_query + html /
css / js, which the model tweaks and passes to `create_widget`.

Static, read-only data — no session or DB needed.
"""
from __future__ import annotations

import json

from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
)
from yaffo.site_agents.widget_templates import TEMPLATES, TEMPLATES_BY_NAME


class WidgetTemplateToolProvider(ToolProvider):
    LIST = "list_widget_templates"
    GET = "get_widget_template"

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(
                name=self.LIST,
                description=(
                    "List the curated widget templates (name + what each is for). They are "
                    "hand-styled reference designs built on the real data contract (named "
                    "data_query + the window.yaffo API) and styled with the app's design "
                    "tokens (var(--color-…), var(--radius-…), var(--font-size-…)) so they "
                    "follow the active theme. Browse these before writing a widget, then "
                    "fetch one with get_widget_template and adapt it."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            RawToolDefinition(
                name=self.GET,
                description=(
                    "Get one widget template's full data_query + html/css/js by name (a name "
                    "from list_widget_templates). Adapt it — change the queries, content, and "
                    "title to the user's request — then create it with create_widget. Prefer "
                    "adapting a template over inventing a design from scratch."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Exact template name from list_widget_templates."},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        args = args or {}
        if name == self.LIST:
            return self._list()
        if name == self.GET:
            return self._get(args.get("name"))
        return f"Unknown tool: {name}"

    def _list(self) -> str:
        lines = [
            "Curated widget templates — fetch one with get_widget_template, then adapt it "
            "and call create_widget:",
        ]
        lines += [f"- {t.name}: {t.description}" for t in TEMPLATES]
        return "\n".join(lines)

    def _get(self, name: str | None) -> str:
        if not name:
            return "get_widget_template requires a name."
        template = TEMPLATES_BY_NAME.get(name)
        if template is None:
            return f"No template named {name!r}. Available: {', '.join(TEMPLATES_BY_NAME)}."
        return json.dumps(
            {
                "name": template.name,
                "description": template.description,
                "data_query": template.data_query,
                "html": template.html,
                "css": template.css,
                "js": template.js,
                "grid_w": template.grid_w,
                "grid_h": template.grid_h,
            },
            indent=2,
        )
