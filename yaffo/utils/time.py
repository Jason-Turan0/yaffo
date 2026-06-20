"""Time helpers.

`utcnow` is the project's replacement for the deprecated `datetime.datetime.utcnow()`
(removed in a future Python). It returns the current UTC time as a **naive** datetime
— the storage convention everywhere here (DB columns are naive UTC; serializers stamp
them as UTC at the wire boundary, e.g. serializers._utc_iso). Computing it from a
timezone-aware `now` and dropping the tzinfo avoids the deprecation while keeping that
naive-UTC semantics identical, so existing rows and comparisons are unaffected.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (matches the stored convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
