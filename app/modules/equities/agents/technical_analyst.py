from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.common.interfaces.time import TimeProvider
from app.modules.backtest.time_provider import LiveTimeProvider
from app.modules.equities.agents.context_formatters import format_technical_context
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import StockSignal, UniverseStock

logger = logging.getLogger(__name__)


class TechnicalAnalyst:
    """Identifies price trends, momentum, and technical patterns."""

    ANALYST_TYPE = "technical"

    def __init__(
        self,
        config: AnalystLLMConfig,
        data_service=None,
        llm_client=None,
        time_provider: TimeProvider | None = None,
    ) -> None:
        self.config = config
        self.data_service = data_service
        self.llm_client = llm_client
        self.time_provider = time_provider or LiveTimeProvider()

    async def analyze(self, stock: UniverseStock) -> StockSignal:
        end_date = self.time_provider.today()
        start_date = end_date - timedelta(days=365)
        price_data = await self.data_service.get_prices(
            stock.symbol,
            start_date,
            end_date,
        )
        bars = price_data.get("bars", [])
        context = format_technical_context(
            symbol=stock.symbol,
            company_name=stock.company_name,
            bars=bars,
            as_of_date=end_date,
        )
        prompt = (
            f"{context}\n\n"
            "Based on the data above, provide: bullish_score (1-10), confidence (1-10), "
            "summary (1-2 sentences)."
        )
        result = await self.llm_client.invoke(prompt)
        if isinstance(result, StockSignal):
            return result
        return StockSignal(
            symbol=stock.symbol,
            analyst_type="technical",
            bullish_score=result.get("bullish_score", 5),
            confidence=result.get("confidence", 5),
            summary=result.get("summary", "No analysis available."),
        )

    async def analyze_batch(
        self,
        stocks: list[UniverseStock],
        max_concurrent: int = 10,
    ) -> list[StockSignal]:
        if not stocks:
            return []
        sem = asyncio.Semaphore(max_concurrent)

        async def _limited(s: UniverseStock) -> StockSignal:
            async with sem:
                try:
                    return await self.analyze(s)
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
