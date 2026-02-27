from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class Filing(BaseModel):
    symbol: str
    filing_type: str
    filing_date: date
    period_end: date
    url: str


class QuarterlyEarnings(BaseModel):
    symbol: str
    fiscal_quarter: str
    revenue: float | None = None
    eps: float | None = None
    revenue_estimate: float | None = None
    eps_estimate: float | None = None
    revenue_surprise_pct: float | None = None
    eps_surprise_pct: float | None = None


class SECEdgarAdapter:
    """Fetches earnings and financial filing data from SEC EDGAR."""

    async def get_recent_filings(
        self,
        symbol: str,
        filing_types: list[str] | None = None,
        limit: int = 4,
    ) -> list[Filing]:
        raise NotImplementedError

    async def get_earnings_data(
        self,
        symbol: str,
        quarters: int = 4,
    ) -> list[QuarterlyEarnings]:
        raise NotImplementedError
