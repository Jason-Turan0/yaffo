from __future__ import annotations

import time
from collections import deque


class RateLimiter:
    """Sliding-window rate limiter keyed by an arbitrary string (client IP).

    Small and honest rather than clever: a deque of event times per key,
    pruned on every check. Key count is bounded by pruning empty keys, so a
    scanner cycling IPs can't grow memory without also sustaining traffic.
    """

    def __init__(self, max_events: int, per_seconds: float) -> None:
        self._max_events = max_events
        self._per_seconds = per_seconds
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = self._events.setdefault(key, deque())
        while window and now - window[0] > self._per_seconds:
            window.popleft()
        if len(window) >= self._max_events:
            return False
        window.append(now)
        self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        for key, window in list(self._events.items()):
            while window and now - window[0] > self._per_seconds:
                window.popleft()
            if not window:
                del self._events[key]
