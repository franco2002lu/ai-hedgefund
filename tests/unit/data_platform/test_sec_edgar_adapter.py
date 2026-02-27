"""Unit tests for the SEC EDGAR adapter."""

from datetime import date

import pytest

from app.modules.data_platform.adapters.sec_edgar import (
    Filing,
    QuarterlyEarnings,
    SECEdgarAdapter,
)


def _make_filing(**overrides) -> dict:
    """Build a canned filing response dict."""
    defaults = dict(
        symbol="AAPL",
        filing_type="10-K",
        filing_date="2025-10-30",
        period_end="2025-09-30",
        url="https://www.sec.gov/Archives/edgar/data/320193/10-K.htm",
    )
    defaults.update(overrides)
    return defaults


def _make_earnings(**overrides) -> dict:
    """Build a canned quarterly earnings response dict."""
    defaults = dict(
        symbol="AAPL",
        fiscal_quarter="Q3 2025",
        revenue=94_836_000_000,
        eps=1.64,
        revenue_estimate=89_000_000_000,
        eps_estimate=1.55,
        revenue_surprise_pct=0.065,
        eps_surprise_pct=0.058,
    )
    defaults.update(overrides)
    return defaults


class TestFilingModel:
    def test_construction(self):
        filing = Filing(
            symbol="AAPL",
            filing_type="10-K",
            filing_date=date(2025, 10, 30),
            period_end=date(2025, 9, 30),
            url="https://www.sec.gov/Archives/edgar/data/320193/10-K.htm",
        )
        assert filing.symbol == "AAPL"
        assert filing.filing_type == "10-K"
        assert filing.filing_date == date(2025, 10, 30)
        assert filing.period_end == date(2025, 9, 30)
        assert "sec.gov" in filing.url

    def test_various_filing_types(self):
        for ft in ["10-K", "10-Q", "8-K"]:
            filing = Filing(
                symbol="AAPL",
                filing_type=ft,
                filing_date=date(2025, 1, 1),
                period_end=date(2024, 12, 31),
                url="https://sec.gov",
            )
            assert filing.filing_type == ft


class TestQuarterlyEarningsModel:
    def test_construction_with_all_fields(self):
        earnings = QuarterlyEarnings(
            symbol="AAPL",
            fiscal_quarter="Q3 2025",
            revenue=94_836_000_000,
            eps=1.64,
            revenue_estimate=89_000_000_000,
            eps_estimate=1.55,
            revenue_surprise_pct=0.065,
            eps_surprise_pct=0.058,
        )
        assert earnings.symbol == "AAPL"
        assert earnings.fiscal_quarter == "Q3 2025"
        assert earnings.revenue == 94_836_000_000
        assert earnings.eps == pytest.approx(1.64)
        assert earnings.revenue_surprise_pct == pytest.approx(0.065)
        assert earnings.eps_surprise_pct == pytest.approx(0.058)

    def test_optional_fields_default_to_none(self):
        earnings = QuarterlyEarnings(symbol="AAPL", fiscal_quarter="Q3 2025")
        assert earnings.revenue is None
        assert earnings.eps is None
        assert earnings.revenue_estimate is None
        assert earnings.eps_estimate is None
        assert earnings.revenue_surprise_pct is None
        assert earnings.eps_surprise_pct is None

    def test_missing_estimates_only(self):
        """Revenue and EPS present, but estimates missing."""
        earnings = QuarterlyEarnings(
            symbol="AAPL",
            fiscal_quarter="Q1 2025",
            revenue=80_000_000_000,
            eps=1.20,
        )
        assert earnings.revenue == 80_000_000_000
        assert earnings.eps == pytest.approx(1.20)
        assert earnings.revenue_estimate is None
        assert earnings.eps_estimate is None


class TestGetRecentFilings:
    @pytest.mark.asyncio
    async def test_stub_raises_not_implemented(self):
        adapter = SECEdgarAdapter()
        with pytest.raises(NotImplementedError):
            await adapter.get_recent_filings("AAPL")

    @pytest.mark.asyncio
    async def test_accepts_filing_types_param(self):
        adapter = SECEdgarAdapter()
        with pytest.raises(NotImplementedError):
            await adapter.get_recent_filings("AAPL", filing_types=["10-K", "10-Q"], limit=4)

    @pytest.mark.asyncio
    async def test_accepts_custom_limit(self):
        adapter = SECEdgarAdapter()
        with pytest.raises(NotImplementedError):
            await adapter.get_recent_filings("MSFT", limit=10)


class TestGetEarningsData:
    @pytest.mark.asyncio
    async def test_stub_raises_not_implemented(self):
        adapter = SECEdgarAdapter()
        with pytest.raises(NotImplementedError):
            await adapter.get_earnings_data("AAPL")

    @pytest.mark.asyncio
    async def test_respects_quarters_param(self):
        adapter = SECEdgarAdapter()
        with pytest.raises(NotImplementedError):
            await adapter.get_earnings_data("AAPL", quarters=8)

    @pytest.mark.asyncio
    async def test_default_quarters_is_four(self):
        """Verify the default param value by inspecting the signature."""
        import inspect

        sig = inspect.signature(SECEdgarAdapter.get_earnings_data)
        quarters_param = sig.parameters["quarters"]
        assert quarters_param.default == 4
