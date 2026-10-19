"""Per-token rate limiting.

A token bucket per token: a steady rate, plus a burst for the bulk calls an
agent makes when a session starts. Caddy already caps the raw HTTP traffic;
this limit is about one agent, authenticated, looping on a tool.

The state is in-process: one instance is one company, and the server is a
single process. A distributed limiter would mean Redis, which is not worth the
dependency at this stage.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..errors import RateLimited

#: Above this many tracked tokens, idle buckets are pruned.
_PRUNE_THRESHOLD = 1024
#: A bucket untouched for this long carries no useful state.
_IDLE_SECONDS = 300.0


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Allows `requests_per_minute` per key, with a `burst` reserve."""

    def __init__(
        self,
        *,
        requests_per_minute: int,
        burst: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = requests_per_minute / 60.0
        self._capacity = float(burst if burst is not None else requests_per_minute)
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}

    @property
    def enabled(self) -> bool:
        return self._rate > 0 and self._capacity > 0

    def check(self, key: str) -> None:
        """Consumes one unit for `key`. Raises `RateLimited` when it is empty."""
        if not self.enabled:
            return

        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= _PRUNE_THRESHOLD:
                self._prune(now)
            self._buckets[key] = _Bucket(tokens=self._capacity - 1.0, updated=now)
            return

        elapsed = max(now - bucket.updated, 0.0)
        bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._rate)
        bucket.updated = now

        if bucket.tokens < 1.0:
            retry_after = max((1.0 - bucket.tokens) / self._rate, 0.1)
            raise RateLimited(
                f"rate limit reached for this token: retry in {retry_after:.1f}s"
            )
        bucket.tokens -= 1.0

    def _prune(self, now: float) -> None:
        for key, bucket in list(self._buckets.items()):
            if now - bucket.updated > _IDLE_SECONDS:
                del self._buckets[key]
