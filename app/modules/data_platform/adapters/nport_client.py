"""N-PORT filing client — fetches fund holdings from SEC EDGAR N-PORT filings."""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel

from app.modules.data_platform.adapters.sec_edgar_client import (
    SEC_REQUEST_DELAY,
    SEC_USER_AGENT,
    SECEdgarClient,
)

logger = logging.getLogger(__name__)

VANGUARD_CIK = "0000891190"
SERIES_MAP = {"VOOG": "S000030012", "VOOV": "S000030013"}
NPORT_NS = "http://www.sec.gov/edgar/nport"

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_raw}/{accession_no_dashes}/{primary_doc}"

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_BATCH_SIZE = 10  # unauthenticated limit (100 with API key)
OPENFIGI_DELAY = 2.5  # seconds between batches (25 req/min unauthenticated limit)


class NPortFiling(BaseModel):
    accession_number: str
    filing_date: date
    report_date: date
    primary_doc: str


class NPortHolding(BaseModel):
    name: str
    cusip: str
    ticker: str | None = None
    balance: float
    value_usd: float
    pct_val: float
    asset_cat: str | None = None


class NPortClient:
    """Async client for fetching and parsing N-PORT XML filings from SEC EDGAR."""

    def __init__(self, sec_client: SECEdgarClient | None = None) -> None:
        self._sec_client = sec_client or SECEdgarClient()
        self._semaphore = asyncio.Semaphore(8)
        self._filing_index_cache: dict[str, list[NPortFiling]] = {}
        self._holdings_cache: dict[str, list[NPortHolding]] = {}

    async def fetch_filing_index(self, cik: str) -> list[NPortFiling]:
        """Fetch all NPORT-P filings from SEC submissions API.

        Returns filings sorted by report_date descending.
        """
        if cik in self._filing_index_cache:
            return self._filing_index_cache[cik]

        cik_padded = cik.zfill(10)
        url = SEC_SUBMISSIONS_URL.format(cik=cik_padded)

        try:
            async with self._semaphore, httpx.AsyncClient(
                headers={"User-Agent": SEC_USER_AGENT},
                timeout=30.0,
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                await asyncio.sleep(SEC_REQUEST_DELAY)
                data = resp.json()
        except Exception:
            logger.warning("Failed to fetch filing index for CIK %s", cik, exc_info=True)
            return []

        filings = self._parse_filing_index(data)
        self._filing_index_cache[cik] = filings
        return filings

    def _parse_filing_index(self, data: dict[str, Any]) -> list[NPortFiling]:
        """Extract NPORT-P filings from submissions JSON response."""
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        filings: list[NPortFiling] = []
        for i, form in enumerate(forms):
            if form != "NPORT-P":
                continue
            try:
                filings.append(NPortFiling(
                    accession_number=accession_numbers[i],
                    filing_date=date.fromisoformat(filing_dates[i]),
                    report_date=date.fromisoformat(report_dates[i]),
                    primary_doc=primary_docs[i],
                ))
            except (IndexError, ValueError):
                continue

        filings.sort(key=lambda f: f.report_date, reverse=True)
        return filings

    async def fetch_holdings(
        self, cik: str, filing: NPortFiling, series_id: str
    ) -> list[NPortHolding]:
        """Fetch and parse holdings from an N-PORT XML filing.

        Verifies the filing matches the expected series_id before extracting holdings.
        """
        cache_key = f"{filing.accession_number}:{series_id}"
        if cache_key in self._holdings_cache:
            return self._holdings_cache[cache_key]

        cik_raw = str(int(cik))
        accession_no_dashes = filing.accession_number.replace("-", "")
        raw_doc = filing.primary_doc.split("/")[-1]
        url = SEC_ARCHIVES_URL.format(
            cik_raw=cik_raw,
            accession_no_dashes=accession_no_dashes,
            primary_doc=raw_doc,
        )

        try:
            async with self._semaphore, httpx.AsyncClient(
                headers={"User-Agent": SEC_USER_AGENT},
                timeout=30.0,
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                await asyncio.sleep(SEC_REQUEST_DELAY)
                xml_text = resp.text
        except Exception:
            logger.warning(
                "Failed to fetch N-PORT XML for %s", filing.accession_number, exc_info=True
            )
            return []

        holdings = self._parse_nport_xml(xml_text, series_id)

        # Resolve missing tickers via OpenFIGI (CUSIP → ticker)
        unresolved = [h for h in holdings if not h.ticker]
        if unresolved:
            await self._resolve_tickers_via_openfigi(unresolved)
            holdings = [h for h in holdings if h.ticker]

        holdings.sort(key=lambda h: h.pct_val, reverse=True)
        self._holdings_cache[cache_key] = holdings
        return holdings

    def _parse_nport_xml(self, xml_text: str, series_id: str) -> list[NPortHolding]:
        """Parse N-PORT XML and extract holdings for the given series."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            logger.warning("Failed to parse N-PORT XML")
            return []

        ns = {"n": NPORT_NS}

        # Verify series ID matches
        series_el = root.find(".//n:genInfo/n:seriesId", ns)
        if series_el is None or series_el.text != series_id:
            return []

        holdings: list[NPortHolding] = []
        for inv in root.findall(".//n:invstOrSec", ns):
            name_el = inv.find("n:name", ns)
            cusip_el = inv.find("n:cusip", ns)
            balance_el = inv.find("n:balance", ns)
            val_el = inv.find("n:valUSD", ns)
            pct_el = inv.find("n:pctVal", ns)

            if any(el is None for el in [name_el, cusip_el, balance_el, val_el, pct_el]):
                continue

            # Extract ticker from <identifiers><ticker value="..."/>
            ticker = None
            ticker_el = inv.find("n:identifiers/n:ticker", ns)
            if ticker_el is not None:
                ticker = ticker_el.get("value")

            # Keep holdings with CUSIP even without ticker — resolved via OpenFIGI later
            if not ticker and not (cusip_el is not None and cusip_el.text):
                continue

            asset_cat_el = inv.find("n:assetCat", ns)

            try:
                holdings.append(NPortHolding(
                    name=name_el.text or "",
                    cusip=cusip_el.text or "",
                    ticker=ticker,
                    balance=float(balance_el.text or 0),
                    value_usd=float(val_el.text or 0),
                    pct_val=float(pct_el.text or 0),
                    asset_cat=asset_cat_el.text if asset_cat_el is not None else None,
                ))
            except (ValueError, TypeError):
                continue

        holdings.sort(key=lambda h: h.pct_val, reverse=True)
        return holdings

    async def _resolve_tickers_via_openfigi(
        self, holdings: list[NPortHolding]
    ) -> None:
        """Batch-resolve CUSIP → ticker via the OpenFIGI API (mutates holdings in place).

        OpenFIGI is a free Bloomberg service. Unauthenticated: 25 req/min, 100 items/batch.
        Holdings that fail to resolve keep ticker=None.
        """
        cusip_to_holdings: dict[str, list[NPortHolding]] = {}
        for h in holdings:
            cusip_to_holdings.setdefault(h.cusip, []).append(h)

        cusips = list(cusip_to_holdings.keys())

        for batch_start in range(0, len(cusips), OPENFIGI_BATCH_SIZE):
            batch = cusips[batch_start : batch_start + OPENFIGI_BATCH_SIZE]
            payload = [
                {"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"}
                for c in batch
            ]

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        OPENFIGI_URL,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    resp.raise_for_status()
                    results = resp.json()
            except Exception:
                logger.warning(
                    "OpenFIGI batch request failed (batch %d–%d)",
                    batch_start, batch_start + len(batch),
                    exc_info=True,
                )
                continue

            for cusip, result in zip(batch, results):
                data = result.get("data", [])
                if data:
                    ticker = data[0].get("ticker")
                    if ticker:
                        for h in cusip_to_holdings[cusip]:
                            h.ticker = ticker

            # Rate-limit between batches
            if batch_start + OPENFIGI_BATCH_SIZE < len(cusips):
                await asyncio.sleep(OPENFIGI_DELAY)

        resolved = sum(1 for h in holdings if h.ticker)
        logger.info(
            "OpenFIGI: resolved %d/%d holdings to tickers", resolved, len(holdings),
        )

    async def get_holdings_as_of(
        self, etf_symbol: str, as_of_date: date | None = None
    ) -> list[NPortHolding]:
        """Get fund holdings from the most recent N-PORT filing as of a given date.

        Args:
            etf_symbol: ETF symbol (e.g. "VOOG" or "VOOV").
            as_of_date: Return holdings from the latest filing on or before this date.
                If None, uses the latest filing overall.

        Returns:
            Holdings sorted by pct_val descending, or empty list on failure.
        """
        series_id = SERIES_MAP.get(etf_symbol.upper())
        if series_id is None:
            logger.warning("Unknown ETF symbol for N-PORT: %s", etf_symbol)
            return []

        filings = await self.fetch_filing_index(VANGUARD_CIK)
        if not filings:
            return []

        # Filter to filings on or before as_of_date
        if as_of_date is not None:
            filings = [f for f in filings if f.filing_date <= as_of_date]

        if not filings:
            return []

        # Iterate through filings (most recent first) until we find one
        # that matches the requested series. Vanguard files one NPORT-P per
        # fund, so most filings are for other series.
        for filing in filings:
            holdings = await self.fetch_holdings(VANGUARD_CIK, filing, series_id)
            if holdings:
                return holdings
        return []

    async def get_all_holdings_in_range(
        self, etf_symbol: str, start: date, end: date
    ) -> list[NPortHolding]:
        """Get the union of all holdings across all filings in [start, end].

        Used by BacktestContext to determine the superset of symbols for OHLCV preloading.
        Deduplicates by ticker, keeping the entry with the highest pct_val.
        """
        series_id = SERIES_MAP.get(etf_symbol.upper())
        if series_id is None:
            return []

        filings = await self.fetch_filing_index(VANGUARD_CIK)
        # Keep filings whose filing_date falls within [start, end]
        range_filings = [f for f in filings if start <= f.filing_date <= end]

        if not range_filings:
            return []

        # Fetch holdings from all filings concurrently
        tasks = [
            self.fetch_holdings(VANGUARD_CIK, f, series_id)
            for f in range_filings
        ]
        all_results = await asyncio.gather(*tasks)

        # Deduplicate by ticker, keeping highest weight
        by_ticker: dict[str, NPortHolding] = {}
        for holdings_list in all_results:
            for h in holdings_list:
                if h.ticker and (h.ticker not in by_ticker or h.pct_val > by_ticker[h.ticker].pct_val):
                    by_ticker[h.ticker] = h

        result = sorted(by_ticker.values(), key=lambda h: h.pct_val, reverse=True)
        return result
