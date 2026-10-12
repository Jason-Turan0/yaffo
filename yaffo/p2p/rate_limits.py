"""Serving-side rate limits on what peers can ask of this device.

A paired peer is trusted with its grants, not with unlimited work: a buggy or
compromised peer must not be able to pin this device's CPU, disk, or the hub
relay with requests. Each kind of request gets a token bucket per peer, checked
AFTER the request's signature is verified, so a third party spoofing a peer's
device id can't spend that peer's allowance.

Pairing confirms come from devices that aren't known yet, so they share one
bucket for the whole device: the cap bounds guessing at pending pairing codes.

The limits sit well above what a real client does (a transfer batch pulls 1 MiB
chunks three files at a time; the remote gallery loads a page of previews) and
only bite on traffic no Yaffo client produces. A refused request answers
`{"status": "error", "code": "rate_limited", "retry_after": <seconds>}`;
session clients wait that long and retry (see puller.SessionHolder).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

RATE_LIMITED_CODE = "rate_limited"
PAIRING_KEY = "*"


@dataclass(frozen=True)
class BucketLimit:
    per_second: float
    burst: int


# request type -> limit per peer
PEER_LIMITS: dict[str, BucketLimit] = {
    "pull_file": BucketLimit(per_second=64, burst=128),
    "pull_preview": BucketLimit(per_second=30, burst=100),
    "list_files": BucketLimit(per_second=10, burst=30),
    "list_shared": BucketLimit(per_second=5, burst=20),
    "pairing_confirm": BucketLimit(per_second=10 / 60, burst=10),
}


class _Bucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, tokens: float, updated: float) -> None:
        self.tokens = tokens
        self.updated = updated


class PeerRateLimiter:
    def __init__(
        self,
        limits: Optional[dict[str, BucketLimit]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = limits if limits is not None else PEER_LIMITS
        self._clock = clock
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, kind: str, peer_key: str) -> Optional[dict]:
        """Spend one token for `peer_key`'s `kind` request. None when allowed,
        otherwise the error response to send back."""
        limit = self._limits.get(kind)
        if limit is None:
            return None
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get((kind, peer_key))
            if bucket is None:
                bucket = self._buckets[(kind, peer_key)] = _Bucket(float(limit.burst), now)
            bucket.tokens = min(float(limit.burst), bucket.tokens + (now - bucket.updated) * limit.per_second)
            bucket.updated = now
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return None
            retry_after = (1 - bucket.tokens) / limit.per_second
        return {
            "status": "error",
            "code": RATE_LIMITED_CODE,
            "detail": "too many requests; slow down",
            "retry_after": round(retry_after, 3),
        }
