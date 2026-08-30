"""Drift guard for the documentation palette.

The MkDocs site does not restate the app's colours: hooks/design_tokens.py
publishes yaffo/static/tokens.css (and the darkroom theme's override block) into
the build, and docs/stylesheets/extra.css maps Material's --md-* variables onto
those tokens. These tests keep that arrangement honest — the docs stylesheet is
outside STATIC_DIR, so test_design_tokens.py does not see it.
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "docs" / "stylesheets" / "extra.css"
HOOK = ROOT / "hooks" / "design_tokens.py"
CLASSIC_TOKENS = ROOT / "yaffo" / "static" / "tokens.css"
DARKROOM_TOKENS = ROOT / "yaffo" / "static" / "themes" / "darkroom" / "tokens.css"

# Kept in step with RAW_COLOR in test_design_tokens.py rather than imported from
# it: nothing else in the suite imports across test modules, and bare `pytest`
# (how CI invokes it) puts tests/yaffo on sys.path, not the repository root.
RAW_COLOR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"           # hex literals
    r"|\b(?:rgb|rgba|hsl|hsla)\("    # functional color literals
    r"|(?<![-\w])(?:white|black)(?![-\w])",  # named colors
)

DECLARATION = re.compile(r"^\s*(--[a-z0-9-]+)\s*:", re.MULTILINE)
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# Material's own variables and the locals this file defines for itself; every
# other var(--…) reference must resolve to an app token.
LOCAL_PREFIXES = ("--md-", "--yaffo-")


def _hook_constant(name: str) -> str:
    """Read a string constant out of the hook without importing it (the hook
    imports mkdocs, which is only installed with the `docs` extra)."""
    module = ast.parse(HOOK.read_text())
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            assert isinstance(node.value, ast.Constant)
            return node.value.value
    raise AssertionError(f"{HOOK.name} no longer defines {name}")


def _without_comments(css: str) -> str:
    """Blank out comment bodies, keeping newlines so line numbers still line up.

    Unlike the app's guard this file has to explain colour decisions in prose
    ("too heavy on white"), and a line-based scan would read that as a value.
    """
    return COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), css)


def test_bridge_declares_no_raw_colors() -> None:
    """The docs palette must come from tokens, exactly like app stylesheets."""
    source = _without_comments(BRIDGE.read_text())
    violations = [
        f"  line {number}: {line.strip()}"
        for number, line in enumerate(source.splitlines(), start=1)
        if RAW_COLOR.search(line)
    ]
    assert not violations, (
        "docs/stylesheets/extra.css contains raw color values; map Material's "
        "--md-* variables onto var(--…) tokens instead:\n" + "\n".join(violations)
    )


def test_bridge_only_references_defined_tokens() -> None:
    """A renamed or deleted token would otherwise leave var() unresolved, which
    fails silently in the browser rather than at build time."""
    defined = set(DECLARATION.findall(CLASSIC_TOKENS.read_text()))
    referenced = {
        name
        for name in re.findall(r"var\((--[a-z0-9-]+)", BRIDGE.read_text())
        if not name.startswith(LOCAL_PREFIXES)
    }
    assert referenced, "the docs bridge should reference the app's design tokens"
    assert not referenced - defined, (
        "docs/stylesheets/extra.css references tokens that "
        f"{CLASSIC_TOKENS.name} no longer defines: {sorted(referenced - defined)}"
    )


def test_hook_can_rescope_the_darkroom_block() -> None:
    """The hook rewrites darkroom's selector to Material's slate scheme. If the
    app renames its theme hook, the docs' dark palette would stop matching
    anything — the build raises, and this catches it sooner."""
    selector = _hook_constant("DARKROOM_SELECTOR")
    assert selector in DARKROOM_TOKENS.read_text(), (
        f"{DARKROOM_TOKENS.relative_to(ROOT)} no longer contains {selector!r}; "
        "update DARKROOM_SELECTOR in hooks/design_tokens.py"
    )
