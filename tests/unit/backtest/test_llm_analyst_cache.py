"""Tests for CachedAnalystWrapper — LLM signal caching + rate cap."""

from datetime import date
from unittest.mock import AsyncMock

from app.modules.backtest.llm_analyst_cache import CachedAnalystWrapper
from app.modules.equities.models import StockSignal, UniverseStock


def _make_stock(symbol: str = "AAPL") -> UniverseStock:
    return UniverseStock(symbol=symbol, company_name=f"{symbol} Inc.")


def _make_signal(
    symbol: str = "AAPL", analyst_type: str = "news", score: int = 7
) -> StockSignal:
    return StockSignal(
        symbol=symbol,
        analyst_type=analyst_type,
        bullish_score=score,
        confidence=8,
        summary="test signal",
    )


def _make_wrapper(
    analyst=None,
    analyst_type: str = "news",
    cache=None,
    counter=None,
    max_calls: int = 60,
    cache_enabled: bool = True,
    today: date = date(2024, 1, 15),
) -> CachedAnalystWrapper:
    if analyst is None:
        analyst = AsyncMock()
        analyst.analyze = AsyncMock(
            return_value=_make_signal(analyst_type=analyst_type)
        )
    return CachedAnalystWrapper(
        analyst=analyst,
        analyst_type=analyst_type,
        cache=cache if cache is not None else {},
        llm_counter=counter if counter is not None else [0],
        max_calls_per_rebalance=max_calls,
        cache_enabled=cache_enabled,
        current_date_fn=lambda: today,
    )


# ---------------------------------------------------------------------------
# Cache hit / miss
# ---------------------------------------------------------------------------


class TestCacheHit:
    async def test_cache_hit_returns_cached_signal_without_calling_analyst(self):
        today = date(2024, 1, 15)
        cached_signal = _make_signal(score=9)
        cache = {(today, "AAPL", "news"): cached_signal}
        mock_analyst = AsyncMock()

        wrapper = _make_wrapper(analyst=mock_analyst, cache=cache, today=today)
        result = await wrapper.analyze(_make_stock("AAPL"))

        assert result is cached_signal
        mock_analyst.analyze.assert_not_called()

    async def test_cache_hit_does_not_increment_counter(self):
        today = date(2024, 1, 15)
        cached_signal = _make_signal()
        cache = {(today, "AAPL", "news"): cached_signal}
        counter = [0]

        wrapper = _make_wrapper(cache=cache, counter=counter, today=today)
        await wrapper.analyze(_make_stock("AAPL"))

        assert counter[0] == 0

    async def test_cache_miss_calls_analyst_and_populates_cache(self):
        today = date(2024, 1, 15)
        cache: dict = {}
        expected_signal = _make_signal(score=6)
        mock_analyst = AsyncMock()
        mock_analyst.analyze = AsyncMock(return_value=expected_signal)

        wrapper = _make_wrapper(analyst=mock_analyst, cache=cache, today=today)
        result = await wrapper.analyze(_make_stock("AAPL"))

        assert result is expected_signal
        assert (today, "AAPL", "news") in cache
        mock_analyst.analyze.assert_called_once()


# ---------------------------------------------------------------------------
# Safety cap
# ---------------------------------------------------------------------------


class TestSafetyCap:
    async def test_cap_returns_neutral_fallback_when_counter_at_limit(self):
        mock_analyst = AsyncMock()
        counter = [60]

        wrapper = _make_wrapper(
            analyst=mock_analyst,
            counter=counter,
            max_calls=60,
            cache_enabled=False,
        )
        result = await wrapper.analyze(_make_stock("AAPL"))

        assert result.bullish_score == 5
        assert result.confidence == 1
        mock_analyst.analyze.assert_not_called()

    async def test_cap_not_exceeded_before_limit(self):
        expected = _make_signal(score=8)
        mock_analyst = AsyncMock()
        mock_analyst.analyze = AsyncMock(return_value=expected)
        counter = [59]

        wrapper = _make_wrapper(
            analyst=mock_analyst,
            counter=counter,
            max_calls=60,
            cache_enabled=False,
        )
        result = await wrapper.analyze(_make_stock("AAPL"))

        assert result is expected
        assert counter[0] == 60

    async def test_counter_increments_on_each_actual_llm_call(self):
        counter = [0]
        mock_analyst = AsyncMock()
        mock_analyst.analyze = AsyncMock(return_value=_make_signal())

        wrapper = _make_wrapper(
            analyst=mock_analyst, counter=counter, cache_enabled=False
        )

        await wrapper.analyze(_make_stock("AAPL"))
        await wrapper.analyze(_make_stock("MSFT"))
        await wrapper.analyze(_make_stock("GOOG"))

        assert counter[0] == 3


# ---------------------------------------------------------------------------
# Counter reset
# ---------------------------------------------------------------------------


class TestCounterReset:
    async def test_reset_rebalance_counter_zeroes_counter(self):
        counter = [45]
        wrapper = _make_wrapper(counter=counter)
        wrapper.reset_rebalance_counter()
        assert counter[0] == 0

    async def test_reset_allows_calls_after_previous_cap_hit(self):
        counter = [60]
        expected = _make_signal(score=7)
        mock_analyst = AsyncMock()
        mock_analyst.analyze = AsyncMock(return_value=expected)

        wrapper = _make_wrapper(
            analyst=mock_analyst,
            counter=counter,
            max_calls=60,
            cache_enabled=False,
        )

        # Blocked before reset
        blocked = await wrapper.analyze(_make_stock("AAPL"))
        assert blocked.confidence == 1

        # Reset and try again
        wrapper.reset_rebalance_counter()
        result = await wrapper.analyze(_make_stock("AAPL"))
        assert result is expected

    async def test_shared_counter_across_multiple_wrappers(self):
        shared_counter = [0]
        shared_cache: dict = {}

        news_mock = AsyncMock()
        news_mock.analyze = AsyncMock(
            return_value=_make_signal(analyst_type="news")
        )
        fund_mock = AsyncMock()
        fund_mock.analyze = AsyncMock(
            return_value=_make_signal(analyst_type="fundamentals")
        )

        news_wrapper = CachedAnalystWrapper(
            analyst=news_mock,
            analyst_type="news",
            cache=shared_cache,
            llm_counter=shared_counter,
            max_calls_per_rebalance=5,
            cache_enabled=False,
            current_date_fn=lambda: date(2024, 1, 15),
        )
        fund_wrapper = CachedAnalystWrapper(
            analyst=fund_mock,
            analyst_type="fundamentals",
            cache=shared_cache,
            llm_counter=shared_counter,
            max_calls_per_rebalance=5,
            cache_enabled=False,
            current_date_fn=lambda: date(2024, 1, 15),
        )

        await news_wrapper.analyze(_make_stock("AAPL"))  # counter -> 1
        await fund_wrapper.analyze(_make_stock("AAPL"))  # counter -> 2
        await news_wrapper.analyze(_make_stock("MSFT"))  # counter -> 3

        assert shared_counter[0] == 3


# ---------------------------------------------------------------------------
# Cache disabled
# ---------------------------------------------------------------------------


class TestCacheDisabled:
    async def test_disabled_cache_always_calls_analyst(self):
        mock_analyst = AsyncMock()
        mock_analyst.analyze = AsyncMock(return_value=_make_signal())

        wrapper = _make_wrapper(analyst=mock_analyst, cache_enabled=False)

        await wrapper.analyze(_make_stock("AAPL"))
        await wrapper.analyze(_make_stock("AAPL"))

        assert mock_analyst.analyze.call_count == 2

    async def test_disabled_cache_does_not_populate_cache_dict(self):
        cache: dict = {}
        mock_analyst = AsyncMock()
        mock_analyst.analyze = AsyncMock(return_value=_make_signal())

        wrapper = _make_wrapper(analyst=mock_analyst, cache=cache, cache_enabled=False)
        await wrapper.analyze(_make_stock("AAPL"))

        assert len(cache) == 0


# ---------------------------------------------------------------------------
# analyze_batch
# ---------------------------------------------------------------------------


class TestAnalyzeBatch:
    async def test_analyze_batch_returns_signal_per_stock(self):
        def make_sig(s):
            return StockSignal(
                symbol=s.symbol,
                analyst_type="news",
                bullish_score=5,
                confidence=5,
                summary="ok",
            )

        mock_analyst = AsyncMock()
        mock_analyst.analyze = AsyncMock(side_effect=make_sig)

        wrapper = _make_wrapper(analyst=mock_analyst, cache_enabled=False)
        stocks = [_make_stock("AAPL"), _make_stock("MSFT"), _make_stock("GOOG")]
        results = await wrapper.analyze_batch(stocks)

        assert len(results) == 3
        assert {r.symbol for r in results} == {"AAPL", "MSFT", "GOOG"}

    async def test_analyze_batch_routes_through_cache(self):
        today = date(2024, 1, 15)
        cached = _make_signal(symbol="AAPL", score=9)
        cache = {(today, "AAPL", "news"): cached}
        mock_analyst = AsyncMock()
        mock_analyst.analyze = AsyncMock(return_value=_make_signal(score=5))

        wrapper = _make_wrapper(analyst=mock_analyst, cache=cache, today=today)
        results = await wrapper.analyze_batch(
            [_make_stock("AAPL"), _make_stock("MSFT")]
        )

        aapl = next(r for r in results if r.symbol == "AAPL")
        assert aapl.bullish_score == 9  # from cache

    async def test_analyze_batch_isolates_errors(self):
        """If analyze() raises for one stock, others still succeed."""
        call_count = 0

        async def _side_effect(stock):
            nonlocal call_count
            call_count += 1
            if stock.symbol == "BAD":
                raise RuntimeError("LLM timeout")
            return _make_signal(symbol=stock.symbol, score=7)

        mock_analyst = AsyncMock()
        mock_analyst.analyze = AsyncMock(side_effect=_side_effect)

        wrapper = _make_wrapper(analyst=mock_analyst, cache_enabled=False)
        results = await wrapper.analyze_batch(
            [_make_stock("AAPL"), _make_stock("BAD"), _make_stock("MSFT")]
        )

        assert len(results) == 3
        bad = next(r for r in results if r.symbol == "BAD")
        assert bad.bullish_score == 5  # neutral fallback
        assert bad.confidence == 1
        good = next(r for r in results if r.symbol == "AAPL")
        assert good.bullish_score == 7

    async def test_analyze_batch_empty_input(self):
        wrapper = _make_wrapper()
        results = await wrapper.analyze_batch([])
        assert results == []
