"""Unit tests for RateLimiter."""

import time

from app.modules.data_platform.rate_limiter import RateLimiter


class TestRateLimiter:
    async def test_first_request_passes_immediately(self):
        limiter = RateLimiter(requests_per_second=5.0)

        start = time.monotonic()
        await limiter.acquire("test_source")
        elapsed = time.monotonic() - start

        # Should complete with negligible delay
        assert elapsed < 0.1

    async def test_sources_are_independent(self):
        limiter = RateLimiter(requests_per_second=2.0)

        # Exhaust tokens for source A
        await limiter.acquire("source_a")
        await limiter.acquire("source_a")

        # Source B should still be full
        start = time.monotonic()
        await limiter.acquire("source_b")
        elapsed = time.monotonic() - start

        assert elapsed < 0.1

    async def test_burst_within_capacity(self):
        limiter = RateLimiter(requests_per_second=10.0)

        start = time.monotonic()
        # 10 requests should fit within the bucket
        for _ in range(5):
            await limiter.acquire("burst")
        elapsed = time.monotonic() - start

        # Should be fast — well within burst capacity
        assert elapsed < 1.0
