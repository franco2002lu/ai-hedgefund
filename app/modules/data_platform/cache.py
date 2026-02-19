"""In-memory TTL cache for data platform responses."""

from cachetools import TTLCache

from app.config import settings


class DataCache:
    """
    Simple cache with different TTLs per data type.
    Uses cachetools TTLCache — single-process only.
    """

    def __init__(self):
        self._prices = TTLCache(maxsize=500, ttl=settings.cache_prices_ttl)
        self._fundamentals = TTLCache(maxsize=200, ttl=settings.cache_fundamentals_ttl)
        self._news = TTLCache(maxsize=100, ttl=settings.cache_news_ttl)
        self._general = TTLCache(maxsize=500, ttl=300)

    def get(self, category: str, key: str):
        cache = self._get_cache(category)
        return cache.get(key)

    def set(self, category: str, key: str, value):
        cache = self._get_cache(category)
        cache[key] = value

    def _get_cache(self, category: str) -> TTLCache:
        if category == "prices":
            return self._prices
        elif category == "fundamentals":
            return self._fundamentals
        elif category == "news":
            return self._news
        return self._general
