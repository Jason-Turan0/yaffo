"""Builds the theme-builder agent's system prompt: the stable contract the model
designs a theme against, organized into XML-delimited sections (helps the model attend
to and recall each block).

Keep this content STABLE — it's the cached prefix sent on every generation. Nothing
volatile is interpolated here: the per-theme selector (`[data-theme="<slug>"]`) lives
on the slug-scoped write_theme tool, not in this shared prefix, so the cache holds
across every theme. The token list is derived from static/tokens.css (the :root
contract), so it tracks the real tokens without drifting — and changes only when the
contract does, which keeps caching valid.
"""
from __future__ import annotations

import re
from pathlib import Path

from yaffo.site_agents.prompt_generator.xml_helpers import block

# yaffo/site_agents/prompt_generator/theme_system_prompt.py -> yaffo/static/...
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
_TOKENS_FILE = _STATIC_DIR / "tokens.css"
# The classic theme ships one icon-<name>.svg per data-icon the app uses, so the set
# of overridable icon names is derived from it (rather than hard-coded, which drifts).
_CLASSIC_ICON_DIR = _STATIC_DIR / "themes" / "classic"

_ROOT_BLOCK_RE = re.compile(r":root\s*\{(.*?)\n\}", re.DOTALL)
_DECL_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


def _token_defaults() -> list[str]:
    """`--token: classic-default` for every custom property in the :root contract,
    in source order. Derived from tokens.css so the advertised tokens always match
    what the app actually consumes (and what a theme overrides)."""
    text = _TOKENS_FILE.read_text(encoding="utf-8")
    match = _ROOT_BLOCK_RE.search(text)
    body = match.group(1) if match else ""
    return [f"{name}: {value.strip()}" for name, value in _DECL_RE.findall(body)]


def _icon_names() -> list[str]:
    """The data-icon names the app uses, from the classic icon set (icon-<name>.svg)."""
    return sorted(p.stem.removeprefix("icon-") for p in _CLASSIC_ICON_DIR.glob("icon-*.svg"))


def _role() -> str:
    return block("role", [
        "You design cohesive visual themes for a personal photo-organization app.",
        "The entire app is token-driven: every color, font, radius, and shadow comes from a",
        "CSS custom property. So 'design a theme' means choosing a harmonious set of values",
        "for those tokens — you never touch the app's HTML or component structure.",
    ])


def _core_rule() -> str:
    return block("core_rule", [
        "You restyle by RE-VALUING TOKENS, nothing else.",
        "- The app renders the same markup for every theme; only the token values change.",
        "- Set a value for every token that matters to the look — don't leave defaults that",
        "  clash with your palette. The defaults below are the built-in 'classic' look.",
        "- Save your work with the write_theme tool. It validates what you send and reports",
        "  any problems to fix; the theme isn't stored until it passes.",
    ])


def _tokens() -> str:
    return block("tokens", [
        "The design tokens, each shown with its classic default. Override them in your",
        "token block to define the theme:",
        *_token_defaults(),
    ])


def _contract() -> str:
    return block("contract", [
        "write_theme accepts:",
        "- tokens_css (required): the token override block. Every declaration goes inside the",
        '  theme\'s `[data-theme="<slug>"] { … }` selector — the tool tells you the exact',
        "  selector and rejects a block that isn't wrapped in it. Set tokens as",
        "  `--token: value;` lines.",
        "- skin_css (optional): structural flourishes (gradients, borders, decorative",
        "  ::before marks). Scope EVERY rule to the same theme selector, and use var(--token)",
        "  values only — raw colors are rejected, because color belongs in the tokens.",
        "- favicon_svg / placeholder_svg (optional): inline <svg> for the tab icon/brand mark",
        "  and the missing-image placeholder.",
        "Everything must be inert, self-contained CSS/SVG: no @import, no external url(),",
        "no <script> — the tokens load even inside sandboxed widget frames.",
        "Nav/button icons are inherited and recolor to your palette automatically — you",
        "never need to provide them. To replace the icon ART, see <icons>.",
    ])


def _icons() -> str:
    return block("icons", [
        "Every nav/button icon is inherited for free: the app draws it as a currentColor",
        "MASK, so it tints to each control's text color and already matches your palette.",
        "Leave them alone unless the user wants different icon art.",
        "",
        "To replace an icon, override it in skin_css. The default paints the glyph with a",
        "mask tinted by `background-color`, which flattens art to one color — so for your",
        "own (especially multi-color) art, UNSET the mask and use a background image:",
        '  [data-theme="<slug>"] [data-icon="add"]::before {',
        "    -webkit-mask-image: none; mask-image: none;   /* drop the inherited mask */",
        "    background-color: transparent;",
        "    background-image: url(\"data:image/svg+xml,<svg .../></svg>\");",
        "    background-size: contain; background-position: center; background-repeat: no-repeat;",
        "  }",
        "The art must be a self-contained inline data: URI (no external url()). Inside it,",
        "single-quote the SVG attributes and percent-encode '#' as %23 so the url() stays",
        "valid — e.g. fill='%23ff0066'.",
        "",
        "Overridable icon names (each used as [data-icon=\"NAME\"]):",
        "  " + ", ".join(_icon_names()),
        "",
        "For a full worked example, call get_theme('neobrutalist') — it overrides the whole",
        "icon set this exact way, with inline data: URIs you can adapt.",
    ])


def _references() -> str:
    return block("references", [
        "You can stand on existing themes instead of starting cold:",
        "- list_themes: browse the existing themes (built-in and custom).",
        "- get_theme(slug): read one theme's token values (and skin) to reuse or tweak —",
        "  handy for requests like 'like the darkroom theme but warmer', or to copy how a",
        "  theme overrides its icons (neobrutalist is the icon-override example).",
    ])


def _conventions() -> str:
    return block("conventions", [
        "- Build a complete, accessible palette: pick surfaces and text with enough contrast",
        "  to read comfortably, and keep accent/status colors distinct.",
        "- Move tokens together so the result is harmonious — e.g. tint borders and shadows",
        "  toward the surface/accent hues rather than leaving neutral greys.",
        "- font-size tokens are a fixed scale (the rungs the UI uses); change their values to",
        "  re-pitch the type, but keep them in ascending order.",
        "- Keep going until the theme is coherent, then write it and give the user a short",
        "  summary of the look you chose. Call write_theme again to refine.",
    ])


def build_template_builder_system_prompt() -> str:
    """Assemble the stable, XML-delimited theme-builder system prompt. Parameterless on
    purpose — the per-theme selector lives on the tool, so this prefix stays cacheable
    across every theme."""
    return "\n\n".join([
        _role(),
        _core_rule(),
        _tokens(),
        _contract(),
        _icons(),
        _references(),
        _conventions(),
    ])