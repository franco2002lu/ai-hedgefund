"""Simple rate limiter for data source API calls."""

import asyncio
import time
from collections import defaultdict


class RateLimiter:
    """
    Token bucket rate limiter — per source.
    Default: 5 requests per second per source.
    """

    def __init__(self, requests_per_second: float = 5.0):
        self._rate = requests_per_second
        self._tokens: dict[str, float] = defaultdict(lambda: requests_per_second)
        self._last_refill: dict[str, float] = defaultdict(time.monotonic)
        self._lock = asyncio.Lock()

    async def acquire(self, source: str) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill[source]
            self._tokens[source] = min(
                self._rate,
                self._tokens[source] + elapsed * self._rate,
            )
            self._last_refill[source] = now

            if self._tokens[source] < 1.0:
                wait_time = (1.0 - self._tokens[source]) / self._rate
                await asyncio.sleep(wait_time)
                self._tokens[source] = 0.0
            else:
                self._tokens[source] -= 1.0
