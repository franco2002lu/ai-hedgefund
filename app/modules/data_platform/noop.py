class NoOpCache:
    """Cache that always misses. Implements the same interface as DataCache."""

    def get(self, category: str, key: str) -> None:
        return None

    def set(self, category: str, key: str, value) -> None:
        pass

    def delete(self, category: str, key: str) -> None:
        pass


class NoOpRateLimiter:
    """Rate limiter that always permits. Implements the same interface as RateLimiter."""

    async def acquire(self, adapter_name: str) -> None:
        pass
