"""Drift guard for the design-token system.

All visual values (colors, in particular) must live in the token homes —
static/tokens.css (the :root contract) and the per-theme
static/themes/<slug>/tokens.css override blocks — and be referenced everywhere
else via var(--…). This test fails if a raw color literal (hex,
rgb()/rgba()/hsl(), or named white/black) appears in any migrated stylesheet,
so new CSS can't silently bypass the theming contract.

Page-specific stylesheets that haven't been migrated yet are listed in
NOT_YET_MIGRATED; remove each entry as its page is converted. New stylesheets
are enforced by default.
"""

import re
from pathlib import Path

import pytest

from yaffo import themes

STATIC_DIR = Path(__file__).resolve().parents[2] / "yaffo" / "static"

TOKENS_FILE = "tokens.css"

# The token homes: the contract file plus each theme's override block.
TOKEN_HOME_RE = re.compile(r"^(?:tokens\.css|themes/[a-z0-9-]+/tokens\.css)$")

# Migration complete — kept so any future stylesheet is enforced by default.
# Do not add files here.
NOT_YET_MIGRATED: set[str] = set()

RAW_COLOR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"          # hex literals
    r"|\b(?:rgb|rgba|hsl|hsla)\("    # functional color literals
    r"|(?<![-\w])(?:white|black)(?![-\w])",  # named colors (not white-space etc.)
)

# font-size must come from the type scale (or inherit); no literal px/rem/em.
RAW_FONT_SIZE = re.compile(r"font-size:(?!\s*(?:var\(|inherit\b))")


def _css_files() -> list[Path]:
    return [
        path
        for path in sorted(STATIC_DIR.rglob("*.css"))
        if "vendor" not in path.parts
    ]


def _relative(path: Path) -> str:
    return path.relative_to(STATIC_DIR).as_posix()


@pytest.mark.parametrize(
    "path",
    [p for p in _css_files() if not TOKEN_HOME_RE.match(_relative(p))],
    ids=_relative,
)
def test_no_raw_colors_outside_tokens(path: Path) -> None:
    relative = _relative(path)
    if relative in NOT_YET_MIGRATED:
        pytest.skip(f"{relative} not migrated to design tokens yet")

    violations = [
        f"  line {line_number}: {line.strip()}"
        for line_number, line in enumerate(path.read_text().splitlines(), start=1)
        if RAW_COLOR.search(line) or RAW_FONT_SIZE.search(line)
    ]
    assert not violations, (
        f"{relative} contains raw color or font-size values; use var(--…) "
        f"tokens from static/{TOKENS_FILE} instead:\n" + "\n".join(violations)
    )


TEMPLATES_DIR = STATIC_DIR.parent / "templates"

# The sandboxed widget iframe document links tokens.css but carries its own
# inline baseline/error styling instead of the app stylesheets.
TEMPLATE_EXEMPT = {"pages/widget_frame.html"}

TEMPLATE_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


@pytest.mark.parametrize(
    "path",
    sorted(TEMPLATES_DIR.rglob("*.html")),
    ids=lambda p: p.relative_to(TEMPLATES_DIR).as_posix(),
)
def test_no_styling_in_templates(path: Path) -> None:
    relative = path.relative_to(TEMPLATES_DIR).as_posix()
    if relative in TEMPLATE_EXEMPT:
        pytest.skip(f"{relative} is a sandboxed iframe document")

    text = path.read_text()
    violations = [
        f"  line {line_number}: {line.strip()}"
        for line_number, line in enumerate(text.splitlines(), start=1)
        if "<style" in line or TEMPLATE_HEX.search(line) or RAW_FONT_SIZE.search(line)
    ]
    assert not violations, (
        f"{relative} contains embedded styling (style blocks, hex colors, or "
        f"font sizes); move it to a stylesheet using var(--…) tokens:\n"
        + "\n".join(violations)
    )


def test_every_registered_theme_has_a_token_file() -> None:
    """Each built-in theme (except the :root default) must ship its
    [data-theme="slug"] override block in themes/<slug>/tokens.css, and there
    must be no orphan token files for unregistered slugs."""
    token_files = {
        path.parent.name: path for path in STATIC_DIR.glob("themes/*/tokens.css")
    }
    registered = set(themes.THEMES) - {themes.DEFAULT_THEME}
    assert set(token_files) == registered, (
        f"themes/<slug>/tokens.css files {sorted(token_files)} must match the "
        f"non-default entries of themes.THEMES {sorted(registered)}"
    )
    for slug, path in token_files.items():
        assert f'[data-theme="{slug}"]' in path.read_text(), (
            f"{path} must contain its own [data-theme=\"{slug}\"] block"
        )


def test_contract_file_declares_no_themes() -> None:
    """static/tokens.css is the :root contract only; theme overrides live in
    the per-theme token files."""
    tokens_text = (STATIC_DIR / TOKENS_FILE).read_text()
    tokens_text = re.sub(r"/\*.*?\*/", "", tokens_text, flags=re.DOTALL)
    assert "[data-theme" not in tokens_text, (
        "move [data-theme=…] blocks out of tokens.css into themes/<slug>/tokens.css"
    )


def test_not_yet_migrated_entries_still_exist() -> None:
    """Entries must be removed from NOT_YET_MIGRATED when their file is migrated
    or deleted, so the allowlist only ever shrinks."""
    missing = [name for name in NOT_YET_MIGRATED if not (STATIC_DIR / name).is_file()]
    assert not missing, f"Remove deleted files from NOT_YET_MIGRATED: {missing}"