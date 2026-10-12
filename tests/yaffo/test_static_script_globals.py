"""Guard: no two static scripts declare the same top-level name.

The app's scripts are classic scripts that share one global scope, so a second
top-level `const el` throws "Identifier 'el' has already been declared" and the whole
second file silently never runs. Vitest loads files as ES modules (each with its own
scope), so it can't see this; this test does."""
import re
from collections import defaultdict
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

STATIC = Path(__file__).resolve().parents[2] / "yaffo" / "static"
TOP_LEVEL = re.compile(r"^(?:const|let|var|class|function|async function)\s+([A-Za-z_$][\w$]*)", re.M)


def test_top_level_names_are_unique_across_static_scripts():
    declared: dict[str, set[str]] = defaultdict(set)
    for script in STATIC.rglob("*.js"):
        if "vendor" in script.parts:
            continue
        for name in TOP_LEVEL.findall(script.read_text(encoding="utf-8")):
            declared[name].add(script.relative_to(STATIC).as_posix())
    clashes = {name: sorted(files) for name, files in declared.items() if len(files) > 1}
    assert clashes == {}, "prefix these names (e.g. syncEl) or wrap the file's helpers in its namespace"
