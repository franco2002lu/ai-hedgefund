"""SEC EDGAR API client — fetches company facts (XBRL) for point-in-time fundamentals."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# SEC requires a descriptive User-Agent with contact info
SEC_USER_AGENT = "hedgefund-backtest admin@hedgefund.local"

# SEC rate limit: 10 requests/second
SEC_MAX_CONCURRENT = 8
SEC_REQUEST_DELAY = 0.12  # seconds between requests (~8/sec to stay safe)


class SECEdgarClientError(Exception):
    """Raised on unrecoverable SEC EDGAR API errors."""


class SECEdgarClient:
    """Async HTTP client for SEC EDGAR XBRL Company Facts API."""

    def __init__(self, user_agent: str = SEC_USER_AGENT) -> None:
        self._user_agent = user_agent
        self._semaphore = asyncio.Semaphore(SEC_MAX_CONCURRENT)

    async def fetch_ticker_cik_map(self) -> dict[str, int]:
        """Fetch the SEC ticker → CIK mapping.

        Returns:
            Dict mapping uppercase ticker symbols to CIK numbers.
        """
        async with httpx.AsyncClient(
            headers={"User-Agent": self._user_agent},
            timeout=30.0,
        ) as client:
            resp = await client.get(SEC_COMPANY_TICKERS_URL)
            resp.raise_for_status()
            data = resp.json()

        # Response format: {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, ...}
        mapping: dict[str, int] = {}
        for entry in data.values():
            ticker = entry.get("ticker", "").upper()
            cik = entry.get("cik_str")
            if ticker and cik:
                mapping[ticker] = int(cik)
        return mapping

    async def fetch_company_facts(self, cik: int) -> dict[str, Any]:
        """Fetch all XBRL facts for a company from SEC EDGAR.

        Args:
            cik: Central Index Key number.

        Returns:
            Raw JSON response with structure:
            {
                "cik": int,
                "entityName": str,
                "facts": {
                    "us-gaap": {concept: {units: {USD: [filing_entries]}}},
                    "dei": {...},
                }
            }
        """
        cik_padded = str(cik).zfill(10)
        url = SEC_COMPANY_FACTS_URL.format(cik=cik_padded)

        async with self._semaphore, httpx.AsyncClient(
            headers={"User-Agent": self._user_agent},
            timeout=30.0,
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            await asyncio.sleep(SEC_REQUEST_DELAY)
            return resp.json()

    async def fetch_company_facts_batch(
        self,
        ticker_cik_pairs: list[tuple[str, int]],
    ) -> dict[str, dict[str, Any]]:
        """Fetch company facts for multiple tickers.

        Args:
            ticker_cik_pairs: List of (ticker, cik) tuples.

        Returns:
            Dict mapping ticker to raw company facts JSON.
        """
        results: dict[str, dict[str, Any]] = {}

        async def _fetch_one(ticker: str, cik: int) -> None:
            try:
                facts = await self.fetch_company_facts(cik)
                if facts:
                    results[ticker] = facts
            except Exception:
                logger.debug("Failed to fetch SEC facts for %s (CIK %d)", ticker, cik)

        await asyncio.gather(*(_fetch_one(t, c) for t, c in ticker_cik_pairs))
        return results


def parse_xbrl_filings(
    company_facts: dict[str, Any],
    concepts: dict[str, list[str]],
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Parse raw SEC company facts into structured filing records.

    Extracts specific XBRL concepts from the company facts response,
    filtered to a date range.

    Args:
        company_facts: Raw response from fetch_company_facts().
        concepts: Mapping of taxonomy → list of concept names.
            e.g. {"us-gaap": ["Revenues", "NetIncomeLoss"], "dei": [...]}
        start_date: Only include filings on or after this date.
        end_date: Only include filings on or before this date.

    Returns:
        List of dicts, each representing one data point:
        {
            "concept": str,          # e.g. "Revenues"
            "taxonomy": str,         # e.g. "us-gaap"
            "value": float,
            "end": date,             # period end date
            "filed": date,           # SEC filing date (point-in-time key)
            "form": str,             # "10-K" or "10-Q"
            "fy": int,               # fiscal year
            "fp": str,               # fiscal period: "FY", "Q1", "Q2", "Q3"
            "accn": str,             # accession number
        }
    """
    facts = company_facts.get("facts", {})
    records: list[dict[str, Any]] = []

    for taxonomy, concept_names in concepts.items():
        taxonomy_facts = facts.get(taxonomy, {})
        for concept_name in concept_names:
            concept_data = taxonomy_facts.get(concept_name, {})
            units = concept_data.get("units", {})

            # Try USD first, then USD/shares (EPS, DPS), then shares, then pure
            for unit_key in ("USD", "USD/shares", "shares", "pure"):
                entries = units.get(unit_key, [])
                if entries:
                    break
            else:
                continue

            for entry in entries:
                form = entry.get("form", "")
                if form not in ("10-K", "10-Q"):
                    continue

                try:
                    filed_date = date.fromisoformat(entry["filed"])
                    period_end = date.fromisoformat(entry["end"])
                except (KeyError, ValueError):
                    continue

                if start_date and filed_date < start_date:
                    continue
                if end_date and filed_date > end_date:
                    continue

                records.append({
                    "concept": concept_name,
                    "taxonomy": taxonomy,
                    "value": float(entry["val"]),
                    "end": period_end,
                    "filed": filed_date,
                    "form": form,
                    "fy": entry.get("fy"),
                    "fp": entry.get("fp", ""),
                    "accn": entry.get("accn", ""),
                })

    return records
