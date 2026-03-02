"""Unit tests for the Screener pipeline."""

from unittest.mock import AsyncMock

from app.modules.equities.models import UniverseStock
from app.modules.equities.universe.screener import Screener, ScreeningFilter


def _make_stock(symbol: str = "AAPL") -> UniverseStock:
    return UniverseStock(symbol=symbol, company_name=f"{symbol} Inc.", weight=0.05, sector="Technology")


class _MockFilter(ScreeningFilter):
    """A concrete ScreeningFilter backed by an AsyncMock for apply()."""

    def __init__(self, filter_name: str, apply_fn):
        self._name = filter_name
        self._apply_fn = apply_fn

    @property
    def name(self) -> str:
        return self._name

    async def apply(self, stocks, data_service):
        return await self._apply_fn(stocks, data_service)


class TestScreenerPipeline:
    async def test_filters_compose_correctly(self):
        """Two filters compose: first keeps A,B; second keeps B only."""
        stocks = [_make_stock("A"), _make_stock("B"), _make_stock("C")]

        async def filter1(stks, ds):
            return [s for s in stks if s.symbol in ("A", "B")]

        async def filter2(stks, ds):
            return [s for s in stks if s.symbol == "B"]

        mock_apply1 = AsyncMock(side_effect=filter1)
        mock_apply2 = AsyncMock(side_effect=filter2)

        f1 = _MockFilter("KeepAB", mock_apply1)
        f2 = _MockFilter("KeepB", mock_apply2)

        screener = Screener(filters=[f1, f2])
        result = await screener.screen(stocks, AsyncMock())

        assert len(result) == 1
        assert result[0].symbol == "B"
        mock_apply1.assert_awaited_once()
        mock_apply2.assert_awaited_once()

    async def test_empty_universe_returns_empty(self):
        async def passthrough(stks, ds):
            return stks

        f = _MockFilter("Pass", AsyncMock(side_effect=passthrough))
        screener = Screener(filters=[f])
        result = await screener.screen([], AsyncMock())
        assert result == []

    async def test_all_filtered_returns_empty(self):
        stocks = [_make_stock("A"), _make_stock("B")]

        async def reject_all(stks, ds):
            return []

        f = _MockFilter("RejectAll", AsyncMock(side_effect=reject_all))
        screener = Screener(filters=[f])
        result = await screener.screen(stocks, AsyncMock())
        assert result == []

    async def test_single_filter_pipeline(self):
        stocks = [_make_stock("A"), _make_stock("B"), _make_stock("C")]

        async def keep_first_two(stks, ds):
            return stks[:2]

        f = _MockFilter("KeepTwo", AsyncMock(side_effect=keep_first_two))
        screener = Screener(filters=[f])
        result = await screener.screen(stocks, AsyncMock())

        assert len(result) == 2
        assert result[0].symbol == "A"
        assert result[1].symbol == "B"

    async def test_no_filters_returns_all(self):
        stocks = [_make_stock("A"), _make_stock("B"), _make_stock("C")]
        screener = Screener(filters=[])
        result = await screener.screen(stocks, AsyncMock())
        assert len(result) == 3
        assert [s.symbol for s in result] == ["A", "B", "C"]
