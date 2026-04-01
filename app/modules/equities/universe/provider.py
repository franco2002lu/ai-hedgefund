from __future__ import annotations

import asyncio
import csv
import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path

import yfinance as yf

from app.modules.equities.models import UniverseStock

logger = logging.getLogger(__name__)

# Default CSV directory relative to project root
_DEFAULT_CSV_DIR = str(Path(__file__).resolve().parents[4] / "data" / "universes")

# Branch name → ETF symbol (used for snapshot lookup and yfinance fallback)
_ETF_MAP = {"growth": "VOOG", "value": "VOOV"}


class UniverseProvider:
    """Loads stock universe from CSV files and hydrates metadata from Yahoo Finance.

    For backtests with ``as_of_date``, loads the nearest pre-baked N-PORT snapshot
    from ``data/universes/nport_snapshots/{etf}_{report_date}.csv``.

    Otherwise loads from ``data/universes/{branch_name}_universe.csv`` and hydrates
    with company metadata.  Falls back to yfinance top_holdings if no CSV exists.
    """

    def __init__(
        self,
        data_service=None,
        csv_dir: str | None = None,
        top_n: int | None = None,
    ) -> None:
        self.data_service = data_service
        self.csv_dir = csv_dir or _DEFAULT_CSV_DIR
        self.top_n = top_n
        self._cache: dict[str, tuple[list[UniverseStock], datetime]] = {}
        self._cache_ttl_days = 90

    async def get_holdings(
        self,
        branch_name: str,
        *,
        as_of_date: date | None = None,
    ) -> list[UniverseStock]:
        """Returns the stock universe for a branch (e.g. 'growth' or 'value').

        Args:
            branch_name: Branch identifier (e.g. 'growth', 'test_growth').
            as_of_date: If provided, loads the nearest N-PORT snapshot on or
                before this date. Otherwise loads the branch CSV.

        Checks the 90-day in-memory cache first, then loads from disk.
        """
        cache_key = f"{branch_name}:{as_of_date}:{self.top_n}"
        cached = self._cache.get(cache_key)
        if cached:
            holdings, cached_at = cached
            age_days = (datetime.now(UTC) - cached_at).days
            if age_days < self._cache_ttl_days:
                return holdings
        return await self.refresh(branch_name, as_of_date=as_of_date)

    async def refresh(
        self,
        branch_name: str,
        *,
        as_of_date: date | None = None,
    ) -> list[UniverseStock]:
        """Force-refresh universe from snapshot CSV, branch CSV, or yfinance fallback."""

        # 1. Try N-PORT snapshot (for backtest path with as_of_date)
        if as_of_date is not None:
            snapshot_path = self._find_snapshot_csv(branch_name, as_of_date)
            if snapshot_path:
                stocks = self._load_snapshot(snapshot_path, top_n=self.top_n)
                if stocks:
                    cache_key = f"{branch_name}:{as_of_date}:{self.top_n}"
                    self._cache[cache_key] = (stocks, datetime.now(UTC))
                    logger.info("Universe '%s' as_of %s: %d stocks from snapshot %s",
                                branch_name, as_of_date, len(stocks), snapshot_path.name)
                    return stocks

        # 2. Load branch CSV
        csv_path = os.path.join(self.csv_dir, f"{branch_name}_universe.csv")
        if os.path.isfile(csv_path):
            stocks = await self._load_and_hydrate(csv_path)
        else:
            logger.warning("No CSV at %s — falling back to yfinance top_holdings", csv_path)
            stocks = await self._fallback_yfinance(branch_name)

        if self.top_n is not None:
            stocks.sort(key=lambda s: s.weight, reverse=True)
            stocks = stocks[:self.top_n]

        cache_key = f"{branch_name}:{as_of_date}:{self.top_n}"
        self._cache[cache_key] = (stocks, datetime.now(UTC))
        logger.info("Universe '%s': %d stocks", branch_name, len(stocks))
        return stocks

    # ------------------------------------------------------------------
    # N-PORT snapshot loading
    # ------------------------------------------------------------------

    def _find_snapshot_csv(self, branch_name: str, as_of_date: date) -> Path | None:
        """Find the most recent snapshot CSV on or before as_of_date.

        Snapshots live at ``data/universes/nport_snapshots/{etf}_{report_date}.csv``.
        """
        clean = branch_name.removeprefix("test_")
        etf_symbol = _ETF_MAP.get(clean)
        if etf_symbol is None:
            return None

        snapshot_dir = Path(self.csv_dir) / "nport_snapshots"
        if not snapshot_dir.is_dir():
            return None

        prefix = etf_symbol.lower() + "_"
        best_path: Path | None = None
        best_date: date | None = None

        for entry in snapshot_dir.iterdir():
            if not entry.name.startswith(prefix) or not entry.name.endswith(".csv"):
                continue
            # Parse report_date from filename: voog_2025-03-31.csv
            date_str = entry.stem[len(prefix):]
            try:
                report_date = date.fromisoformat(date_str)
            except ValueError:
                continue
            if report_date <= as_of_date and (best_date is None or report_date > best_date):
                best_date = report_date
                best_path = entry

        return best_path

    @staticmethod
    def _load_snapshot(path: Path, top_n: int | None = None) -> list[UniverseStock]:
        """Load a snapshot CSV (columns: symbol, name, weight).

        Args:
            path: Path to the snapshot CSV file.
            top_n: If set, return only the top N holdings by weight.
        """
        stocks: list[UniverseStock] = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = row.get("symbol", "").strip()
                if not sym:
                    continue
                stocks.append(UniverseStock(
                    symbol=sym,
                    company_name=row.get("name", sym),
                    weight=float(row.get("weight", 0.0)),
                ))
        if top_n is not None:
            stocks.sort(key=lambda s: s.weight, reverse=True)
            stocks = stocks[:top_n]
        return stocks

    def get_snapshot_symbols(self, branch_name: str, start: date, end: date) -> list[str]:
        """Return the superset of symbols across all snapshots in [start, end].

        Used by BacktestContext to determine which symbols need OHLCV preloading.
        """
        clean = branch_name.removeprefix("test_")
        etf_symbol = _ETF_MAP.get(clean)
        if etf_symbol is None:
            return []

        snapshot_dir = Path(self.csv_dir) / "nport_snapshots"
        if not snapshot_dir.is_dir():
            return []

        prefix = etf_symbol.lower() + "_"
        all_symbols: set[str] = set()

        for entry in snapshot_dir.iterdir():
            if not entry.name.startswith(prefix) or not entry.name.endswith(".csv"):
                continue
            date_str = entry.stem[len(prefix):]
            try:
                report_date = date.fromisoformat(date_str)
            except ValueError:
                continue
            if start <= report_date <= end:
                stocks = self._load_snapshot(entry, top_n=self.top_n)
                all_symbols.update(s.symbol for s in stocks)

        return sorted(all_symbols)

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
        clean = branch_name.removeprefix("test_")
        etf_symbol = _ETF_MAP.get(clean, branch_name)

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
