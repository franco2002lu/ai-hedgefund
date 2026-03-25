from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel

from app.modules.data_platform.adapters.sec_edgar_client import (
    SECEdgarClient,
    parse_xbrl_filings,
)

logger = logging.getLogger(__name__)


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


SEC_FILING_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}&dateb=&owner=include&count=40"


class SECEdgarAdapter:
    """Fetches earnings and financial filing data from SEC EDGAR."""

    def __init__(self) -> None:
        self._client = SECEdgarClient()
        self._cik_map: dict[str, int] | None = None

    async def _ensure_cik_map(self) -> dict[str, int]:
        if self._cik_map is None:
            self._cik_map = await self._client.fetch_ticker_cik_map()
        return self._cik_map

    async def get_recent_filings(
        self,
        symbol: str,
        filing_types: list[str] | None = None,
        limit: int = 4,
    ) -> list[Filing]:
        cik_map = await self._ensure_cik_map()
        cik = cik_map.get(symbol.upper())
        if cik is None:
            return []

        facts = await self._client.fetch_company_facts(cik)
        if not facts:
            return []

        # Extract filing metadata from XBRL data
        target_types = set(filing_types or ["10-K", "10-Q"])
        concepts = {"us-gaap": ["Revenues", "NetIncomeLoss"]}
        records = parse_xbrl_filings(facts, concepts)

        # Deduplicate by (filing_date, form)
        seen: set[tuple[date, str]] = set()
        filings: list[Filing] = []
        for rec in sorted(records, key=lambda r: r["filed"], reverse=True):
            if rec["form"] not in target_types:
                continue
            key = (rec["filed"], rec["form"])
            if key in seen:
                continue
            seen.add(key)
            cik_padded = str(cik).zfill(10)
            filings.append(Filing(
                symbol=symbol,
                filing_type=rec["form"],
                filing_date=rec["filed"],
                period_end=rec["end"],
                url=SEC_FILING_URL.format(cik=cik_padded, form=rec["form"]),
            ))
            if len(filings) >= limit:
                break

        return filings

    async def get_earnings_data(
        self,
        symbol: str,
        quarters: int = 4,
    ) -> list[QuarterlyEarnings]:
        cik_map = await self._ensure_cik_map()
        cik = cik_map.get(symbol.upper())
        if cik is None:
            return []

        facts = await self._client.fetch_company_facts(cik)
        if not facts:
            return []

        concepts = {
            "us-gaap": [
                "Revenues",
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "EarningsPerShareDiluted",
                "EarningsPerShareBasic",
            ],
        }
        records = parse_xbrl_filings(facts, concepts)

        # Group by (period_end, form) and extract revenue + EPS
        quarterly_data: dict[date, dict] = {}
        for rec in records:
            if rec["form"] != "10-Q":
                continue
            pe = rec["end"]
            quarterly_data.setdefault(pe, {"period_end": pe, "filed": rec["filed"]})
            if "Revenues" in rec["concept"] or "Revenue" in rec["concept"]:
                quarterly_data[pe]["revenue"] = rec["value"]
            if "EarningsPerShare" in rec["concept"]:
                quarterly_data[pe]["eps"] = rec["value"]

        # Sort by period end descending, take most recent quarters
        sorted_quarters = sorted(quarterly_data.values(), key=lambda x: x["period_end"], reverse=True)

        results: list[QuarterlyEarnings] = []
        for q in sorted_quarters[:quarters]:
            results.append(QuarterlyEarnings(
                symbol=symbol,
                fiscal_quarter=q["period_end"].isoformat(),
                revenue=q.get("revenue"),
                eps=q.get("eps"),
            ))

        return results
