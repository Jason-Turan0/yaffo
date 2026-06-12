"""Drift guard for the design-token system.

All visual values (colors, in particular) must live in static/tokens.css and be
referenced everywhere else via var(--…). This test fails if a raw color literal
(hex, rgb()/rgba()/hsl(), or named white/black) appears in any migrated
stylesheet, so new CSS can't silently bypass the theming contract.

Page-specific stylesheets that haven't been migrated yet are listed in
NOT_YET_MIGRATED; remove each entry as its page is converted. New stylesheets
are enforced by default.
"""

import re
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[2] / "yaffo" / "static"

TOKENS_FILE = "tokens.css"

# Migration complete — kept so any future stylesheet is enforced by default.
# Do not add files here.
NOT_YET_MIGRATED: set[str] = set()

RAW_COLOR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"          # hex literals
    r"|\b(?:rgb|rgba|hsl|hsla)\("    # functional color literals
    r"|(?<![-\w])(?:white|black)(?![-\w])",  # named colors (not white-space etc.)
)


def _css_files() -> list[Path]:
    return [
        path
        for path in sorted(STATIC_DIR.rglob("*.css"))
        if "vendor" not in path.parts
    ]


def _relative(path: Path) -> str:
    return path.relative_to(STATIC_DIR).as_posix()


@pytest.mark.parametrize(
    "path", [p for p in _css_files() if _relative(p) != TOKENS_FILE], ids=_relative
)
def test_no_raw_colors_outside_tokens(path: Path) -> None:
    relative = _relative(path)
    if relative in NOT_YET_MIGRATED:
        pytest.skip(f"{relative} not migrated to design tokens yet")

    violations = [
        f"  line {line_number}: {line.strip()}"
        for line_number, line in enumerate(path.read_text().splitlines(), start=1)
        if RAW_COLOR.search(line)
    ]
    assert not violations, (
        f"{relative} contains raw color values; use var(--…) tokens from "
        f"static/{TOKENS_FILE} instead:\n" + "\n".join(violations)
    )


def test_not_yet_migrated_entries_still_exist() -> None:
    """Entries must be removed from NOT_YET_MIGRATED when their file is migrated
    or deleted, so the allowlist only ever shrinks."""
    missing = [name for name in NOT_YET_MIGRATED if not (STATIC_DIR / name).is_file()]
    assert not missing, f"Remove deleted files from NOT_YET_MIGRATED: {missing}"