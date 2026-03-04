"""CachedAnalystWrapper — caching + rate-cap shim for LLM analysts in backtests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date

from app.modules.equities.models import StockSignal, UniverseStock

logger = logging.getLogger(__name__)

_NEUTRAL_FALLBACK_SCORE = 5
_NEUTRAL_FALLBACK_CONFIDENCE = 1


class CachedAnalystWrapper:
    """Wraps an LLM analyst to add per-backtest signal caching and a per-rebalance call cap.

    Cache key: (date, symbol, analyst_type).
    The cache and llm_counter are owned by BacktestContext and shared across all
    three wrapper instances so the cap is applied globally across all analysts.
    """

    def __init__(
        self,
        analyst: object,
        analyst_type: str,
        cache: dict[tuple[date, str, str], StockSignal],
        llm_counter: list[int],
        max_calls_per_rebalance: int,
        cache_enabled: bool,
        current_date_fn: Callable[[], date],
    ) -> None:
        self._analyst = analyst
        self._analyst_type = analyst_type
        self._cache = cache
        self._llm_counter = llm_counter
        self._max_calls = max_calls_per_rebalance
        self._cache_enabled = cache_enabled
        self._current_date_fn = current_date_fn

    def reset_rebalance_counter(self) -> None:
        self._llm_counter[0] = 0

    async def analyze(self, stock: UniverseStock) -> StockSignal:
        today = self._current_date_fn()
        cache_key = (today, stock.symbol, self._analyst_type)

        if self._cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]

        if self._llm_counter[0] >= self._max_calls:
            logger.warning(
                "LLM call cap reached (%d/%d) — neutral fallback for %s (%s)",
                self._llm_counter[0],
                self._max_calls,
                stock.symbol,
                self._analyst_type,
            )
            return StockSignal(
                symbol=stock.symbol,
                analyst_type=self._analyst_type,
                bullish_score=_NEUTRAL_FALLBACK_SCORE,
                confidence=_NEUTRAL_FALLBACK_CONFIDENCE,
                summary=f"LLM cap reached ({self._max_calls} calls/rebalance).",
            )

        self._llm_counter[0] += 1
        signal = await self._analyst.analyze(stock)

        if self._cache_enabled:
            self._cache[cache_key] = signal

        return signal

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
                        "CachedAnalystWrapper: analyze failed for %s (%s)",
                        s.symbol,
                        self._analyst_type,
                        exc_info=True,
                    )
                    return StockSignal(
                        symbol=s.symbol,
                        analyst_type=self._analyst_type,
                        bullish_score=_NEUTRAL_FALLBACK_SCORE,
                        confidence=_NEUTRAL_FALLBACK_CONFIDENCE,
                        summary="Analysis failed — neutral fallback signal.",
                    )

        return list(await asyncio.gather(*(_limited(s) for s in stocks)))
