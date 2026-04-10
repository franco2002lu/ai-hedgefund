"""Unit tests for CSV-based universe provider with Yahoo Finance hydration."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from app.modules.equities.models import UniverseStock
from app.modules.equities.universe.provider import UniverseProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(directory: str, filename: str, symbols: list[str]) -> str:
    """Write a minimal universe CSV and return its path."""
    path = os.path.join(directory, filename)
    with open(path, "w") as f:
        f.write("symbol\n")
        for s in symbols:
            f.write(f"{s}\n")
    return path


def _make_company_facts(symbol: str, sector: str = "Technology", industry: str = "Software") -> dict:
    """Return a mock company facts dict matching YahooFinanceAdapter.get_company_facts()."""
    return {
        "symbol": symbol,
        "name": f"{symbol} Inc.",
        "sector": sector,
        "industry": industry,
        "country": "United States",
        "website": f"https://{symbol.lower()}.com",
        "description": f"{symbol} is a company.",
        "employees": 10000,
        "exchange": "NMS",
        "currency": "USD",
    }


def _mock_data_service(facts_by_symbol: dict[str, dict]) -> AsyncMock:
    """Create a mock DataPlatformService whose get_company_facts returns per-symbol data."""
    svc = AsyncMock()

    async def _get_facts(symbol: str) -> dict:
        if symbol in facts_by_symbol:
            return facts_by_symbol[symbol]
        raise Exception(f"No data for {symbol}")

    svc.get_company_facts = AsyncMock(side_effect=_get_facts)
    return svc


def _mock_yf_ticker(holdings: dict, index: list[str]) -> MagicMock:
    """Create a mock yfinance Ticker for fallback testing."""
    df = pd.DataFrame(holdings, index=index)
    mock_ticker = MagicMock()
    mock_ticker.funds_data.top_holdings = df
    return mock_ticker


# ---------------------------------------------------------------------------
# CSV Loading + Hydration
# ---------------------------------------------------------------------------


class TestCSVLoadingAndHydration:
    async def test_loads_from_csv(self):
        """Provider reads symbols from CSV and returns UniverseStock objects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(tmpdir, "growth_universe.csv", ["AAPL", "MSFT"])
            facts = {
                "AAPL": _make_company_facts("AAPL", "Technology", "Consumer Electronics"),
                "MSFT": _make_company_facts("MSFT", "Technology", "Software"),
            }
            data_service = _mock_data_service(facts)
            provider = UniverseProvider(data_service=data_service, csv_dir=tmpdir)

            result = await provider.get_holdings("growth")

        assert len(result) == 2
        assert all(isinstance(s, UniverseStock) for s in result)
        symbols = {s.symbol for s in result}
        assert symbols == {"AAPL", "MSFT"}

    async def test_hydrates_company_name_sector_industry(self):
        """Hydration populates company_name, sector, and industry from get_company_facts()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(tmpdir, "growth_universe.csv", ["AAPL"])
            facts = {"AAPL": _make_company_facts("AAPL", "Technology", "Consumer Electronics")}
            data_service = _mock_data_service(facts)
            provider = UniverseProvider(data_service=data_service, csv_dir=tmpdir)

            result = await provider.get_holdings("growth")

        assert len(result) == 1
        stock = result[0]
        assert stock.symbol == "AAPL"
        assert stock.company_name == "AAPL Inc."
        assert stock.sector == "Technology"
        assert stock.industry == "Consumer Electronics"
        assert stock.weight == 0.0  # default when loaded from CSV

    async def test_caches_hydrated_results(self):
        """Second call returns cached data without re-calling get_company_facts()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(tmpdir, "growth_universe.csv", ["AAPL"])
            facts = {"AAPL": _make_company_facts("AAPL")}
            data_service = _mock_data_service(facts)
            provider = UniverseProvider(data_service=data_service, csv_dir=tmpdir)

            result1 = await provider.get_holdings("growth")
            result2 = await provider.get_holdings("growth")

        assert result1 == result2
        # get_company_facts should only be called once (first call); second uses cache
        assert data_service.get_company_facts.call_count == 1

    async def test_concurrent_hydration(self):
        """Multiple symbols are hydrated (all get_company_facts calls are made)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]
            _write_csv(tmpdir, "value_universe.csv", symbols)
            facts = {s: _make_company_facts(s) for s in symbols}
            data_service = _mock_data_service(facts)
            provider = UniverseProvider(data_service=data_service, csv_dir=tmpdir)

            result = await provider.get_holdings("value")

        assert len(result) == 5
        assert data_service.get_company_facts.call_count == 5

    async def test_handles_hydration_failure_gracefully(self):
        """If get_company_facts() fails for one symbol, other symbols still returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(tmpdir, "growth_universe.csv", ["AAPL", "BAD_TICKER", "MSFT"])
            facts = {
                "AAPL": _make_company_facts("AAPL"),
                "MSFT": _make_company_facts("MSFT"),
                # BAD_TICKER not in facts → will raise exception
            }
            data_service = _mock_data_service(facts)
            provider = UniverseProvider(data_service=data_service, csv_dir=tmpdir)

            result = await provider.get_holdings("growth")

        # BAD_TICKER should be skipped; AAPL and MSFT should be returned
        assert len(result) == 2
        symbols = {s.symbol for s in result}
        assert symbols == {"AAPL", "MSFT"}

    async def test_skips_blank_lines_and_whitespace(self):
        """CSV parser handles blank lines and whitespace around symbols."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "growth_universe.csv")
            with open(path, "w") as f:
                f.write("symbol\n AAPL \n\nMSFT\n  \n")
            facts = {
                "AAPL": _make_company_facts("AAPL"),
                "MSFT": _make_company_facts("MSFT"),
            }
            data_service = _mock_data_service(facts)
            provider = UniverseProvider(data_service=data_service, csv_dir=tmpdir)

            result = await provider.get_holdings("growth")

        assert len(result) == 2
        symbols = {s.symbol for s in result}
        assert symbols == {"AAPL", "MSFT"}


# ---------------------------------------------------------------------------
# Fallback to yfinance
# ---------------------------------------------------------------------------


class TestYahooFinanceFallback:
    async def test_fallback_to_yfinance_when_no_csv(self):
        """When CSV file doesn't exist, falls back to yfinance top_holdings."""
        holdings = {
            "Name": ["Apple Inc.", "Microsoft Corp."],
            "Holding Percent": [0.07, 0.06],
        }
        mock_ticker = _mock_yf_ticker(holdings, index=["AAPL", "MSFT"])

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "app.modules.equities.universe.provider.yf.Ticker",
                return_value=mock_ticker,
            ),
        ):
            # No CSV written — directory is empty
            provider = UniverseProvider(data_service=AsyncMock(), csv_dir=tmpdir)
            result = await provider.get_holdings("growth")

        assert len(result) == 2
        assert result[0].symbol == "AAPL"
        assert result[0].company_name == "Apple Inc."

    async def test_fallback_returns_empty_on_api_failure(self):
        """When no CSV and yfinance fails, returns empty list."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "app.modules.equities.universe.provider.yf.Ticker",
                side_effect=Exception("API down"),
            ),
        ):
            provider = UniverseProvider(data_service=AsyncMock(), csv_dir=tmpdir)
            result = await provider.get_holdings("growth")

        assert result == []


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    async def test_refresh_rehydrates(self):
        """refresh() bypasses cache and re-fetches from CSV + hydration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(tmpdir, "growth_universe.csv", ["AAPL"])
            facts = {"AAPL": _make_company_facts("AAPL")}
            data_service = _mock_data_service(facts)
            provider = UniverseProvider(data_service=data_service, csv_dir=tmpdir)

            result1 = await provider.get_holdings("growth")
            assert len(result1) == 1

            # Add a second symbol to the CSV
            _write_csv(tmpdir, "growth_universe.csv", ["AAPL", "MSFT"])
            facts["MSFT"] = _make_company_facts("MSFT")

            result2 = await provider.refresh("growth")
            assert len(result2) == 2

            # Subsequent get_holdings uses cache
            result3 = await provider.get_holdings("growth")
            assert len(result3) == 2
            # 1 (initial) + 2 (refresh: AAPL + MSFT) = 3 calls total
            assert data_service.get_company_facts.call_count == 3

    async def test_refresh_updates_cache_for_subsequent_calls(self):
        """After refresh, get_holdings returns updated data without another API call."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(tmpdir, "growth_universe.csv", ["AAPL"])
            facts = {"AAPL": _make_company_facts("AAPL")}
            data_service = _mock_data_service(facts)
            provider = UniverseProvider(data_service=data_service, csv_dir=tmpdir)

            await provider.get_holdings("growth")
            call_count_after_first = data_service.get_company_facts.call_count

            await provider.get_holdings("growth")  # cache hit
            assert data_service.get_company_facts.call_count == call_count_after_first


# ---------------------------------------------------------------------------
# N-PORT Snapshot Hydration
# ---------------------------------------------------------------------------


def _write_nport_snapshot(tmpdir: str, etf: str, report_date: str, holdings: list[tuple[str, str, float]]) -> None:
    """Write an N-PORT snapshot CSV: symbol,name,weight."""
    snapshot_dir = os.path.join(tmpdir, "nport_snapshots")
    os.makedirs(snapshot_dir, exist_ok=True)
    path = os.path.join(snapshot_dir, f"{etf.lower()}_{report_date}.csv")
    with open(path, "w") as f:
        f.write("symbol,name,weight\n")
        for sym, name, weight in holdings:
            f.write(f"{sym},{name},{weight}\n")


class TestNPortSnapshotHydration:
    async def test_snapshot_stocks_have_sector_populated(self):
        """N-PORT snapshot stocks should be hydrated with sector from get_company_facts().

        This is the critical fix: previously, _load_snapshot returned stocks with
        sector=None, and they were never hydrated.
        """
        from datetime import date

        with tempfile.TemporaryDirectory() as tmpdir:
            _write_nport_snapshot(
                tmpdir,
                "VOOG",
                "2025-03-31",
                [
                    ("AAPL", "Apple Inc.", 8.0),
                    ("MSFT", "Microsoft Corp.", 7.0),
                ],
            )
            facts = {
                "AAPL": _make_company_facts("AAPL", "Technology", "Consumer Electronics"),
                "MSFT": _make_company_facts("MSFT", "Technology", "Software"),
            }
            data_service = _mock_data_service(facts)
            provider = UniverseProvider(data_service=data_service, csv_dir=tmpdir)

            result = await provider.get_holdings("growth", as_of_date=date(2025, 6, 15))

        assert len(result) == 2
        for stock in result:
            assert stock.sector == "Technology", (
                f"{stock.symbol} sector should be 'Technology' from hydration, got {stock.sector!r}"
            )

    async def test_snapshot_hydration_preserves_weight(self):
        """Hydration should keep the weight from the N-PORT CSV."""
        from datetime import date

        with tempfile.TemporaryDirectory() as tmpdir:
            _write_nport_snapshot(
                tmpdir,
                "VOOG",
                "2025-03-31",
                [
                    ("AAPL", "Apple Inc.", 8.0),
                ],
            )
            facts = {"AAPL": _make_company_facts("AAPL")}
            data_service = _mock_data_service(facts)
            provider = UniverseProvider(data_service=data_service, csv_dir=tmpdir)

            result = await provider.get_holdings("growth", as_of_date=date(2025, 6, 15))

        assert len(result) == 1
        assert result[0].weight == 8.0
