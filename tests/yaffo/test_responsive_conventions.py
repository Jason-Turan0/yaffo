"""Drift guards for the conventions in docs/development/responsive.md.

Two rules there are mechanically checkable, and both fail silently when broken —
a stray breakpoint just works on the author's screen, and a `vh` without its
dynamic-unit companion only misbehaves once mobile browser chrome collapses.
Neither shows up in a Playwright run on a desktop-sized viewport.

Rules that need a rendered page (scroll ownership, state across resize, minimum
targets) stay in the Playwright suites; this file only covers what is decidable
from the stylesheet text.
"""

import re
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[2] / "yaffo" / "static"

# The shared boundaries documented in docs/development/responsive.md. A page may
# introduce another only when its content demonstrates the need — which means
# editing this set, deliberately, in the same change.
ALLOWED_BREAKPOINTS = {
    "max-width: 400px",    # navbar button text hides so three controls fit a row
    "max-width: 640px",    # single-column nav, sheet modals, icon pagination
    "max-width: 720px",    # isolated component wrapping
    "max-width: 900px",    # two-column page layouts become one
    "max-width: 1200px",   # the narrow shell: Menu, navbar-hosted page panels
    "min-width: 641px",    # the 640 boundary's complement
}

MEDIA_WIDTH_RE = re.compile(r"\(\s*(max|min)-width\s*:\s*([0-9.]+)px\s*\)")

# Declaration blocks. Flat enough for this codebase: an @media wrapper contains
# its rules, and each inner rule still matches as a brace pair with no braces.
BLOCK_RE = re.compile(r"\{([^{}]*)\}")
DECLARATION_RE = re.compile(r"([-a-zA-Z]+)\s*:\s*([^;]+)")

# `100vh` but not the `vh` inside `100dvh` / `100svh`: the character before the
# unit is a digit or a dot only in the static-unit case.
STATIC_VH_RE = re.compile(r"[\d.]vh\b")
DYNAMIC_VH_RE = re.compile(r"[\d.](?:d|s|l)vh\b")

# Properties that bind a box to the viewport, where the browser's collapsing
# toolbars are what make the static unit wrong.
VIEWPORT_BOUND_PROPERTIES = {
    "height",
    "max-height",
    "min-height",
    "top",
    "bottom",
    "inset",
    "inset-block",
    "inset-block-start",
    "inset-block-end",
}


def _css_files() -> list[Path]:
    return sorted(STATIC_DIR.rglob("*.css"))


def _relative(path: Path) -> str:
    return str(path.relative_to(STATIC_DIR))


@pytest.mark.parametrize("css_file", _css_files(), ids=_relative)
def test_media_queries_use_the_shared_breakpoints(css_file: Path) -> None:
    """Width breakpoints come from the documented set.

    Breakpoints are content-driven, but an undocumented one is almost always an
    author matching their own window rather than the content. Adding a boundary
    is allowed; adding it without recording it is not.
    """
    text = css_file.read_text(encoding="utf-8")
    violations = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if "@media" not in line:
            continue
        for direction, pixels in MEDIA_WIDTH_RE.findall(line):
            measure = float(pixels)
            rounded = int(measure) if measure == int(measure) else measure
            width = f"{direction}-width: {rounded}px"
            if width not in ALLOWED_BREAKPOINTS:
                violations.append(f"  line {line_number}: ({width})")

    assert not violations, (
        f"{_relative(css_file)} uses width breakpoints outside the shared set.\n"
        + "\n".join(violations)
        + "\n\nThe documented boundaries are:\n  "
        + "\n  ".join(sorted(ALLOWED_BREAKPOINTS))
        + "\n\nIf the content genuinely needs another one, add it to "
        "ALLOWED_BREAKPOINTS with a comment saying what changes there, and "
        "record it in docs/development/responsive.md."
    )


@pytest.mark.parametrize("css_file", _css_files(), ids=_relative)
def test_viewport_heights_pair_vh_with_a_dynamic_unit(css_file: Path) -> None:
    """A `vh` on a viewport-bound property is followed by a `dvh` line.

    `100vh` measures the viewport as though the mobile browser's chrome were not
    there, so a bar pinned to the bottom sits under it. The convention is both
    lines, static first, so engines without dynamic units keep the old value and
    everything else takes the correct one.
    """
    text = css_file.read_text(encoding="utf-8")
    violations = []

    for block in BLOCK_RE.finditer(text):
        body = block.group(1)
        static_properties: set[str] = set()
        dynamic_properties: set[str] = set()

        for name, value in DECLARATION_RE.findall(body):
            prop = name.lower()
            if prop not in VIEWPORT_BOUND_PROPERTIES:
                continue
            if DYNAMIC_VH_RE.search(value):
                dynamic_properties.add(prop)
            elif STATIC_VH_RE.search(value):
                static_properties.add(prop)

        missing = static_properties - dynamic_properties
        if missing:
            line_number = text.count("\n", 0, block.start()) + 1
            for prop in sorted(missing):
                violations.append(
                    f"  line ~{line_number}: `{prop}` uses vh with no dvh companion"
                )

    assert not violations, (
        f"{_relative(css_file)} binds a box to the viewport with a static unit "
        "only.\n" + "\n".join(violations) + "\n\nDeclare both, static first:\n"
        "    height: calc(100vh - 60px);\n"
        "    height: calc(100dvh - 60px);"
    )
