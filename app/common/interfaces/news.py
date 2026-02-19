from abc import ABC, abstractmethod
from datetime import date, datetime

from pydantic import BaseModel


class NewsArticle(BaseModel):
    title: str
    author: str | None = None
    source: str
    published_at: datetime
    url: str
    symbols: list[str] = []
    sentiment: str | None = None


class NewsAdapter(ABC):
    @abstractmethod
    async def get_news(
        self,
        symbols: list[str] | None = None,
        query: str | None = None,
        since: date | None = None,
        limit: int = 100,
    ) -> list[NewsArticle]:
        ...
