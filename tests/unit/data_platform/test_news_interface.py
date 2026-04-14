"""Contract tests for the NewsAdapter interface."""

from datetime import datetime

from app.common.interfaces.news import NewsAdapter, NewsArticle


def test_news_adapter_requires_get_market_news():
    class Incomplete(NewsAdapter):
        async def get_sector_news(self, sector, since=None, limit=20):
            return []

    try:
        Incomplete()
    except TypeError as exc:
        assert "get_market_news" in str(exc)
    else:
        raise AssertionError("expected TypeError for missing get_market_news")


def test_news_adapter_requires_get_sector_news():
    class Incomplete(NewsAdapter):
        async def get_market_news(self, since=None, limit=20):
            return []

    try:
        Incomplete()
    except TypeError as exc:
        assert "get_sector_news" in str(exc)
    else:
        raise AssertionError("expected TypeError for missing get_sector_news")


async def test_concrete_subclass_satisfies_interface():
    class Concrete(NewsAdapter):
        async def get_market_news(self, since=None, limit=20):
            return [NewsArticle(title="m", source="s", published_at=datetime.now(), url="u")]

        async def get_sector_news(self, sector, since=None, limit=20):
            return [NewsArticle(title="s", source="s", published_at=datetime.now(), url="u")]

    a = Concrete()
    m = await a.get_market_news()
    s = await a.get_sector_news("Technology")
    assert len(m) == 1 and len(s) == 1
