from abc import ABC, abstractmethod
from datetime import date, datetime

from pydantic import BaseModel


class NewsArticle(BaseModel):
    title: str
    author: str | None = None
    source: str
    published_at: datetime
    url: str = ""
    symbols: list[str] = []
    sentiment: str | None = None
    scope: str | None = None  # "market" or a sector name — used by manual articles


class NewsAdapter(ABC):
    """Abstract news adapter.

    New adapters must implement `get_market_news` and `get_sector_news`.
    `get_news` (symbol-scoped) is retained as a non-abstract method for
    backward compatibility with YahooFinanceAdapter and DataPlatformService.get_news().
    """

    @abstractmethod
    async def get_market_news(
        self,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]:
        """Return broad-market news articles since `since` (inclusive)."""

    @abstractmethod
    async def get_sector_news(
        self,
        sector: str,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]:
        """Return sector-scoped news articles since `since` (inclusive)."""

    async def get_news(
        self,
        symbols: list[str] | None = None,
        query: str | None = None,
        since: date | None = None,
        limit: int = 100,
    ) -> list[NewsArticle]:
        """Legacy symbol-scoped fetch. Default returns []; subclasses may override."""
        return []
