"""Unit tests for DataPlatformService — adapter routing, caching, and fallback logic."""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.data_platform.service import DataPlatformService, DataUnavailableError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(name: str = "yahoo") -> MagicMock:
    adapter = MagicMock()
    adapter.name = name
    return adapter


def _make_cache(hit=None) -> MagicMock:
    cache = MagicMock()
    cache.get.return_value = hit
    return cache


def _make_rate_limiter() -> AsyncMock:
    rl = AsyncMock()
    rl.acquire = AsyncMock()
    return rl


def _bar(close: float = 150.0, volume: int = 1_000_000) -> MagicMock:
    bar = MagicMock()
    bar.model_dump.return_value = {"close": close, "volume": volume}
    return bar


# ---------------------------------------------------------------------------
# get_prices
# ---------------------------------------------------------------------------


class TestGetPrices:
    async def test_returns_cached_result(self):
        cached = {"symbol": "AAPL", "bars": [{"close": 150.0}], "source": "cache"}
        cache = _make_cache(hit=cached)
        svc = DataPlatformService(adapter_registry={}, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_prices("AAPL", date(2025, 1, 1), date(2025, 1, 10))

        assert result == cached
        cache.get.assert_called_once()

    async def test_calls_adapter_and_caches_on_miss(self):
        adapter = _make_adapter("yahoo")
        adapter.get_prices = AsyncMock(return_value=[_bar()])
        cache = _make_cache()
        rl = _make_rate_limiter()
        registry = {"prices": {"equity": [adapter]}}
        svc = DataPlatformService(adapter_registry=registry, cache=cache, rate_limiter=rl)

        result = await svc.get_prices("AAPL", date(2025, 1, 1), date(2025, 1, 10))

        assert result["symbol"] == "AAPL"
        assert result["source"] == "yahoo"
        assert len(result["bars"]) == 1
        cache.set.assert_called_once()
        rl.acquire.assert_awaited_once_with("yahoo")

    async def test_falls_back_to_all_adapters(self):
        adapter = _make_adapter("fallback")
        adapter.get_prices = AsyncMock(return_value=[_bar()])
        cache = _make_cache()
        registry = {"prices": {"all": [adapter]}}  # no "equity" key
        svc = DataPlatformService(adapter_registry=registry, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_prices("AAPL", date(2025, 1, 1), date(2025, 1, 10), asset_class="equity")

        assert result["source"] == "fallback"

    async def test_raises_when_no_adapters(self):
        cache = _make_cache()
        svc = DataPlatformService(adapter_registry={}, cache=cache, rate_limiter=_make_rate_limiter())

        with pytest.raises(DataUnavailableError, match="No adapter could serve prices"):
            await svc.get_prices("AAPL", date(2025, 1, 1), date(2025, 1, 10))

    async def test_skips_failing_adapter(self):
        bad_adapter = _make_adapter("bad")
        bad_adapter.get_prices = AsyncMock(side_effect=RuntimeError("API down"))
        good_adapter = _make_adapter("good")
        good_adapter.get_prices = AsyncMock(return_value=[_bar()])
        cache = _make_cache()
        registry = {"prices": {"equity": [bad_adapter, good_adapter]}}
        svc = DataPlatformService(adapter_registry=registry, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_prices("AAPL", date(2025, 1, 1), date(2025, 1, 10))

        assert result["source"] == "good"


# ---------------------------------------------------------------------------
# get_current_price
# ---------------------------------------------------------------------------


class TestGetCurrentPrice:
    async def test_returns_none_when_unavailable(self):
        cache = _make_cache()
        svc = DataPlatformService(adapter_registry={}, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_current_price("AAPL")

        assert result is None

    async def test_caches_result(self):
        adapter = _make_adapter("yahoo")
        adapter.get_current_price = AsyncMock(return_value=175.50)
        cache = _make_cache()
        registry = {"prices": {"equity": [adapter]}}
        svc = DataPlatformService(adapter_registry=registry, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_current_price("AAPL")

        assert result == 175.50
        cache.set.assert_called_once()

    async def test_returns_cached_price(self):
        cache = _make_cache(hit=175.50)
        svc = DataPlatformService(adapter_registry={}, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_current_price("AAPL")

        assert result == 175.50

    async def test_skips_adapter_without_method(self):
        adapter_no_method = _make_adapter("basic")
        del adapter_no_method.get_current_price  # hasattr will return False
        adapter_with_method = _make_adapter("yahoo")
        adapter_with_method.get_current_price = AsyncMock(return_value=180.0)
        cache = _make_cache()
        registry = {"prices": {"equity": [adapter_no_method, adapter_with_method]}}
        svc = DataPlatformService(adapter_registry=registry, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_current_price("AAPL")

        assert result == 180.0


# ---------------------------------------------------------------------------
# get_metrics
# ---------------------------------------------------------------------------


class TestGetMetrics:
    async def test_routes_to_fundamentals_adapters(self):
        adapter = _make_adapter("yahoo")
        adapter.get_metrics = AsyncMock(return_value={"pe_ratio": 25.0})
        cache = _make_cache()
        registry = {"fundamentals": {"equity": [adapter]}}
        svc = DataPlatformService(adapter_registry=registry, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_metrics("AAPL")

        assert result["symbol"] == "AAPL"
        assert result["metrics"] == {"pe_ratio": 25.0}
        assert result["source"] == "yahoo"

    async def test_raises_when_unavailable(self):
        cache = _make_cache()
        svc = DataPlatformService(adapter_registry={}, cache=cache, rate_limiter=_make_rate_limiter())

        with pytest.raises(DataUnavailableError, match="metrics"):
            await svc.get_metrics("AAPL")


# ---------------------------------------------------------------------------
# get_company_facts
# ---------------------------------------------------------------------------


class TestGetCompanyFacts:
    async def test_returns_cached(self):
        facts = {"name": "Apple Inc.", "sector": "Technology"}
        cache = _make_cache(hit=facts)
        svc = DataPlatformService(adapter_registry={}, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_company_facts("AAPL")

        assert result == facts

    async def test_calls_adapter(self):
        facts = {"name": "Apple Inc.", "sector": "Technology"}
        adapter = _make_adapter("yahoo")
        adapter.get_company_facts = AsyncMock(return_value=facts)
        cache = _make_cache()
        registry = {"fundamentals": {"equity": [adapter]}}
        svc = DataPlatformService(adapter_registry=registry, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_company_facts("AAPL")

        assert result == facts
        cache.set.assert_called_once()


# ---------------------------------------------------------------------------
# get_news
# ---------------------------------------------------------------------------


class TestGetNews:
    async def test_routes_to_news_adapters(self):
        article = MagicMock()
        article.model_dump.return_value = {"title": "AAPL up", "source": "reuters"}
        adapter = _make_adapter("news_api")
        adapter.get_news = AsyncMock(return_value=[article])
        cache = _make_cache()
        registry = {"news": {"all": [adapter]}}
        svc = DataPlatformService(adapter_registry=registry, cache=cache, rate_limiter=_make_rate_limiter())

        result = await svc.get_news(symbols=["AAPL"])

        assert len(result["articles"]) == 1
        assert result["source"] == "news_api"

    async def test_raises_when_unavailable(self):
        cache = _make_cache()
        svc = DataPlatformService(adapter_registry={}, cache=cache, rate_limiter=_make_rate_limiter())

        with pytest.raises(DataUnavailableError, match="news"):
            await svc.get_news()


# ---------------------------------------------------------------------------
# get_market_news / get_sector_news
# ---------------------------------------------------------------------------

from app.common.interfaces.news import NewsArticle  # noqa: E402
from app.modules.data_platform.noop import NoOpCache, NoOpRateLimiter  # noqa: E402


def _article(title="t"):
    return NewsArticle(
        title=title,
        source="Reuters",
        published_at=datetime(2026, 4, 14, 12, 0, 0),
        url="https://x",
    )


async def test_get_market_news_returns_first_successful_adapter():
    adapter = AsyncMock()
    adapter.name = "mock_news"
    adapter.get_market_news.return_value = [_article()]

    svc = DataPlatformService(
        adapter_registry={"news": {"all": [adapter]}},
        cache=NoOpCache(),
        rate_limiter=NoOpRateLimiter(),
    )

    result = await svc.get_market_news(since=date(2026, 4, 7), limit=10)

    assert result["articles"][0]["title"] == "t"
    assert result["source"] == "mock_news"
    adapter.get_market_news.assert_awaited_once_with(date(2026, 4, 7), 10)


async def test_get_sector_news_returns_sector_articles():
    adapter = AsyncMock()
    adapter.name = "mock_news"
    adapter.get_sector_news.return_value = [_article("tech")]

    svc = DataPlatformService(
        adapter_registry={"news": {"all": [adapter]}},
        cache=NoOpCache(),
        rate_limiter=NoOpRateLimiter(),
    )

    result = await svc.get_sector_news("Technology", since=date(2026, 4, 7))

    assert result["sector"] == "Technology"
    assert result["articles"][0]["title"] == "tech"
    adapter.get_sector_news.assert_awaited_once_with("Technology", date(2026, 4, 7), 20)


async def test_get_market_news_raises_when_no_adapter_succeeds():
    svc = DataPlatformService(
        adapter_registry={},
        cache=NoOpCache(),
        rate_limiter=NoOpRateLimiter(),
    )
    with pytest.raises(DataUnavailableError):
        await svc.get_market_news()


async def test_get_sector_news_raises_when_no_adapter_succeeds():
    svc = DataPlatformService(
        adapter_registry={},
        cache=NoOpCache(),
        rate_limiter=NoOpRateLimiter(),
    )
    with pytest.raises(DataUnavailableError):
        await svc.get_sector_news("Technology")


async def test_get_market_news_caches_result():
    adapter = AsyncMock()
    adapter.name = "mock_news"
    adapter.get_market_news.return_value = [_article()]
    cache_store: dict = {}

    class _StubCache:
        def get(self, cat, key):
            return cache_store.get((cat, key))

        def set(self, cat, key, value):
            cache_store[(cat, key)] = value

    svc = DataPlatformService(
        adapter_registry={"news": {"all": [adapter]}},
        cache=_StubCache(),
        rate_limiter=NoOpRateLimiter(),
    )

    await svc.get_market_news(since=date(2026, 4, 7), limit=10)
    await svc.get_market_news(since=date(2026, 4, 7), limit=10)

    # Second call should hit cache — adapter invoked only once.
    adapter.get_market_news.assert_awaited_once()


async def test_get_sector_news_falls_through_to_next_adapter_on_error():
    failing = AsyncMock()
    failing.name = "failing"
    failing.get_sector_news.side_effect = Exception("down")

    working = AsyncMock()
    working.name = "working"
    working.get_sector_news.return_value = [_article()]

    svc = DataPlatformService(
        adapter_registry={"news": {"all": [failing, working]}},
        cache=NoOpCache(),
        rate_limiter=NoOpRateLimiter(),
    )

    result = await svc.get_sector_news("Technology")

    assert result["source"] == "working"


async def test_get_market_news_falls_through_to_next_adapter_on_error():
    failing = AsyncMock()
    failing.name = "failing"
    failing.get_market_news.side_effect = Exception("down")

    working = AsyncMock()
    working.name = "working"
    working.get_market_news.return_value = [_article()]

    svc = DataPlatformService(
        adapter_registry={"news": {"all": [failing, working]}},
        cache=NoOpCache(),
        rate_limiter=NoOpRateLimiter(),
    )

    result = await svc.get_market_news()

    assert result["source"] == "working"
