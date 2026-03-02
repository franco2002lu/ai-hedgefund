"""Unit tests for each of the 17 screening filters (~50 tests).

Each filter gets pass / reject / boundary tests with a mocked DataPlatformService.
TDD tests -- the tests define the contract for stub implementations.
"""

from unittest.mock import AsyncMock

import pytest

from app.modules.equities.models import UniverseStock
from app.modules.equities.universe.screener import (
    DividendYieldFilter,
    EarningsGrowthFilter,
    EarningsRecencyFilter,
    EarningsSurpriseFilter,
    FCFYieldFilter,
    GrossMarginTrendFilter,
    LeverageFilter,
    LiquidityFilter,
    MarketCapFilter,
    MomentumFilter,
    PBFilter,
    PEFilter,
    PEGFilter,
    PriceRangeFilter,
    RevenueGrowthFilter,
    ROEFilter,
    VolatilityFilter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stock(symbol: str = "AAPL", **overrides) -> UniverseStock:
    """Convenience builder -- local to this module."""
    defaults = dict(
        symbol=symbol,
        company_name=f"{symbol} Inc.",
        weight=0.03,
        sector="Technology",
    )
    defaults.update(overrides)
    return UniverseStock(**defaults)


def _metrics_response(symbol: str, metrics_dict: dict) -> dict:
    """Build the standard get_metrics() return envelope."""
    return {"symbol": symbol, "metrics": [metrics_dict], "source": "yahoo"}


# ---------------------------------------------------------------------------
# Shared Filters (5)
# ---------------------------------------------------------------------------


class TestLiquidityFilter:
    """LiquidityFilter checks averageDailyVolume10Day >= threshold."""

    async def test_passes_stock_above_threshold(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("AAPL", {"averageDailyVolume10Day": 1_000_000})
        f = LiquidityFilter(min_avg_daily_volume=500_000)
        result = await f.apply([_make_stock("AAPL")], data_service)
        assert len(result) == 1
        assert result[0].symbol == "AAPL"

    async def test_rejects_stock_below_threshold(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("THIN", {"averageDailyVolume10Day": 100_000})
        f = LiquidityFilter(min_avg_daily_volume=500_000)
        result = await f.apply([_make_stock("THIN")], data_service)
        assert len(result) == 0

    async def test_name_property(self):
        assert LiquidityFilter().name == "LiquidityFilter"


class TestMarketCapFilter:
    """MarketCapFilter checks marketCap >= threshold."""

    async def test_passes_large_cap(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("AAPL", {"marketCap": 3e12})
        f = MarketCapFilter(min_market_cap=2e9)
        result = await f.apply([_make_stock("AAPL")], data_service)
        assert len(result) == 1
        assert result[0].symbol == "AAPL"

    async def test_rejects_small_cap(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("TINY", {"marketCap": 500e6})
        f = MarketCapFilter(min_market_cap=2e9)
        result = await f.apply([_make_stock("TINY")], data_service)
        assert len(result) == 0

    async def test_boundary_at_threshold(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("EDGE", {"marketCap": 2e9})
        f = MarketCapFilter(min_market_cap=2e9)
        result = await f.apply([_make_stock("EDGE")], data_service)
        assert len(result) == 1
        assert result[0].symbol == "EDGE"


class TestEarningsRecencyFilter:
    """EarningsRecencyFilter checks daysSinceLastEarnings <= threshold."""

    async def test_passes_recent_earnings(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("FRESH", {"daysSinceLastEarnings": 60})
        f = EarningsRecencyFilter(max_days_since_earnings=120)
        result = await f.apply([_make_stock("FRESH")], data_service)
        assert len(result) == 1

    async def test_rejects_stale_earnings(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("STALE", {"daysSinceLastEarnings": 150})
        f = EarningsRecencyFilter(max_days_since_earnings=120)
        result = await f.apply([_make_stock("STALE")], data_service)
        assert len(result) == 0

    async def test_handles_no_earnings_data(self):
        """When earnings date is absent the stock must be rejected."""
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("NONE", {})
        f = EarningsRecencyFilter(max_days_since_earnings=120)
        result = await f.apply([_make_stock("NONE")], data_service)
        assert len(result) == 0


class TestVolatilityFilter:
    """VolatilityFilter rejects the top N-percentile most volatile stocks."""

    async def test_passes_normal_volatility(self):
        """A stock NOT in the top 5% should pass."""
        data_service = AsyncMock()

        async def _mock_metrics(symbol, **kw):
            vols = {"CALM": 0.20, "MILD": 0.25}
            return _metrics_response(symbol, {"volatility90d": vols.get(symbol, 0.20)})

        data_service.get_metrics.side_effect = _mock_metrics
        f = VolatilityFilter(max_volatility_percentile=95.0)
        stocks = [_make_stock("CALM"), _make_stock("MILD")]
        result = await f.apply(stocks, data_service)
        # With only 2 stocks neither is extreme enough to be top-5%
        symbols = {s.symbol for s in result}
        assert "CALM" in symbols

    async def test_rejects_top_percentile(self):
        """With 20 stocks, the top 5% (1 stock) should be rejected."""
        data_service = AsyncMock()
        sym_list = [f"S{i:02d}" for i in range(20)]
        stocks = [_make_stock(s) for s in sym_list]

        # Assign volatilities: S00=0.10, S01=0.11, ..., S19=0.29
        async def _mock_metrics(symbol, **kw):
            idx = int(symbol[1:])
            vol = 0.10 + idx * 0.01
            return _metrics_response(symbol, {"volatility90d": vol})

        data_service.get_metrics.side_effect = _mock_metrics
        f = VolatilityFilter(max_volatility_percentile=95.0)
        result = await f.apply(stocks, data_service)
        result_symbols = {s.symbol for s in result}
        # The most volatile stock S19 should be excluded
        assert "S19" not in result_symbols
        # Less volatile stocks should remain
        assert "S00" in result_symbols
        assert "S10" in result_symbols

    async def test_needs_multiple_stocks_for_percentile(self):
        """A single stock always passes -- cannot compute meaningful percentile."""
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("SOLO", {"volatility90d": 0.90})
        f = VolatilityFilter(max_volatility_percentile=95.0)
        result = await f.apply([_make_stock("SOLO")], data_service)
        assert len(result) == 1
        assert result[0].symbol == "SOLO"


class TestLeverageFilter:
    """LeverageFilter checks debtToEquity <= threshold."""

    async def test_passes_moderate_leverage(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("SAFE", {"debtToEquity": 2.0})
        f = LeverageFilter(max_debt_to_equity=5.0)
        result = await f.apply([_make_stock("SAFE")], data_service)
        assert len(result) == 1

    async def test_rejects_high_leverage(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("RISKY", {"debtToEquity": 10.0})
        f = LeverageFilter(max_debt_to_equity=5.0)
        result = await f.apply([_make_stock("RISKY")], data_service)
        assert len(result) == 0

    async def test_handles_missing_data(self):
        """When debtToEquity is absent the stock should be rejected.

        NOTE: Current stub defaults to 0.0 which passes. This test documents the
        desired TDD contract -- implementation should treat missing leverage as rejection.
        """
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("NODATA", {})
        f = LeverageFilter(max_debt_to_equity=5.0)
        result = await f.apply([_make_stock("NODATA")], data_service)
        # TDD contract: missing data -> rejected
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Growth Filters (6)
# ---------------------------------------------------------------------------


class TestRevenueGrowthFilter:
    """RevenueGrowthFilter checks revenueGrowthYoy >= threshold."""

    async def test_passes_high_growth(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("FAST", {"revenueGrowthYoy": 0.20})
        f = RevenueGrowthFilter(min_revenue_growth_yoy=0.05)
        result = await f.apply([_make_stock("FAST")], data_service)
        assert len(result) == 1

    async def test_rejects_low_growth(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("SLOW", {"revenueGrowthYoy": 0.02})
        f = RevenueGrowthFilter(min_revenue_growth_yoy=0.05)
        result = await f.apply([_make_stock("SLOW")], data_service)
        assert len(result) == 0

    async def test_rejects_negative_growth(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("SHRK", {"revenueGrowthYoy": -0.10})
        f = RevenueGrowthFilter(min_revenue_growth_yoy=0.05)
        result = await f.apply([_make_stock("SHRK")], data_service)
        assert len(result) == 0


class TestEarningsGrowthFilter:
    """EarningsGrowthFilter checks earningsGrowthYoy >= threshold."""

    async def test_passes_positive_growth(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("EARN", {"earningsGrowthYoy": 0.15})
        f = EarningsGrowthFilter(min_earnings_growth_yoy=0.0)
        result = await f.apply([_make_stock("EARN")], data_service)
        assert len(result) == 1

    async def test_rejects_negative_growth(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("LOSE", {"earningsGrowthYoy": -0.05})
        f = EarningsGrowthFilter(min_earnings_growth_yoy=0.0)
        result = await f.apply([_make_stock("LOSE")], data_service)
        assert len(result) == 0


class TestGrossMarginTrendFilter:
    """GrossMarginTrendFilter rejects stocks with >= N declining margin quarters."""

    async def test_passes_improving_margins(self):
        """margins=[0.30, 0.32, 0.34, 0.35] => 0 declining quarters => passes."""
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("GOOD", {"grossMarginDecliningQuarters": 0})
        f = GrossMarginTrendFilter(margin_declining_quarters=2)
        result = await f.apply([_make_stock("GOOD")], data_service)
        assert len(result) == 1

    async def test_rejects_declining_margins(self):
        """margins=[0.35, 0.33, 0.30, 0.28] => 3 declining quarters (>=2) => rejected."""
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("DECL", {"grossMarginDecliningQuarters": 3})
        f = GrossMarginTrendFilter(margin_declining_quarters=2)
        result = await f.apply([_make_stock("DECL")], data_service)
        assert len(result) == 0


class TestEarningsSurpriseFilter:
    """EarningsSurpriseFilter checks lastEarningsSurprisePct >= threshold."""

    async def test_passes_beat(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("BEAT", {"lastEarningsSurprisePct": 0.05})
        f = EarningsSurpriseFilter(min_surprise_pct=-0.05)
        result = await f.apply([_make_stock("BEAT")], data_service)
        assert len(result) == 1

    async def test_passes_small_miss_within_threshold(self):
        """A miss of -3% is above the -5% threshold => passes."""
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("NEAR", {"lastEarningsSurprisePct": -0.03})
        f = EarningsSurpriseFilter(min_surprise_pct=-0.05)
        result = await f.apply([_make_stock("NEAR")], data_service)
        assert len(result) == 1

    async def test_rejects_large_miss(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("MISS", {"lastEarningsSurprisePct": -0.10})
        f = EarningsSurpriseFilter(min_surprise_pct=-0.05)
        result = await f.apply([_make_stock("MISS")], data_service)
        assert len(result) == 0


class TestMomentumFilter:
    """MomentumFilter checks return6m >= threshold."""

    async def test_passes_positive_momentum(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("UP", {"return6m": 0.15})
        f = MomentumFilter(min_return_6m=-0.10)
        result = await f.apply([_make_stock("UP")], data_service)
        assert len(result) == 1

    async def test_passes_small_decline_within_threshold(self):
        """A 6m return of -5% is above the -10% threshold => passes."""
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("DIP", {"return6m": -0.05})
        f = MomentumFilter(min_return_6m=-0.10)
        result = await f.apply([_make_stock("DIP")], data_service)
        assert len(result) == 1

    async def test_rejects_severe_decline(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("CRASH", {"return6m": -0.30})
        f = MomentumFilter(min_return_6m=-0.10)
        result = await f.apply([_make_stock("CRASH")], data_service)
        assert len(result) == 0


class TestPEGFilter:
    """PEGFilter checks pegRatio <= threshold."""

    async def test_passes_reasonable_peg(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("FAIR", {"pegRatio": 1.5})
        f = PEGFilter(max_peg_ratio=3.0)
        result = await f.apply([_make_stock("FAIR")], data_service)
        assert len(result) == 1
        assert result[0].symbol == "FAIR"

    async def test_rejects_extreme_peg(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("EXPEN", {"pegRatio": 5.0})
        f = PEGFilter(max_peg_ratio=3.0)
        result = await f.apply([_make_stock("EXPEN")], data_service)
        assert len(result) == 0

    async def test_handles_negative_growth(self):
        """When growth <= 0 the PEG ratio is undefined / meaningless => rejected.

        NOTE: Current stub defaults missing pegRatio to 0.0 which passes.
        This test documents the TDD contract: negative-growth PEG should be rejected.
        """
        data_service = AsyncMock()
        # Negative PEG signals negative growth -- should be treated as invalid
        data_service.get_metrics.return_value = _metrics_response("NEGPEG", {"pegRatio": -2.0})
        f = PEGFilter(max_peg_ratio=3.0)
        result = await f.apply([_make_stock("NEGPEG")], data_service)
        # Negative PEG is meaningless; TDD contract says reject
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Value Filters (6)
# ---------------------------------------------------------------------------


class TestPEFilter:
    """PEFilter uses percentile-based cutoff across the universe."""

    async def test_passes_below_percentile(self):
        """Stock with PE in the bottom 50% of a 10-stock universe should pass."""
        data_service = AsyncMock()
        symbols = [f"V{i}" for i in range(10)]
        stocks = [_make_stock(s) for s in symbols]

        # PEs: V0=5, V1=10, V2=15, ... V9=50
        async def _mock_metrics(symbol, **kw):
            idx = int(symbol[1:])
            pe = 5.0 + idx * 5.0
            return _metrics_response(symbol, {"peRatio": pe})

        data_service.get_metrics.side_effect = _mock_metrics
        f = PEFilter(max_pe_percentile=60.0)
        result = await f.apply(stocks, data_service)
        result_symbols = {s.symbol for s in result}
        # V0 (PE=5) is cheapest, must pass
        assert "V0" in result_symbols
        # V1 (PE=10) also cheap, must pass
        assert "V1" in result_symbols

    async def test_rejects_above_percentile(self):
        """Stock with PE in the top 20% of a 10-stock universe should be rejected."""
        data_service = AsyncMock()
        symbols = [f"V{i}" for i in range(10)]
        stocks = [_make_stock(s) for s in symbols]

        # PEs: V0=5, V1=10, V2=15, ... V9=50
        async def _mock_metrics(symbol, **kw):
            idx = int(symbol[1:])
            pe = 5.0 + idx * 5.0
            return _metrics_response(symbol, {"peRatio": pe})

        data_service.get_metrics.side_effect = _mock_metrics
        f = PEFilter(max_pe_percentile=60.0)
        result = await f.apply(stocks, data_service)
        result_symbols = {s.symbol for s in result}
        # V9 (PE=50) is the most expensive -- must be rejected
        assert "V9" not in result_symbols

    async def test_percentile_from_universe(self):
        """Must create 10 stocks to compute percentile -- verifies universe-relative logic."""
        data_service = AsyncMock()
        symbols = [f"U{i}" for i in range(10)]
        stocks = [_make_stock(s) for s in symbols]

        # Linear PE: U0=8, U1=12, U2=16, ... U9=44
        async def _mock_metrics(symbol, **kw):
            idx = int(symbol[1:])
            pe = 8.0 + idx * 4.0
            return _metrics_response(symbol, {"peRatio": pe})

        data_service.get_metrics.side_effect = _mock_metrics
        f = PEFilter(max_pe_percentile=50.0)
        result = await f.apply(stocks, data_service)
        # At 50th percentile, roughly bottom half should pass
        assert len(result) >= 4
        assert len(result) <= 6
        # Cheapest must be in, most expensive must be out
        result_symbols = {s.symbol for s in result}
        assert "U0" in result_symbols
        assert "U9" not in result_symbols


class TestPBFilter:
    """PBFilter uses percentile-based cutoff across the universe (same as PE)."""

    async def test_passes_below_percentile(self):
        data_service = AsyncMock()
        symbols = [f"B{i}" for i in range(10)]
        stocks = [_make_stock(s) for s in symbols]

        async def _mock_metrics(symbol, **kw):
            idx = int(symbol[1:])
            pb = 0.5 + idx * 0.5  # B0=0.5, B1=1.0, ... B9=5.0
            return _metrics_response(symbol, {"pbRatio": pb})

        data_service.get_metrics.side_effect = _mock_metrics
        f = PBFilter(max_pb_percentile=60.0)
        result = await f.apply(stocks, data_service)
        result_symbols = {s.symbol for s in result}
        # Cheapest P/B stocks should pass
        assert "B0" in result_symbols
        assert "B1" in result_symbols

    async def test_rejects_above_percentile(self):
        data_service = AsyncMock()
        symbols = [f"B{i}" for i in range(10)]
        stocks = [_make_stock(s) for s in symbols]

        async def _mock_metrics(symbol, **kw):
            idx = int(symbol[1:])
            pb = 0.5 + idx * 0.5
            return _metrics_response(symbol, {"pbRatio": pb})

        data_service.get_metrics.side_effect = _mock_metrics
        f = PBFilter(max_pb_percentile=60.0)
        result = await f.apply(stocks, data_service)
        result_symbols = {s.symbol for s in result}
        # Most expensive P/B stock should be rejected
        assert "B9" not in result_symbols


class TestFCFYieldFilter:
    """FCFYieldFilter checks fcfYield >= threshold."""

    async def test_passes_high_yield(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("CASH", {"fcfYield": 0.05})
        f = FCFYieldFilter(min_fcf_yield=0.02)
        result = await f.apply([_make_stock("CASH")], data_service)
        assert len(result) == 1
        assert result[0].symbol == "CASH"

    async def test_rejects_low_yield(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("BURN", {"fcfYield": 0.01})
        f = FCFYieldFilter(min_fcf_yield=0.02)
        result = await f.apply([_make_stock("BURN")], data_service)
        assert len(result) == 0


class TestDividendYieldFilter:
    """DividendYieldFilter checks dividendYield >= threshold."""

    async def test_passes_above_minimum(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("DIVS", {"dividendYield": 0.02})
        f = DividendYieldFilter(min_dividend_yield=0.005)
        result = await f.apply([_make_stock("DIVS")], data_service)
        assert len(result) == 1

    async def test_rejects_zero_dividend(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("NODIV", {"dividendYield": 0.0})
        f = DividendYieldFilter(min_dividend_yield=0.005)
        result = await f.apply([_make_stock("NODIV")], data_service)
        assert len(result) == 0


class TestROEFilter:
    """ROEFilter checks returnOnEquity >= threshold."""

    async def test_passes_high_roe(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("GOOD", {"returnOnEquity": 0.15})
        f = ROEFilter(min_roe=0.08)
        result = await f.apply([_make_stock("GOOD")], data_service)
        assert len(result) == 1
        assert result[0].symbol == "GOOD"

    async def test_rejects_low_roe(self):
        data_service = AsyncMock()
        data_service.get_metrics.return_value = _metrics_response("POOR", {"returnOnEquity": 0.03})
        f = ROEFilter(min_roe=0.08)
        result = await f.apply([_make_stock("POOR")], data_service)
        assert len(result) == 0


class TestPriceRangeFilter:
    """PriceRangeFilter checks currentPrice position within 52-week range."""

    async def test_passes_in_lower_range(self):
        """Price at 40% of 52-week range should pass (threshold=70%)."""
        data_service = AsyncMock()
        # Low=100, High=200, Current=140 => (140-100)/(200-100) = 40%
        data_service.get_metrics.return_value = _metrics_response(
            "MID",
            {
                "fiftyTwoWeekHigh": 200.0,
                "fiftyTwoWeekLow": 100.0,
                "currentPrice": 140.0,
            },
        )
        f = PriceRangeFilter(max_52w_range_percentile=70.0)
        result = await f.apply([_make_stock("MID")], data_service)
        assert len(result) == 1
        # Verify the position math: (140-100)/(200-100)*100 = 40% <= 70%
        pct = (140.0 - 100.0) / (200.0 - 100.0) * 100.0
        assert pct == pytest.approx(40.0)

    async def test_rejects_near_52w_high(self):
        """Price at 95% of 52-week range should be rejected (threshold=70%)."""
        data_service = AsyncMock()
        # Low=100, High=200, Current=195 => (195-100)/(200-100) = 95%
        data_service.get_metrics.return_value = _metrics_response(
            "TOP",
            {
                "fiftyTwoWeekHigh": 200.0,
                "fiftyTwoWeekLow": 100.0,
                "currentPrice": 195.0,
            },
        )
        f = PriceRangeFilter(max_52w_range_percentile=70.0)
        result = await f.apply([_make_stock("TOP")], data_service)
        assert len(result) == 0
        # Verify the position math: (195-100)/(200-100)*100 = 95% > 70%
        pct = (195.0 - 100.0) / (200.0 - 100.0) * 100.0
        assert pct == pytest.approx(95.0)
