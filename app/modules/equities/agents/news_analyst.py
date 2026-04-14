from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.modules.equities.agents.context_formatters import format_news_context
from app.modules.equities.agents.skills.loader import compose_system_prompt
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import StockSignal, UniverseStock

logger = logging.getLogger(__name__)


class NewsAnalyst:
    """Assesses recent news sentiment, catalysts, and risks.

    Articles are pre-fetched by the prefetch_news graph node and passed in
    via `analyze(stock, articles)`. The analyst does not fetch its own data.
    """

    ANALYST_TYPE = "news"

    def __init__(
        self,
        config: AnalystLLMConfig,
        llm_client=None,
        branch_name: str = "",
        skills_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.llm_client = llm_client
        self.branch_name = branch_name
        self.skills_dir = skills_dir

    async def analyze(
        self,
        stock: UniverseStock,
        articles: list[dict],
    ) -> StockSignal:
        context = format_news_context(
            symbol=stock.symbol,
            company_name=stock.company_name,
            sector=stock.sector,
            articles=(articles or [])[:20],
        )
        system_prompt = compose_system_prompt(
            self.ANALYST_TYPE,
            self.branch_name,
            stock.sector,
            self.skills_dir,
        )
        prompt = f"{context}\n\nAnalyze this stock based on the data above and your instructions."
        result = await self.llm_client.invoke(prompt, system_prompt=system_prompt)
        if isinstance(result, StockSignal):
            return result
        return StockSignal(
            symbol=stock.symbol,
            analyst_type=self.ANALYST_TYPE,
            bullish_score=result.get("bullish_score", 5),
            confidence=result.get("confidence", 5),
            summary=result.get("summary", "No analysis available."),
        )

    async def analyze_batch(
        self,
        stocks: list[UniverseStock],
        articles_by_symbol: dict[str, list[dict]] | None = None,
        max_concurrent: int = 10,
        **kwargs,
    ) -> list[StockSignal]:
        if not stocks:
            return []
        articles_by_symbol = articles_by_symbol or {}
        sem = asyncio.Semaphore(max_concurrent)

        async def _limited(s: UniverseStock) -> StockSignal:
            async with sem:
                try:
                    return await self.analyze(s, articles=articles_by_symbol.get(s.symbol, []))
                except Exception:
                    logger.warning(
                        "%s: analyze failed for %s",
                        self.__class__.__name__,
                        s.symbol,
                        exc_info=True,
                    )
                    return StockSignal(
                        symbol=s.symbol,
                        analyst_type=self.ANALYST_TYPE,
                        bullish_score=5,
                        confidence=1,
                        summary="Analysis failed — neutral fallback signal.",
                    )

        return list(await asyncio.gather(*(_limited(s) for s in stocks)))
