"""Drift guard for the documentation palette.

docs/stylesheets/extra.css maps Material's --md-* variables onto Yaffo's design
tokens, but MkDocs only publishes files underneath docs_dir, so it cannot import
yaffo/static/tokens.css — the values are copied in by hand. Each copied
declaration carries the token it came from in a trailing comment:

    --md-default-bg-color: #f8f9fa;   /* --color-bg */

These tests treat that comment as the contract and fail when the copy and the
token disagree, so a colour change in the app cannot silently leave the docs
behind. This file is outside STATIC_DIR, so test_design_tokens.py does not
see it.

Declarations with no annotation are values the app has no token for (Material's
translucent overlays, the footer bar) and are deliberately unchecked.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "docs" / "stylesheets" / "extra.css"
CLASSIC_TOKENS = ROOT / "yaffo" / "static" / "tokens.css"
DARKROOM_TOKENS = ROOT / "yaffo" / "static" / "themes" / "darkroom" / "tokens.css"

# `--md-thing: value;   /* --token-name maybe some prose */`
ANNOTATED = re.compile(
    r"^\s*[a-z-]+\s*:\s*(?P<value>[^;]+);\s*/\*\s*(?P<token>--[a-z0-9-]+)"
)
DECLARATION = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")
VAR = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*\)")


def _tokens(path: Path) -> dict[str, str]:
    """Both token files are a single block of custom properties (the app's own
    drift guard keeps them that way), so a flat scan is enough."""
    return dict(DECLARATION.findall(path.read_text()))


def _resolve(name: str, table: dict[str, str], depth: int = 0) -> str | None:
    """Tokens may point at other tokens — --color-navbar-bg is var(--color-surface)."""
    value = table.get(name)
    if value is None or depth > 8:
        return value
    return VAR.sub(lambda m: _resolve(m.group(1), table, depth + 1) or m.group(0), value)


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _dark_by_line(text: str) -> dict[int, bool]:
    """Map each line to whether it sits inside a slate-scoped block.

    Walks the file tracking brace depth so multi-line selectors and nested
    @media blocks are attributed correctly; comments are blanked first (keeping
    newlines) so braces in prose cannot unbalance the stack.
    """
    code = re.sub(
        r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S
    )
    stack: list[str] = []
    buffer = ""
    line = 1
    dark: dict[int, bool] = {}
    for char in code:
        if char == "\n":
            dark[line] = any("slate" in header for header in stack)
            line += 1
            buffer += " "
        elif char == "{":
            stack.append(buffer.strip())
            buffer = ""
        elif char == "}":
            if stack:
                stack.pop()
            buffer = ""
        elif char == ";":
            buffer = ""
        else:
            buffer += char
    dark[line] = any("slate" in header for header in stack)
    return dark


def _annotated_declarations() -> list[tuple[int, str, str, bool]]:
    """(line number, declared value, token name, is_dark) for each annotated line.

    Values that are themselves var() references are skipped: they cannot drift,
    because they resolve through the mapping rather than copying a literal.
    """
    text = BRIDGE.read_text()
    dark = _dark_by_line(text)
    found: list[tuple[int, str, str, bool]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = ANNOTATED.match(line)
        if match and "var(" not in match.group("value"):
            found.append(
                (number, match.group("value"), match.group("token"), dark.get(number, False))
            )
    return found


def test_annotations_are_present() -> None:
    """Guard the mechanism itself: the comments are the only link to the app."""
    assert len(_annotated_declarations()) > 50, (
        "docs/stylesheets/extra.css has lost its /* --token */ annotations; "
        "they are what ties the copied values back to the app's tokens"
    )


def test_annotated_values_match_the_app_tokens() -> None:
    classic = _tokens(CLASSIC_TOKENS)
    darkroom = {**classic, **_tokens(DARKROOM_TOKENS)}

    violations = []
    for number, value, token, is_dark in _annotated_declarations():
        table = darkroom if is_dark else classic
        expected = _resolve(token, table)
        if expected is None:
            continue  # reported by the test below
        if _normalise(value) != _normalise(expected):
            theme = "darkroom" if is_dark else "classic"
            violations.append(
                f"  line {number}: {token} is {_normalise(expected)} in {theme}, "
                f"but the docs use {_normalise(value)}"
            )

    assert not violations, (
        "docs/stylesheets/extra.css has drifted from the app's design tokens; "
        "update the copied values (or the /* --token */ comment if the mapping "
        "changed):\n" + "\n".join(violations)
    )


def test_annotations_name_real_tokens() -> None:
    """Scheme-aware on purpose: a token deleted from the classic contract is
    still "known" if darkroom happens to define it, which would let a rename slip
    past the value check (it skips what it cannot resolve). Darkroom inherits
    :root, so its table is the union; classic's is not."""
    classic = _tokens(CLASSIC_TOKENS)
    darkroom = {**classic, **_tokens(DARKROOM_TOKENS)}

    missing = [
        f"  line {number}: {token} (checked against {'darkroom' if is_dark else 'classic'})"
        for number, _, token, is_dark in _annotated_declarations()
        if _resolve(token, darkroom if is_dark else classic) is None
    ]
    assert not missing, (
        "docs/stylesheets/extra.css is annotated with tokens the app no longer "
        "defines:\n" + "\n".join(missing)
    )
