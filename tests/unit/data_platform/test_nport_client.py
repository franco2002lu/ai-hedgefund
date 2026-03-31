"""Unit tests for the N-PORT client.

Tests mock HTTP responses to avoid real SEC EDGAR calls.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.data_platform.adapters.nport_client import (
    NPORT_NS,
    NPortClient,
    NPortFiling,
    NPortHolding,
)

# --- Fake submissions JSON (minimal) ---

_FAKE_SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["NPORT-P", "10-K", "NPORT-P", "NPORT-P"],
            "filingDate": ["2025-08-29", "2025-07-15", "2025-05-30", "2025-02-28"],
            "reportDate": ["2025-06-30", "2025-06-30", "2025-03-31", "2024-12-31"],
            "accessionNumber": [
                "0000891190-25-000001",
                "0000891190-25-000099",
                "0000891190-25-000002",
                "0000891190-25-000003",
            ],
            "primaryDocument": [
                "xslFormNPORT-P_X01/primary_doc.xml",
                "annual.htm",
                "xslFormNPORT-P_X01/primary_doc.xml",
                "primary_doc.xml",
            ],
        },
    },
}

# --- Fake N-PORT XML (with <ticker> elements — legacy format) ---

_FAKE_NPORT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="{NPORT_NS}">
  <genInfo>
    <seriesId>S000030012</seriesId>
  </genInfo>
  <invstOrSec>
    <name>Apple Inc</name>
    <cusip>037833100</cusip>
    <identifiers><ticker value="AAPL"/></identifiers>
    <balance>50000</balance>
    <valUSD>8750000</valUSD>
    <pctVal>12.5</pctVal>
    <assetCat>EC</assetCat>
  </invstOrSec>
  <invstOrSec>
    <name>Microsoft Corp</name>
    <cusip>594918104</cusip>
    <identifiers><ticker value="MSFT"/></identifiers>
    <balance>30000</balance>
    <valUSD>6300000</valUSD>
    <pctVal>9.0</pctVal>
    <assetCat>EC</assetCat>
  </invstOrSec>
  <invstOrSec>
    <name>US Treasury Bond</name>
    <cusip></cusip>
    <identifiers></identifiers>
    <balance>100000</balance>
    <valUSD>1000000</valUSD>
    <pctVal>1.4</pctVal>
    <assetCat>DBT</assetCat>
  </invstOrSec>
</edgarSubmission>
"""

# --- Fake N-PORT XML with ISIN only (current SEC format) ---

_FAKE_NPORT_XML_ISIN_ONLY = f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="{NPORT_NS}">
  <genInfo>
    <seriesId>S000030012</seriesId>
  </genInfo>
  <invstOrSec>
    <name>Apple Inc</name>
    <cusip>037833100</cusip>
    <identifiers><isin value="US0378331005"/></identifiers>
    <balance>50000</balance>
    <valUSD>8750000</valUSD>
    <pctVal>12.5</pctVal>
    <assetCat>EC</assetCat>
  </invstOrSec>
  <invstOrSec>
    <name>NVIDIA Corp</name>
    <cusip>67066G104</cusip>
    <identifiers><isin value="US67066G1040"/></identifiers>
    <balance>30000</balance>
    <valUSD>6300000</valUSD>
    <pctVal>9.0</pctVal>
    <assetCat>EC</assetCat>
  </invstOrSec>
  <invstOrSec>
    <name>US Treasury Bond</name>
    <cusip></cusip>
    <identifiers></identifiers>
    <balance>100000</balance>
    <valUSD>1000000</valUSD>
    <pctVal>1.4</pctVal>
    <assetCat>DBT</assetCat>
  </invstOrSec>
</edgarSubmission>
"""

_FAKE_NPORT_XML_WRONG_SERIES = f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="{NPORT_NS}">
  <genInfo>
    <seriesId>S000030013</seriesId>
  </genInfo>
  <invstOrSec>
    <name>Apple Inc</name>
    <cusip>037833100</cusip>
    <identifiers><ticker value="AAPL"/></identifiers>
    <balance>50000</balance>
    <valUSD>8750000</valUSD>
    <pctVal>12.5</pctVal>
  </invstOrSec>
</edgarSubmission>
"""

# --- Fake OpenFIGI response ---

_FAKE_OPENFIGI_RESPONSE = [
    {"data": [{"ticker": "AAPL", "name": "APPLE INC", "exchCode": "US"}]},
    {"data": [{"ticker": "NVDA", "name": "NVIDIA CORP", "exchCode": "US"}]},
]

_FAKE_OPENFIGI_PARTIAL_RESPONSE = [
    {"data": [{"ticker": "AAPL", "name": "APPLE INC", "exchCode": "US"}]},
    {"warning": "No identifier found."},
]


def _mock_httpx_response(status_code=200, json_data=None, text=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    if text is not None:
        resp.text = text
    return resp


class TestNPortFilingModel:
    def test_construction(self):
        f = NPortFiling(
            accession_number="0000891190-25-000001",
            filing_date=date(2025, 8, 29),
            report_date=date(2025, 6, 30),
            primary_doc="primary_doc.xml",
        )
        assert f.accession_number == "0000891190-25-000001"
        assert f.filing_date == date(2025, 8, 29)
        assert f.report_date == date(2025, 6, 30)

    def test_filing_date_ordering(self):
        f1 = NPortFiling(accession_number="a", filing_date=date(2025, 8, 29),
                         report_date=date(2025, 6, 30), primary_doc="x.xml")
        f2 = NPortFiling(accession_number="b", filing_date=date(2025, 5, 30),
                         report_date=date(2025, 3, 31), primary_doc="x.xml")
        assert f1.filing_date > f2.filing_date


class TestNPortHoldingModel:
    def test_construction_with_all_fields(self):
        h = NPortHolding(
            name="Apple Inc",
            cusip="037833100",
            ticker="AAPL",
            balance=50000.0,
            value_usd=8750000.0,
            pct_val=12.5,
            asset_cat="EC",
        )
        assert h.ticker == "AAPL"
        assert h.pct_val == pytest.approx(12.5)

    def test_optional_fields_default_to_none(self):
        h = NPortHolding(name="X", cusip="X", balance=0, value_usd=0, pct_val=0)
        assert h.ticker is None
        assert h.asset_cat is None


class TestFetchFilingIndex:
    async def test_extracts_nport_filings_only(self):
        client = NPortClient()
        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=_mock_httpx_response(
                json_data=_FAKE_SUBMISSIONS
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            filings = await client.fetch_filing_index("0000891190")

        # Should only return NPORT-P filings (3 of them)
        assert len(filings) == 3
        assert all(isinstance(f, NPortFiling) for f in filings)

    async def test_parses_xsl_prefixed_primary_doc(self):
        """SEC now returns primaryDocument with XSL prefix; should be stored as-is."""
        client = NPortClient()
        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=_mock_httpx_response(
                json_data=_FAKE_SUBMISSIONS
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            filings = await client.fetch_filing_index("0000891190")

        # First filing has XSL prefix
        assert filings[0].primary_doc == "xslFormNPORT-P_X01/primary_doc.xml"

    async def test_sorted_by_report_date_descending(self):
        client = NPortClient()
        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=_mock_httpx_response(
                json_data=_FAKE_SUBMISSIONS
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            filings = await client.fetch_filing_index("0000891190")

        report_dates = [f.report_date for f in filings]
        assert report_dates == sorted(report_dates, reverse=True)

    async def test_caches_by_cik(self):
        client = NPortClient()
        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=_mock_httpx_response(
                json_data=_FAKE_SUBMISSIONS
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.fetch_filing_index("0000891190")
            await client.fetch_filing_index("0000891190")

        # HTTP should only be called once
        assert mock_ctx.get.call_count == 1

    async def test_404_returns_empty(self):
        client = NPortClient()
        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=_mock_httpx_response(status_code=404))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            filings = await client.fetch_filing_index("9999999999")

        assert filings == []


class TestFetchHoldings:
    async def test_parses_holdings_from_xml_with_tickers(self):
        """Legacy format: XML has <ticker> elements — no OpenFIGI call needed."""
        client = NPortClient()
        filing = NPortFiling(
            accession_number="0000891190-25-000001",
            filing_date=date(2025, 8, 29),
            report_date=date(2025, 6, 30),
            primary_doc="primary_doc.xml",
        )
        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=_mock_httpx_response(
                text=_FAKE_NPORT_XML
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            holdings = await client.fetch_holdings("0000891190", filing, "S000030012")

        # Should get AAPL and MSFT (Treasury has empty CUSIP → skipped)
        assert len(holdings) == 2
        tickers = [h.ticker for h in holdings]
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    async def test_strips_xsl_prefix_from_url(self):
        """XSL-prefixed primary_doc should be stripped to fetch raw XML."""
        client = NPortClient()
        filing = NPortFiling(
            accession_number="0000891190-25-000001",
            filing_date=date(2025, 8, 29),
            report_date=date(2025, 6, 30),
            primary_doc="xslFormNPORT-P_X01/primary_doc.xml",
        )
        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=_mock_httpx_response(
                text=_FAKE_NPORT_XML
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.fetch_holdings("0000891190", filing, "S000030012")

        # Verify the URL used the raw filename, not the XSL-prefixed one
        call_url = mock_ctx.get.call_args[0][0]
        assert "xslFormNPORT-P_X01" not in call_url
        assert "primary_doc.xml" in call_url

    async def test_resolves_isin_only_holdings_via_openfigi(self):
        """Current SEC format: holdings have <isin> not <ticker>. Should call OpenFIGI."""
        client = NPortClient()
        filing = NPortFiling(
            accession_number="0000891190-25-000001",
            filing_date=date(2025, 8, 29),
            report_date=date(2025, 6, 30),
            primary_doc="primary_doc.xml",
        )
        # Mock SEC XML fetch (GET) and OpenFIGI resolve (POST)
        mock_get_resp = _mock_httpx_response(text=_FAKE_NPORT_XML_ISIN_ONLY)
        mock_post_resp = _mock_httpx_response(json_data=_FAKE_OPENFIGI_RESPONSE)

        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=mock_get_resp)
            mock_ctx.post = AsyncMock(return_value=mock_post_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            holdings = await client.fetch_holdings("0000891190", filing, "S000030012")

        assert len(holdings) == 2
        tickers = {h.ticker for h in holdings}
        assert tickers == {"AAPL", "NVDA"}

    async def test_openfigi_partial_failure_keeps_resolved(self):
        """If OpenFIGI can't resolve some CUSIPs, only resolved holdings are returned."""
        client = NPortClient()
        filing = NPortFiling(
            accession_number="0000891190-25-000001",
            filing_date=date(2025, 8, 29),
            report_date=date(2025, 6, 30),
            primary_doc="primary_doc.xml",
        )
        mock_get_resp = _mock_httpx_response(text=_FAKE_NPORT_XML_ISIN_ONLY)
        mock_post_resp = _mock_httpx_response(json_data=_FAKE_OPENFIGI_PARTIAL_RESPONSE)

        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=mock_get_resp)
            mock_ctx.post = AsyncMock(return_value=mock_post_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            holdings = await client.fetch_holdings("0000891190", filing, "S000030012")

        # Only AAPL resolved, NVDA did not
        assert len(holdings) == 1
        assert holdings[0].ticker == "AAPL"

    async def test_openfigi_total_failure_returns_empty(self):
        """If OpenFIGI is down, all CUSIP-only holdings remain unresolved → filtered out."""
        client = NPortClient()
        filing = NPortFiling(
            accession_number="0000891190-25-000001",
            filing_date=date(2025, 8, 29),
            report_date=date(2025, 6, 30),
            primary_doc="primary_doc.xml",
        )
        mock_get_resp = _mock_httpx_response(text=_FAKE_NPORT_XML_ISIN_ONLY)

        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=mock_get_resp)
            mock_ctx.post = AsyncMock(side_effect=Exception("OpenFIGI down"))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            holdings = await client.fetch_holdings("0000891190", filing, "S000030012")

        # All holdings had no ticker and OpenFIGI failed → empty
        assert len(holdings) == 0

    async def test_sorted_by_pct_val_descending(self):
        client = NPortClient()
        filing = NPortFiling(
            accession_number="0000891190-25-000001",
            filing_date=date(2025, 8, 29),
            report_date=date(2025, 6, 30),
            primary_doc="primary_doc.xml",
        )
        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=_mock_httpx_response(
                text=_FAKE_NPORT_XML
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            holdings = await client.fetch_holdings("0000891190", filing, "S000030012")

        pct_vals = [h.pct_val for h in holdings]
        assert pct_vals == sorted(pct_vals, reverse=True)

    async def test_wrong_series_returns_empty(self):
        client = NPortClient()
        filing = NPortFiling(
            accession_number="0000891190-25-000001",
            filing_date=date(2025, 8, 29),
            report_date=date(2025, 6, 30),
            primary_doc="primary_doc.xml",
        )
        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            # XML has series S000030013 but we ask for S000030012
            mock_ctx.get = AsyncMock(return_value=_mock_httpx_response(
                text=_FAKE_NPORT_XML_WRONG_SERIES
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            holdings = await client.fetch_holdings("0000891190", filing, "S000030012")

        assert holdings == []

    async def test_caches_by_accession_and_series(self):
        client = NPortClient()
        filing = NPortFiling(
            accession_number="0000891190-25-000001",
            filing_date=date(2025, 8, 29),
            report_date=date(2025, 6, 30),
            primary_doc="primary_doc.xml",
        )
        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=_mock_httpx_response(
                text=_FAKE_NPORT_XML
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.fetch_holdings("0000891190", filing, "S000030012")
            await client.fetch_holdings("0000891190", filing, "S000030012")

        assert mock_ctx.get.call_count == 1


class TestResolveTickersViaOpenfigi:
    async def test_resolves_cusips_to_tickers(self):
        client = NPortClient()
        holdings = [
            NPortHolding(name="Apple", cusip="037833100", balance=100, value_usd=1000, pct_val=10),
            NPortHolding(name="Nvidia", cusip="67066G104", balance=50, value_usd=500, pct_val=5),
        ]

        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post = AsyncMock(return_value=_mock_httpx_response(
                json_data=_FAKE_OPENFIGI_RESPONSE
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client._resolve_tickers_via_openfigi(holdings)

        assert holdings[0].ticker == "AAPL"
        assert holdings[1].ticker == "NVDA"

    async def test_partial_resolution(self):
        client = NPortClient()
        holdings = [
            NPortHolding(name="Apple", cusip="037833100", balance=100, value_usd=1000, pct_val=10),
            NPortHolding(name="Unknown", cusip="999999999", balance=50, value_usd=500, pct_val=5),
        ]

        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post = AsyncMock(return_value=_mock_httpx_response(
                json_data=_FAKE_OPENFIGI_PARTIAL_RESPONSE
            ))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client._resolve_tickers_via_openfigi(holdings)

        assert holdings[0].ticker == "AAPL"
        assert holdings[1].ticker is None  # unresolved

    async def test_api_failure_leaves_tickers_none(self):
        client = NPortClient()
        holdings = [
            NPortHolding(name="Apple", cusip="037833100", balance=100, value_usd=1000, pct_val=10),
        ]

        with patch("app.modules.data_platform.adapters.nport_client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post = AsyncMock(side_effect=Exception("OpenFIGI down"))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client._resolve_tickers_via_openfigi(holdings)

        assert holdings[0].ticker is None


class TestGetHoldingsAsOf:
    async def test_returns_holdings_for_known_etf(self):
        client = NPortClient()
        client.fetch_filing_index = AsyncMock(return_value=[
            NPortFiling(accession_number="a1", filing_date=date(2025, 8, 29),
                        report_date=date(2025, 6, 30), primary_doc="x.xml"),
        ])
        client.fetch_holdings = AsyncMock(return_value=[
            NPortHolding(name="Apple", cusip="X", ticker="AAPL",
                         balance=1000, value_usd=175000, pct_val=12.5),
        ])

        holdings = await client.get_holdings_as_of("VOOG")

        assert len(holdings) == 1
        assert holdings[0].ticker == "AAPL"

    async def test_filters_by_as_of_date(self):
        client = NPortClient()
        client.fetch_filing_index = AsyncMock(return_value=[
            NPortFiling(accession_number="a1", filing_date=date(2025, 8, 29),
                        report_date=date(2025, 6, 30), primary_doc="x.xml"),
            NPortFiling(accession_number="a2", filing_date=date(2025, 5, 30),
                        report_date=date(2025, 3, 31), primary_doc="x.xml"),
        ])
        client.fetch_holdings = AsyncMock(return_value=[
            NPortHolding(name="Apple", cusip="X", ticker="AAPL",
                         balance=1000, value_usd=175000, pct_val=12.5),
        ])

        # as_of_date before the first filing but after the second
        await client.get_holdings_as_of("VOOG", as_of_date=date(2025, 6, 1))

        # Should use filing a2 (filing_date=2025-05-30)
        client.fetch_holdings.assert_called_once()
        call_filing = client.fetch_holdings.call_args[0][1]
        assert call_filing.accession_number == "a2"

    async def test_none_as_of_tries_latest_first(self):
        """With as_of_date=None, iterates filings starting from the latest."""
        client = NPortClient()
        client.fetch_filing_index = AsyncMock(return_value=[
            NPortFiling(accession_number="a1", filing_date=date(2025, 8, 29),
                        report_date=date(2025, 6, 30), primary_doc="x.xml"),
            NPortFiling(accession_number="a2", filing_date=date(2025, 5, 30),
                        report_date=date(2025, 3, 31), primary_doc="x.xml"),
        ])
        expected = [NPortHolding(name="Apple", cusip="X", ticker="AAPL",
                                 balance=1000, value_usd=175000, pct_val=12.5)]
        client.fetch_holdings = AsyncMock(return_value=expected)

        result = await client.get_holdings_as_of("VOOG", as_of_date=None)

        # Should try the latest filing first
        first_call_filing = client.fetch_holdings.call_args_list[0][0][1]
        assert first_call_filing.accession_number == "a1"
        assert result == expected

    async def test_iterates_filings_when_series_mismatch(self):
        """If the latest filing doesn't match the series, tries the next one."""
        client = NPortClient()
        client.fetch_filing_index = AsyncMock(return_value=[
            NPortFiling(accession_number="a1", filing_date=date(2025, 8, 29),
                        report_date=date(2025, 6, 30), primary_doc="x.xml"),
            NPortFiling(accession_number="a2", filing_date=date(2025, 5, 30),
                        report_date=date(2025, 3, 31), primary_doc="x.xml"),
        ])
        expected = [NPortHolding(name="Apple", cusip="X", ticker="AAPL",
                                 balance=1000, value_usd=175000, pct_val=12.5)]
        # First filing returns empty (wrong series), second returns holdings
        client.fetch_holdings = AsyncMock(side_effect=[[], expected])

        result = await client.get_holdings_as_of("VOOG", as_of_date=None)

        assert client.fetch_holdings.call_count == 2
        assert result == expected

    async def test_unknown_etf_returns_empty(self):
        client = NPortClient()
        holdings = await client.get_holdings_as_of("UNKNOWN_ETF")
        assert holdings == []


class TestGetAllHoldingsInRange:
    async def test_deduplicates_by_ticker(self):
        client = NPortClient()
        client.fetch_filing_index = AsyncMock(return_value=[
            NPortFiling(accession_number="a1", filing_date=date(2025, 8, 29),
                        report_date=date(2025, 6, 30), primary_doc="x.xml"),
            NPortFiling(accession_number="a2", filing_date=date(2025, 5, 30),
                        report_date=date(2025, 3, 31), primary_doc="x.xml"),
        ])
        # Both filings have AAPL but with different weights
        client.fetch_holdings = AsyncMock(side_effect=[
            [
                NPortHolding(name="Apple", cusip="X", ticker="AAPL",
                             balance=1000, value_usd=175000, pct_val=12.5),
                NPortHolding(name="Microsoft", cusip="Y", ticker="MSFT",
                             balance=500, value_usd=100000, pct_val=9.0),
            ],
            [
                NPortHolding(name="Apple", cusip="X", ticker="AAPL",
                             balance=1100, value_usd=185000, pct_val=11.0),
                NPortHolding(name="Nvidia", cusip="Z", ticker="NVDA",
                             balance=200, value_usd=80000, pct_val=6.0),
            ],
        ])

        holdings = await client.get_all_holdings_in_range(
            "VOOG", date(2025, 1, 1), date(2025, 12, 31)
        )

        tickers = [h.ticker for h in holdings]
        assert len(tickers) == 3  # AAPL, MSFT, NVDA (no duplicates)
        assert set(tickers) == {"AAPL", "MSFT", "NVDA"}

    async def test_keeps_highest_weight_on_dedup(self):
        client = NPortClient()
        client.fetch_filing_index = AsyncMock(return_value=[
            NPortFiling(accession_number="a1", filing_date=date(2025, 8, 29),
                        report_date=date(2025, 6, 30), primary_doc="x.xml"),
            NPortFiling(accession_number="a2", filing_date=date(2025, 5, 30),
                        report_date=date(2025, 3, 31), primary_doc="x.xml"),
        ])
        client.fetch_holdings = AsyncMock(side_effect=[
            [NPortHolding(name="Apple", cusip="X", ticker="AAPL",
                          balance=1000, value_usd=175000, pct_val=12.5)],
            [NPortHolding(name="Apple", cusip="X", ticker="AAPL",
                          balance=1100, value_usd=185000, pct_val=11.0)],
        ])

        holdings = await client.get_all_holdings_in_range(
            "VOOG", date(2025, 1, 1), date(2025, 12, 31)
        )

        assert len(holdings) == 1
        assert holdings[0].pct_val == pytest.approx(12.5)

    async def test_filters_filings_by_date_range(self):
        client = NPortClient()
        client.fetch_filing_index = AsyncMock(return_value=[
            NPortFiling(accession_number="a1", filing_date=date(2025, 8, 29),
                        report_date=date(2025, 6, 30), primary_doc="x.xml"),
            NPortFiling(accession_number="a2", filing_date=date(2025, 5, 30),
                        report_date=date(2025, 3, 31), primary_doc="x.xml"),
            NPortFiling(accession_number="a3", filing_date=date(2024, 2, 28),
                        report_date=date(2023, 12, 31), primary_doc="x.xml"),
        ])
        client.fetch_holdings = AsyncMock(return_value=[])

        await client.get_all_holdings_in_range(
            "VOOG", date(2025, 1, 1), date(2025, 12, 31)
        )

        # Only 2 filings in range (a3 is before 2025-01-01)
        assert client.fetch_holdings.call_count == 2

    async def test_unknown_etf_returns_empty(self):
        client = NPortClient()
        result = await client.get_all_holdings_in_range(
            "UNKNOWN", date(2025, 1, 1), date(2025, 12, 31)
        )
        assert result == []
