from __future__ import annotations

import pytest

from loremd.auth.ratelimit import RateLimiter
from loremd.errors import RateLimited


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_burst_is_allowed_then_refused() -> None:
    clock = FakeClock()
    limiter = RateLimiter(requests_per_minute=60, burst=3, clock=clock)

    for _ in range(3):
        limiter.check("abcd1234")

    with pytest.raises(RateLimited):
        limiter.check("abcd1234")


def test_bucket_refills_over_time() -> None:
    clock = FakeClock()
    limiter = RateLimiter(requests_per_minute=60, burst=2, clock=clock)
    limiter.check("abcd1234")
    limiter.check("abcd1234")

    clock.advance(1.0)  # 60/min = one unit per second

    limiter.check("abcd1234")


def test_tokens_do_not_share_a_bucket() -> None:
    limiter = RateLimiter(requests_per_minute=60, burst=1, clock=FakeClock())
    limiter.check("first")

    limiter.check("second")

    with pytest.raises(RateLimited):
        limiter.check("first")


def test_message_tells_the_agent_when_to_retry() -> None:
    limiter = RateLimiter(requests_per_minute=60, burst=1, clock=FakeClock())
    limiter.check("abcd1234")

    with pytest.raises(RateLimited, match="retry in"):
        limiter.check("abcd1234")


def test_zero_disables_the_limit() -> None:
    limiter = RateLimiter(requests_per_minute=0, burst=0, clock=FakeClock())

    for _ in range(1000):
        limiter.check("abcd1234")

    assert not limiter.enabled
