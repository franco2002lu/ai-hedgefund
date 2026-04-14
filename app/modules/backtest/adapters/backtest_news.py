"""BacktestNewsAdapter — empty placeholder until a historical news provider is plugged in.

Returns empty lists for both methods. Manual articles are loaded separately by
the prefetch_news graph node, so the news analyst still receives content when
curated articles exist in data/news/manual/.
"""

from __future__ import annotations

from datetime import date

from app.common.interfaces.news import NewsAdapter, NewsArticle


class BacktestNewsAdapter(NewsAdapter):
    name = "backtest_news"

    async def get_market_news(
        self,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]:
        return []

    async def get_sector_news(
        self,
        sector: str,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]:
        return []
