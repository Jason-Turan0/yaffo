"""Theme catalog tools: list_themes and get_theme.

Read-only access to the existing themes — built-in and other custom ones — so the
agent can adapt a proven palette as a starting point instead of inventing every value
from scratch. The flow mirrors the widget templates: `list_themes` to browse what
exists, then `get_theme` to pull one theme's tokens (and skin) and reuse/tweak its
values.

Reads custom themes through the supplied session (the worker has no app context), the
same way the write tool persists.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from yaffo import themes
from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
)


class ThemeCatalogToolProvider(ToolProvider):
    LIST = "list_themes"
    GET = "get_theme"

    def __init__(self, session: Session):
        self.session = session

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(
                name=self.LIST,
                description=(
                    "List the existing themes (slug, label, and whether built-in). These are "
                    "working reference palettes — fetch one with get_theme to see how it values "
                    "the design tokens, then adapt it. Useful when the user says 'like the "
                    "darkroom theme but warmer'."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            RawToolDefinition(
                name=self.GET,
                description=(
                    "Get one theme's CSS by slug (a slug from list_themes): its tokens_css token "
                    "block and any skin_css. Read it for reference — reuse or tweak its token "
                    "values for your own theme; do not copy a built-in theme's icon-art file "
                    "paths (icons are inherited automatically)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string", "description": "Exact theme slug from list_themes."},
                    },
                    "required": ["slug"],
                    "additionalProperties": False,
                },
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        args = args or {}
        if name == self.LIST:
            return self._list()
        if name == self.GET:
            return self._get(args.get("slug"))
        return f"Unknown tool: {name}"

    def _list(self) -> str:
        lines = ["Existing themes — fetch one with get_theme to see its token values:"]
        labels = themes.list_themes(self.session)
        for slug, label in labels.items():
            kind = "built-in" if themes.is_builtin(slug) else "custom"
            lines.append(f"- {slug}: {label} ({kind})")
        return "\n".join(lines)

    def _get(self, slug: str | None) -> str:
        if not slug:
            return "get_theme requires a slug."
        assets = themes.read_theme_css(slug, self.session)
        if assets is None:
            return f"No theme named {slug!r}. Use list_themes to see what exists."
        if not assets.tokens_css:
            # Only the classic baseline ships no token-override block — its tokens are the
            # defaults already listed in your instructions.
            return f"{slug!r} is the classic baseline — its tokens are the defaults shown in your instructions."
        parts = [f"Theme {slug!r}:", "", "tokens_css:", assets.tokens_css]
        if assets.skin_css:
            parts += ["", "skin_css:", assets.skin_css]
        return "\n".join(parts)