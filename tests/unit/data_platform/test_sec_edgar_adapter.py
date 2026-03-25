"""Unit tests for the SEC EDGAR adapter.

Tests mock the underlying SECEdgarClient to avoid real HTTP calls.
"""

import inspect
from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.modules.data_platform.adapters.sec_edgar import (
    Filing,
    QuarterlyEarnings,
    SECEdgarAdapter,
)

# --- Fake XBRL company facts for AAPL (minimal structure) ---

_FAKE_CIK_MAP = {"AAPL": 320193, "MSFT": 789019}

_FAKE_COMPANY_FACTS = {
    "cik": 320193,
    "entityName": "Apple Inc",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "val": 94_836_000_000,
                            "end": "2025-06-30",
                            "filed": "2025-07-30",
                            "form": "10-Q",
                            "fy": 2025,
                            "fp": "Q3",
                            "accn": "0000320193-25-000001",
                        },
                        {
                            "val": 391_000_000_000,
                            "end": "2025-09-30",
                            "filed": "2025-10-30",
                            "form": "10-K",
                            "fy": 2025,
                            "fp": "FY",
                            "accn": "0000320193-25-000002",
                        },
                    ],
                },
            },
            "NetIncomeLoss": {
                "units": {
                    "USD": [
                        {
                            "val": 24_000_000_000,
                            "end": "2025-06-30",
                            "filed": "2025-07-30",
                            "form": "10-Q",
                            "fy": 2025,
                            "fp": "Q3",
                            "accn": "0000320193-25-000001",
                        },
                    ],
                },
            },
            "EarningsPerShareDiluted": {
                "units": {
                    "USD/shares": [
                        {
                            "val": 1.64,
                            "end": "2025-06-30",
                            "filed": "2025-07-30",
                            "form": "10-Q",
                            "fy": 2025,
                            "fp": "Q3",
                            "accn": "0000320193-25-000001",
                        },
                    ],
                },
            },
        },
    },
}


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
    async def test_returns_filings_from_xbrl(self):
        """With mocked client, get_recent_filings returns parsed Filing objects."""
        adapter = SECEdgarAdapter()
        adapter._client.fetch_ticker_cik_map = AsyncMock(return_value=_FAKE_CIK_MAP)
        adapter._client.fetch_company_facts = AsyncMock(return_value=_FAKE_COMPANY_FACTS)

        filings = await adapter.get_recent_filings("AAPL")

        assert len(filings) > 0
        assert all(isinstance(f, Filing) for f in filings)
        assert all(f.symbol == "AAPL" for f in filings)

    async def test_accepts_filing_types_param(self):
        """Filing types parameter filters which forms are returned."""
        adapter = SECEdgarAdapter()
        adapter._client.fetch_ticker_cik_map = AsyncMock(return_value=_FAKE_CIK_MAP)
        adapter._client.fetch_company_facts = AsyncMock(return_value=_FAKE_COMPANY_FACTS)

        filings = await adapter.get_recent_filings("AAPL", filing_types=["10-K"], limit=4)

        # Should only contain 10-K filings
        assert all(f.filing_type == "10-K" for f in filings)

    async def test_unknown_symbol_returns_empty(self):
        """Symbol not in CIK map returns empty list."""
        adapter = SECEdgarAdapter()
        adapter._client.fetch_ticker_cik_map = AsyncMock(return_value=_FAKE_CIK_MAP)

        filings = await adapter.get_recent_filings("UNKNOWN")

        assert filings == []

    async def test_accepts_custom_limit(self):
        """Limit parameter is respected (no crash)."""
        adapter = SECEdgarAdapter()
        adapter._client.fetch_ticker_cik_map = AsyncMock(return_value=_FAKE_CIK_MAP)
        adapter._client.fetch_company_facts = AsyncMock(return_value=_FAKE_COMPANY_FACTS)

        filings = await adapter.get_recent_filings("AAPL", limit=1)

        assert len(filings) <= 1


class TestGetEarningsData:
    async def test_returns_quarterly_earnings(self):
        """With mocked client, get_earnings_data returns parsed QuarterlyEarnings."""
        adapter = SECEdgarAdapter()
        adapter._client.fetch_ticker_cik_map = AsyncMock(return_value=_FAKE_CIK_MAP)
        adapter._client.fetch_company_facts = AsyncMock(return_value=_FAKE_COMPANY_FACTS)

        earnings = await adapter.get_earnings_data("AAPL")

        assert isinstance(earnings, list)
        assert all(isinstance(e, QuarterlyEarnings) for e in earnings)
        assert all(e.symbol == "AAPL" for e in earnings)

    async def test_respects_quarters_param(self):
        """Quarters parameter limits the number of results."""
        adapter = SECEdgarAdapter()
        adapter._client.fetch_ticker_cik_map = AsyncMock(return_value=_FAKE_CIK_MAP)
        adapter._client.fetch_company_facts = AsyncMock(return_value=_FAKE_COMPANY_FACTS)

        earnings = await adapter.get_earnings_data("AAPL", quarters=1)

        assert len(earnings) <= 1

    async def test_default_quarters_is_four(self):
        """Verify the default param value by inspecting the signature."""
        sig = inspect.signature(SECEdgarAdapter.get_earnings_data)
        quarters_param = sig.parameters["quarters"]
        assert quarters_param.default == 4

    async def test_unknown_symbol_returns_empty(self):
        """Symbol not in CIK map returns empty list."""
        adapter = SECEdgarAdapter()
        adapter._client.fetch_ticker_cik_map = AsyncMock(return_value=_FAKE_CIK_MAP)

        earnings = await adapter.get_earnings_data("UNKNOWN")

        assert earnings == []
