"""Serving-side per-peer rate limits, and the session client backing off when a
peer says to."""
import asyncio

import pytest

from yaffo.p2p import puller as puller_module
from yaffo.p2p.rate_limits import RATE_LIMITED_CODE, BucketLimit, PeerRateLimiter
from yaffo.p2p.puller import SessionHolder

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def limiter(clock):
    return PeerRateLimiter({"pull_file": BucketLimit(per_second=2, burst=3)}, clock=clock)


def test_burst_then_refusal_with_retry_after(limiter):
    assert [limiter.check("pull_file", "PEER") for _ in range(3)] == [None, None, None]

    refused = limiter.check("pull_file", "PEER")

    assert refused["status"] == "error"
    assert refused["code"] == RATE_LIMITED_CODE
    assert refused["retry_after"] == pytest.approx(0.5)


def test_tokens_refill_with_time(limiter, clock):
    for _ in range(3):
        limiter.check("pull_file", "PEER")
    clock.now += 0.5
    assert limiter.check("pull_file", "PEER") is None
    assert limiter.check("pull_file", "PEER") is not None


def test_refill_never_exceeds_the_burst(limiter, clock):
    clock.now += 3600
    assert [limiter.check("pull_file", "PEER") for _ in range(4)][-1] is not None


def test_each_peer_has_its_own_bucket(limiter):
    for _ in range(3):
        limiter.check("pull_file", "PEER-A")
    assert limiter.check("pull_file", "PEER-A") is not None
    assert limiter.check("pull_file", "PEER-B") is None


def test_unlisted_request_types_are_not_limited(limiter):
    assert all(limiter.check("ping", "PEER") is None for _ in range(100))


class RateLimitingSession:
    """Answers rate_limited a set number of times, then ok."""

    path = "local"
    closed = False

    def __init__(self, refusals: int) -> None:
        self.refusals = refusals
        self.calls = 0

    async def request(self, payload, timeout):
        self.calls += 1
        if self.calls <= self.refusals:
            return {"status": "error", "code": RATE_LIMITED_CODE, "retry_after": 0.01}
        return {"status": "ok"}

    def close(self):
        pass


def _holder_for(session):
    async def opener():
        return session
    return SessionHolder(opener)


def test_session_waits_and_retries_when_rate_limited():
    session = RateLimitingSession(refusals=2)

    response = asyncio.run(_holder_for(session).request({"type": "pull_file"}))

    assert response == {"status": "ok"}
    assert session.calls == 3


def test_session_gives_up_after_repeated_rate_limits(monkeypatch):
    monkeypatch.setattr(puller_module, "MAX_RATE_LIMITED_RETRIES", 3)
    session = RateLimitingSession(refusals=10)

    response = asyncio.run(_holder_for(session).request({"type": "pull_file"}))

    assert response["code"] == RATE_LIMITED_CODE
    assert session.calls == 3
