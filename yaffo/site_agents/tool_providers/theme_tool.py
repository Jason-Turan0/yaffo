"""Theme tool: write_theme.

The tool the theme-builder agent calls to design a custom theme. The provider is
scoped to a single theme **slug** at construction, so the model can only touch that
theme — it supplies CSS (and optional assets); the server validates and persists.

Like the widget tool, this **persists directly into the theme in the database** (the
theme's ``working_theme``): the theme is the durable draft, so a generation survives
disconnect and the browser observes it by polling. The theme's ``published_theme`` is
untouched until the working draft is published.

A theme is pure presentation: the whole app is token-driven, so "design a theme" means
re-valuing the design tokens inside the theme's ``[data-theme="<slug>"]`` block (plus
optional scoped structural CSS and assets). The tool sanitises what the model writes —
the override block must be present and scoped, structural CSS must be token-driven, and
nothing that breaks the widget-frame CSP (external URLs, scripts, @import) is allowed —
returning the problems as the tool result so the model can fix and retry.
"""
from __future__ import annotations

import re
from dataclasses import asdict

from sqlalchemy.orm import Session

from yaffo import themes
from yaffo.themes import ThemeAssets
from yaffo.site_agents.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)

# Content that would break the widget-frame CSP or escape the stylesheet. tokens_css
# and skin_css are loaded as plain CSS (tokens even inside sandboxed widget frames),
# so they must stay inert.
_UNSAFE_RE = re.compile(r"</?script|</?style|@import|javascript:|expression\s*\(", re.IGNORECASE)

# url() pointing off-origin (absolute or protocol-relative): the frame's connect-src is
# 'none', so remote fonts/images must not sneak in through CSS.
_EXTERNAL_URL_RE = re.compile(r"""url\(\s*['"]?\s*(?:https?:)?//""", re.IGNORECASE)

# Raw color literals (the design-token drift guard, mirrored from
# tests/yaffo/test_design_tokens.py): colors belong in tokens_css as --color-* values,
# so structural skin CSS must reference them via var(--…), never hard-code them.
_RAW_COLOR_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"
    r"|\b(?:rgb|rgba|hsl|hsla)\("
    r"|(?<![-\w])(?:white|black)(?![-\w])"
)


class ThemeToolProvider(ToolProvider):
    WRITE = "write_theme"

    def __init__(self, slug: str, session: Session):
        # The theme this run designs, and the session used to persist (a request's
        # db.session or a worker's SessionFactory session — the tool doesn't care).
        self.slug = slug
        self.session = session

    @property
    def _selector(self) -> str:
        return f'[data-theme="{self.slug}"]'

    def get_tools(self) -> list[RawToolDefinition]:
        selector = self._selector
        return [
            RawToolDefinition(
                name=self.WRITE,
                description=(
                    "Save the theme's design. Re-value the app's design tokens to set the look; "
                    f"wrap the whole token block in `{selector} {{ … }}`. Call again to refine — "
                    "each call replaces the working draft. tokens_css is required; the rest are "
                    "optional."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "tokens_css": {
                            "type": "string",
                            "description": (
                                f"The token override block: `{selector} {{ --color-bg: …; --color-accent: …; … }}`. "
                                "Set every token needed for a cohesive, accessible palette."
                            ),
                        },
                        "skin_css": {
                            "type": "string",
                            "description": (
                                f"Optional structural flourishes. Scope EVERY rule to `{selector} …` and use "
                                "var(--token) values only — no raw colors."
                            ),
                        },
                        "favicon_svg": {
                            "type": "string",
                            "description": "Optional inline <svg> for the tab icon / brand mark.",
                        },
                        "placeholder_svg": {
                            "type": "string",
                            "description": "Optional inline <svg> shown in place of a missing image.",
                        },
                    },
                    "required": ["tokens_css"],
                    "additionalProperties": False,
                },
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name == self.WRITE:
            return self._write(args or {})
        return f"Unknown tool: {name}"

    def _validate(self, tokens_css: str, skin_css: str) -> list[str]:
        selector = self._selector
        problems: list[str] = []
        if not tokens_css:
            problems.append("tokens_css is required.")
        elif selector not in tokens_css:
            problems.append(f"tokens_css must wrap its tokens in a `{selector} {{ … }}` block.")

        for label, css in (("tokens_css", tokens_css), ("skin_css", skin_css)):
            if css and _UNSAFE_RE.search(css):
                problems.append(f"{label} must be inert CSS — no <script>/<style>, @import, javascript:, or expression().")
            if css and _EXTERNAL_URL_RE.search(css):
                problems.append(f"{label} must not reference external url() resources (the widget frame blocks off-origin loads).")

        if skin_css:
            if ":root" in skin_css:
                problems.append(f"skin_css must not target :root — scope every rule to `{selector}`.")
            elif selector not in skin_css:
                problems.append(f"skin_css must scope its selectors to `{selector}`.")
            if _RAW_COLOR_RE.search(skin_css):
                problems.append("skin_css must use var(--token) colors, not raw values — define colors as --color-* tokens in tokens_css.")
        return problems

    def _write(self, args: dict) -> CallToolReturn:
        tokens_css = (args.get("tokens_css") or "").strip()
        skin_css = (args.get("skin_css") or "").strip()
        favicon_svg = (args.get("favicon_svg") or "").strip()
        placeholder_svg = (args.get("placeholder_svg") or "").strip()

        problems = self._validate(tokens_css, skin_css)
        if problems:
            return (
                "Theme not saved — fix these and call write_theme again:\n"
                + "\n".join(f"- {p}" for p in problems)
            )

        theme = themes.get_custom_theme(self.slug, self.session)
        if theme is None:
            return f"Theme {self.slug!r} not found."

        # Optional fields fall back to the prior draft, so a refining call can change
        # just the tokens without dropping a skin/asset set earlier.
        prior = theme.working_theme
        theme.working_theme = ThemeAssets(
            tokens_css=tokens_css,
            skin_css=skin_css or (prior.skin_css if prior else ""),
            favicon_svg=favicon_svg or (prior.favicon_svg if prior else ""),
            placeholder_svg=placeholder_svg or (prior.placeholder_svg if prior else ""),
        )
        themes.save_custom_theme(theme, self.session)

        extras = [name for name, value in (
            ("skin_css", skin_css), ("favicon_svg", favicon_svg), ("placeholder_svg", placeholder_svg),
        ) if value]
        summary = f"Saved the working theme for {self.slug!r} ({len(tokens_css)} chars of tokens"
        summary += f"; also set {', '.join(extras)}." if extras else ")."
        return ToolResult(
            model_text=summary,
            host_data={"slug": self.slug, **asdict(theme.working_theme)},
        )