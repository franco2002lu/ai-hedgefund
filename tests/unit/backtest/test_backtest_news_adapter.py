"""Unit tests for the BacktestNewsAdapter placeholder."""

from datetime import date

from app.common.interfaces.news import NewsAdapter
from app.modules.backtest.adapters.backtest_news import BacktestNewsAdapter


def test_is_a_news_adapter():
    assert issubclass(BacktestNewsAdapter, NewsAdapter)


async def test_get_market_news_returns_empty():
    adapter = BacktestNewsAdapter()
    assert await adapter.get_market_news() == []
    assert await adapter.get_market_news(since=date(2026, 1, 1), limit=5) == []


async def test_get_sector_news_returns_empty():
    adapter = BacktestNewsAdapter()
    assert await adapter.get_sector_news("Technology") == []
    assert await adapter.get_sector_news("Healthcare", since=date(2026, 1, 1), limit=5) == []


def test_has_name_attribute():
    adapter = BacktestNewsAdapter()
    assert adapter.name == "backtest_news"
