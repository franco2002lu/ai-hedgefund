"""Tests for NoOpCache and NoOpRateLimiter (Null Object pattern)."""

import time

from app.modules.data_platform.noop import NoOpCache, NoOpRateLimiter


class TestNoOpCache:
    def test_get_returns_none(self):
        cache = NoOpCache()
        assert cache.get("prices", "AAPL") is None

    def test_set_does_not_raise(self):
        cache = NoOpCache()
        cache.set("prices", "AAPL", {"data": [1, 2, 3]})

    def test_set_then_get_still_returns_none(self):
        """NoOp cache intentionally drops all writes."""
        cache = NoOpCache()
        cache.set("prices", "AAPL", "value")
        assert cache.get("prices", "AAPL") is None

    def test_different_categories_all_return_none(self):
        cache = NoOpCache()
        cache.set("fundamentals", "MSFT", {"revenue": 100})
        assert cache.get("fundamentals", "MSFT") is None
        assert cache.get("news", "MSFT") is None


class TestNoOpRateLimiter:
    async def test_acquire_does_not_block(self):
        limiter = NoOpRateLimiter()
        await limiter.acquire("yahoo_finance")

    async def test_hundred_acquires_under_100ms(self):
        """NoOp rate limiter should never throttle."""
        limiter = NoOpRateLimiter()
        start = time.monotonic()
        for _ in range(100):
            await limiter.acquire("yahoo_finance")
        elapsed_ms = (time.monotonic() - start) * 1000
        assert elapsed_ms < 100, f"100 acquires took {elapsed_ms:.1f}ms (expected < 100ms)"

    async def test_acquire_returns_none(self):
        limiter = NoOpRateLimiter()
        result = await limiter.acquire("yahoo_finance")
        assert result is None
