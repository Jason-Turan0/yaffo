"""Access to the widget-runtime JS API source (static/pages/widget_api.js).

The same source is used in two places, so they can never diverge:
1. inlined into every widget iframe (so widget code can call window.yaffo), and
2. embedded in the system prompt (so the model writes widgets against the real,
   current API).

One reader for both. Cached, so it's read once and stays byte-stable across a run
(which keeps the system-prompt prefix cacheable).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_WIDGET_API_PATH = Path(__file__).resolve().parents[1] / "static" / "pages" / "widget_api.js"


@lru_cache(maxsize=1)
def widget_api_source() -> str:
    """The contents of static/pages/widget_api.js."""
    return _WIDGET_API_PATH.read_text(encoding="utf-8")