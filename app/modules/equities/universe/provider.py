from __future__ import annotations

import asyncio
import csv
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import yfinance as yf

from app.modules.equities.models import UniverseStock

logger = logging.getLogger(__name__)

# Default CSV directory relative to project root
_DEFAULT_CSV_DIR = str(Path(__file__).resolve().parents[4] / "data" / "universes")


class UniverseProvider:
    """Loads stock universe from CSV files and hydrates metadata from Yahoo Finance.

    Primary path: reads symbols from ``{csv_dir}/{branch_name}_universe.csv``,
    then concurrently fetches company_name / sector / industry via
    ``data_service.get_company_facts()``.

    Fallback: if no CSV exists for the branch, falls back to yfinance
    ``Ticker.funds_data.top_holdings`` (limited to ~10 stocks).
    """

    def __init__(
        self,
        data_service=None,
        csv_dir: str | None = None,
    ) -> None:
        self.data_service = data_service
        self.csv_dir = csv_dir or _DEFAULT_CSV_DIR
        self._cache: dict[str, tuple[list[UniverseStock], datetime]] = {}
        self._cache_ttl_days = 90

    async def get_holdings(self, branch_name: str) -> list[UniverseStock]:
        """Returns the stock universe for a branch (e.g. 'growth' or 'value').

        Checks the 90-day in-memory cache first, then loads from CSV + hydrates.
        """
        cached = self._cache.get(branch_name)
        if cached:
            holdings, cached_at = cached
            age_days = (datetime.now(UTC) - cached_at).days
            if age_days < self._cache_ttl_days:
                return holdings
        return await self.refresh(branch_name)

    async def refresh(self, branch_name: str) -> list[UniverseStock]:
        """Force-refresh universe from CSV + hydration (or yfinance fallback)."""
        csv_path = os.path.join(self.csv_dir, f"{branch_name}_universe.csv")

        if os.path.isfile(csv_path):
            stocks = await self._load_and_hydrate(csv_path)
        else:
            logger.warning("No CSV at %s — falling back to yfinance top_holdings", csv_path)
            stocks = await self._fallback_yfinance(branch_name)

        self._cache[branch_name] = (stocks, datetime.now(UTC))
        logger.info("Universe '%s': %d stocks", branch_name, len(stocks))
        return stocks

    # ------------------------------------------------------------------
    # CSV loading + hydration
    # ------------------------------------------------------------------

    def _load_symbols(self, csv_path: str) -> list[str]:
        """Read symbols from a single-column CSV (header: 'symbol')."""
        symbols: list[str] = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = row.get("symbol", "").strip()
                if sym:
                    symbols.append(sym)
        return symbols

    async def _hydrate_one(self, symbol: str) -> UniverseStock | None:
        """Fetch company facts for a single symbol and build a UniverseStock."""
        if self.data_service is None:
            return UniverseStock(symbol=symbol, company_name=symbol)
        try:
            facts = await self.data_service.get_company_facts(symbol)
            return UniverseStock(
                symbol=symbol,
                company_name=facts.get("name", symbol),
                sector=facts.get("sector"),
                industry=facts.get("industry"),
            )
        except Exception:
            logger.warning("Hydration failed for %s — skipping", symbol)
            return None

    async def _load_and_hydrate(self, csv_path: str) -> list[UniverseStock]:
        """Load symbols from CSV and hydrate concurrently."""
        symbols = self._load_symbols(csv_path)
        if not symbols:
            return []

        sem = asyncio.Semaphore(10)

        async def _limited(sym: str) -> UniverseStock | None:
            async with sem:
                return await self._hydrate_one(sym)

        results = await asyncio.gather(*(_limited(s) for s in symbols))
        return [r for r in results if r is not None]

    # ------------------------------------------------------------------
    # yfinance fallback (top 10 holdings only)
    # ------------------------------------------------------------------

    async def _fallback_yfinance(self, branch_name: str) -> list[UniverseStock]:
        """Legacy path: fetch ETF top_holdings from yfinance."""
        # Map branch name to ETF symbol for fallback
        etf_map = {"growth": "VOOG", "value": "VOOV"}
        etf_symbol = etf_map.get(branch_name, branch_name)

        try:
            ticker = yf.Ticker(etf_symbol)
            funds_data = ticker.funds_data
            holdings_df = funds_data.top_holdings
            stocks: list[UniverseStock] = []
            if holdings_df is not None and not holdings_df.empty:
                for symbol, row in holdings_df.iterrows():
                    stocks.append(
                        UniverseStock(
                            symbol=str(symbol),
                            company_name=row.get("Name", str(symbol)),
                            weight=float(row.get("Holding Percent", 0.0)),
                        )
                    )
            return stocks
        except Exception as e:
            logger.warning("yfinance fallback failed for %s: %s", branch_name, e)
            cached = self._cache.get(branch_name)
            if cached:
                return cached[0]
            return []
