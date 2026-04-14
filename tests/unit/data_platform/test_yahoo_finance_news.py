"""Unit tests for YahooFinanceAdapter's news methods — uses monkeypatched yfinance."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.data_platform.adapters.yahoo_finance import YahooFinanceAdapter


def _yf_news_item(title, provider="Reuters", pub_date="2026-04-14T12:00:00Z"):
    return {
        "content": {
            "title": title,
            "provider": {"displayName": provider},
            "pubDate": pub_date,
            "canonicalUrl": {"url": "https://example.com/article"},
        }
    }


@pytest.fixture
def mock_ticker():
    with patch("app.modules.data_platform.adapters.yahoo_finance.yf.Ticker") as m:
        yield m


async def test_get_news_puts_provider_in_source_field(mock_ticker):
    mock_ticker.return_value = MagicMock(news=[_yf_news_item("Apple beats earnings", provider="Reuters")])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_news(symbols=["AAPL"], limit=10)

    assert len(articles) == 1
    assert articles[0].source == "Reuters"  # NOT "yahoo_finance"
    assert articles[0].author is None


async def test_get_news_falls_back_when_provider_missing(mock_ticker):
    item = {
        "content": {
            "title": "No provider",
            "pubDate": "2026-04-14T12:00:00Z",
            "canonicalUrl": {"url": "https://example.com/a"},
        }
    }
    mock_ticker.return_value = MagicMock(news=[item])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_news(symbols=["AAPL"], limit=10)

    assert articles[0].source == "Yahoo Finance"


async def test_get_market_news_queries_spy(mock_ticker):
    mock_ticker.return_value = MagicMock(news=[_yf_news_item("Markets rally")])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_market_news(limit=5)

    mock_ticker.assert_called_with("SPY")
    assert len(articles) == 1


async def test_get_sector_news_queries_sector_etf(mock_ticker):
    mock_ticker.return_value = MagicMock(news=[_yf_news_item("Tech leads")])
    adapter = YahooFinanceAdapter()

    await adapter.get_sector_news("Technology")

    mock_ticker.assert_called_with("XLK")


async def test_get_sector_news_unknown_sector_returns_empty(mock_ticker):
    mock_ticker.return_value = MagicMock(news=[])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_sector_news("FakeSector")

    assert articles == []


async def test_get_market_news_respects_since(mock_ticker):
    old = _yf_news_item("Old", pub_date="2026-01-01T00:00:00Z")
    new = _yf_news_item("New", pub_date="2026-04-14T00:00:00Z")
    mock_ticker.return_value = MagicMock(news=[old, new])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_market_news(since=date(2026, 4, 1))

    titles = [a.title for a in articles]
    assert "Old" not in titles and "New" in titles


async def test_yahoo_finance_adapter_can_be_instantiated():
    """Sanity check — until Task 3, instantiation fails because abstract methods are missing."""
    adapter = YahooFinanceAdapter()
    assert adapter is not None


async def test_get_news_survives_null_provider(mock_ticker):
    """yfinance sometimes returns {"provider": null}; must not crash."""
    item = {
        "content": {
            "title": "Wire story",
            "provider": None,
            "pubDate": "2026-04-14T12:00:00Z",
            "canonicalUrl": {"url": "https://example.com/a"},
        }
    }
    mock_ticker.return_value = MagicMock(news=[item])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_news(symbols=["AAPL"], limit=10)

    assert len(articles) == 1
    assert articles[0].source == "Yahoo Finance"
