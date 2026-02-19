"""Unit tests for DataCache."""

import pytest

from app.modules.data_platform.cache import DataCache


@pytest.fixture
def cache():
    return DataCache()


class TestDataCache:
    def test_set_and_get(self, cache):
        cache.set("prices", "AAPL:1d", [1, 2, 3])
        assert cache.get("prices", "AAPL:1d") == [1, 2, 3]

    def test_cache_miss_returns_none(self, cache):
        assert cache.get("prices", "NONEXISTENT") is None

    def test_categories_are_isolated(self, cache):
        cache.set("prices", "key1", "price_data")
        cache.set("fundamentals", "key1", "fundamental_data")
        cache.set("news", "key1", "news_data")

        assert cache.get("prices", "key1") == "price_data"
        assert cache.get("fundamentals", "key1") == "fundamental_data"
        assert cache.get("news", "key1") == "news_data"

    def test_unknown_category_uses_general(self, cache):
        cache.set("custom", "key1", "value1")
        assert cache.get("custom", "key1") == "value1"

        # A different unknown category shares the same general cache
        cache.set("other", "key1", "value2")
        # They share the same store so "key1" was overwritten
        assert cache.get("other", "key1") == "value2"

    def test_overwrite_existing_key(self, cache):
        cache.set("prices", "AAPL", "old")
        cache.set("prices", "AAPL", "new")
        assert cache.get("prices", "AAPL") == "new"
