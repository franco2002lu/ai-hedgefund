from __future__ import annotations

import asyncio

from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import StockSignal, UniverseStock


class FundamentalsAnalyst:
    """Analyzes financial health, earnings quality, and valuation."""

    def __init__(
        self,
        config: AnalystLLMConfig,
        data_service=None,
        sec_edgar=None,
        llm_client=None,
    ) -> None:
        self.config = config
        self.data_service = data_service
        self.sec_edgar = sec_edgar
        self.llm_client = llm_client

    async def analyze(self, stock: UniverseStock) -> StockSignal:
        metrics_data = await self.data_service.get_metrics(stock.symbol)
        metrics = metrics_data.get("metrics", [])
        earnings = []
        if self.sec_edgar:
            try:
                earnings = await self.sec_edgar.get_earnings_data(stock.symbol)
            except (NotImplementedError, Exception):
                earnings = []
        metrics_str = str(metrics[0]) if metrics else "No metrics available."
        earnings_str = (
            "\n".join(f"- {e.fiscal_quarter}: EPS={e.eps}, Revenue={e.revenue}" for e in earnings[:4])
            or "No earnings data available."
        )
        prompt = (
            f"Analyze fundamentals for {stock.company_name} ({stock.symbol}).\n"
            f"Sector: {stock.sector or 'Unknown'}\n\n"
            f"Key metrics:\n{metrics_str}\n\n"
            f"Recent earnings:\n{earnings_str}\n\n"
            "Provide: bullish_score (1-10), confidence (1-10), summary (1-2 sentences)."
        )
        result = await self.llm_client.invoke(prompt)
        if isinstance(result, StockSignal):
            return result
        return StockSignal(
            symbol=stock.symbol,
            analyst_type="fundamentals",
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
                return await self.analyze(s)

        return list(await asyncio.gather(*(_limited(s) for s in stocks)))
