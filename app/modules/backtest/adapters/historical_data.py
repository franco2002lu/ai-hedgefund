"""Historical price store and data adapter for backtesting."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd
import yfinance as yf

from app.common.interfaces.fundamentals import FundamentalsAdapter
from app.common.interfaces.price_data import PriceBar, PriceDataAdapter
from app.modules.backtest.time_provider import BacktestTimeProvider

if TYPE_CHECKING:
    from app.modules.backtest.adapters.historical_fundamentals import HistoricalFundamentalsStore

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
BATCH_TIMEOUT = 30  # seconds


class BacktestDataError(Exception):
    """Raised when preload fails catastrophically (< 50% of symbols loaded)."""


class HistoricalPriceStore:
    """In-memory store for historical price bars with O(1) point lookups."""

    def __init__(self) -> None:
        self._data: dict[str, dict[date, PriceBar]] = {}
        self._sorted: dict[str, list[PriceBar]] = {}
        self.failed_symbols: set[str] = set()

    def get_bar(self, symbol: str, dt: date) -> PriceBar | None:
        return self._data.get(symbol, {}).get(dt)

    def get_bars(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        bars = self._sorted.get(symbol, [])
        return [b for b in bars if start <= b.timestamp <= end]

    def get_close(self, symbol: str, dt: date) -> float | None:
        bar = self.get_bar(symbol, dt)
        return bar.close if bar is not None else None

    def get_latest_close(self, symbol: str, as_of: date) -> float | None:
        bars = self._sorted.get(symbol, [])
        result: float | None = None
        for bar in bars:
            if bar.timestamp <= as_of:
                result = bar.close
            else:
                break
        return result

    def get_next_trading_day(self, symbol: str, after: date) -> date | None:
        """Return the first trading day strictly after `after` for the given symbol."""
        bars = self._sorted.get(symbol, [])
        for bar in bars:
            if bar.timestamp > after:
                return bar.timestamp
        return None

    def get_open(self, symbol: str, dt: date) -> float | None:
        """Return the open price for a symbol on a specific date."""
        bar = self.get_bar(symbol, dt)
        return bar.open if bar is not None else None

    def get_latest_close_with_staleness(
        self, symbol: str, as_of: date, max_stale_days: int = 10,
    ) -> float | None:
        """Return the latest close price, or None if data is stale beyond threshold.

        A stock with no price data for more than `max_stale_days` trading days
        is considered delisted/halted. Returns None so the caller can force-
        liquidate the position at the last known price.
        """
        bars = self._sorted.get(symbol, [])
        result_bar: PriceBar | None = None
        for bar in bars:
            if bar.timestamp <= as_of:
                result_bar = bar
            else:
                break
        if result_bar is None:
            return None
        # Count trading days between last bar and as_of
        stale_days = sum(
            1 for b_date in self.get_trading_days(result_bar.timestamp, as_of)
            if b_date > result_bar.timestamp
        )
        if stale_days > max_stale_days:
            return None
        return result_bar.close

    async def preload(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        lookback_days: int = 252,
    ) -> None:
        """Download OHLCV from yfinance in batches and populate the store."""
        if not symbols:
            return

        actual_start = start_date - timedelta(days=int(lookback_days * 1.5))
        actual_end = end_date + timedelta(days=1)
        batches = [symbols[i : i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
        total = len(batches)

        for i, batch in enumerate(batches):
            logger.info(
                "Preloading batch %d/%d (symbols %d-%d)",
                i + 1,
                total,
                i * BATCH_SIZE + 1,
                min((i + 1) * BATCH_SIZE, len(symbols)),
            )
            for attempt in range(2):
                try:
                    loop = asyncio.get_running_loop()
                    df = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda b=batch: yf.download(
                                b,
                                start=actual_start.isoformat(),
                                end=actual_end.isoformat(),
                                auto_adjust=True,
                                progress=False,
                                group_by="ticker",
                            ),
                        ),
                        timeout=BATCH_TIMEOUT,
                    )
                    self._ingest_dataframe(df, batch)
                    break
                except (TimeoutError, Exception) as exc:
                    if attempt == 1:
                        logger.warning("Batch %d/%d failed after retry: %s", i + 1, total, exc)
                        for sym in batch:
                            self.failed_symbols.add(sym)
                    else:
                        await asyncio.sleep(2**attempt)

        success = len(symbols) - len(self.failed_symbols)
        logger.info("Preload complete: %d/%d symbols loaded", success, len(symbols))
        if success < len(symbols) * 0.5:
            raise BacktestDataError(f"Catastrophic preload failure: only {success}/{len(symbols)} symbols loaded")

    def _ingest_dataframe(self, df: pd.DataFrame, batch: list[str]) -> None:
        """Parse yfinance DataFrame and populate _data and _sorted."""
        if df is None or df.empty:
            for sym in batch:
                self.failed_symbols.add(sym)
            return

        if isinstance(df.columns, pd.MultiIndex):
            # Multi-ticker with group_by="ticker": columns are (ticker, price_type)
            available_tickers = set(df.columns.get_level_values(0))
            for sym in batch:
                if sym not in available_tickers:
                    self.failed_symbols.add(sym)
                    continue
                try:
                    sym_df = df[sym]
                    self._ingest_symbol(sym, sym_df)
                except Exception:
                    self.failed_symbols.add(sym)
        else:
            # Single ticker: flat columns
            if len(batch) == 1:
                self._ingest_symbol(batch[0], df)
            else:
                for sym in batch:
                    self.failed_symbols.add(sym)

    def _ingest_symbol(self, symbol: str, sym_df: pd.DataFrame) -> None:
        """Convert a single-symbol DataFrame into PriceBar dicts."""
        bars_by_date: dict[date, PriceBar] = {}
        for ts, row in sym_df.iterrows():
            try:
                d = ts.date() if hasattr(ts, "date") else ts
                close_val = float(row["Close"])
                if math.isnan(close_val):
                    continue
                bar = PriceBar(
                    timestamp=d,
                    open=float(row.get("Open", close_val)),
                    high=float(row.get("High", close_val)),
                    low=float(row.get("Low", close_val)),
                    close=close_val,
                    volume=int(row.get("Volume", 0)) if not math.isnan(row.get("Volume", 0)) else 0,
                )
                bars_by_date[d] = bar
            except Exception:
                continue
        if bars_by_date:
            self._data[symbol] = bars_by_date
            self._sorted[symbol] = sorted(bars_by_date.values(), key=lambda b: b.timestamp)
        else:
            self.failed_symbols.add(symbol)

    def get_first_trading_date(self, symbol: str) -> date | None:
        """Return the earliest date with data for a symbol."""
        bars = self._sorted.get(symbol, [])
        return bars[0].timestamp if bars else None

    def get_data_coverage(self, symbol: str, start: date, end: date) -> float:
        """Return fraction of trading days with data for a symbol in [start, end]."""
        bars = self.get_bars(symbol, start, end)
        if not bars:
            return 0.0
        all_trading_days = self.get_trading_days(start, end)
        return len(bars) / len(all_trading_days) if all_trading_days else 0.0

    def get_trading_days(self, start: date, end: date) -> list[date]:
        all_dates: set[date] = set()
        for bars in self._sorted.values():
            for bar in bars:
                if start <= bar.timestamp <= end and bar.timestamp.weekday() < 5:
                    all_dates.add(bar.timestamp)
        return sorted(all_dates)


class HistoricalDataAdapter(PriceDataAdapter, FundamentalsAdapter):
    """Adapter that serves historical data clamped to the simulated date.

    Supports two modes:
    - Point-in-time mode (fundamentals_store provided): Uses SEC EDGAR
      quarterly filings indexed by filing date for true point-in-time metrics.
    - Legacy cache mode (fundamentals_cache/facts_cache provided): Uses a
      static snapshot for backward compatibility.
    """

    name = "backtest_historical"

    def __init__(
        self,
        store: HistoricalPriceStore,
        time_provider: BacktestTimeProvider,
        fundamentals_cache: dict | None = None,
        facts_cache: dict | None = None,
        *,
        fundamentals_store: HistoricalFundamentalsStore | None = None,
    ) -> None:
        self._store = store
        self._time_provider = time_provider
        self._fundamentals_store = fundamentals_store
        # Legacy caches (used only if fundamentals_store is None)
        self._fundamentals_cache = fundamentals_cache or {}
        self._facts_cache = facts_cache or {}

    async def get_prices(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> list[PriceBar]:
        clamped_end = min(end_date, self._time_provider.today())
        return self._store.get_bars(symbol, start_date, clamped_end)

    async def get_current_price(self, symbol: str) -> float | None:
        return self._store.get_latest_close(symbol, self._time_provider.today())

    def supported_asset_classes(self) -> list[str]:
        return ["equity"]

    async def get_metrics(
        self,
        symbol: str,
        end_date: date | None = None,
        period: str = "ttm",
        limit: int = 10,
        **kwargs,
    ) -> list[dict]:
        if self._fundamentals_store is not None:
            as_of = end_date or self._time_provider.today()
            return self._fundamentals_store.get_metrics_as_of(symbol, as_of)
        return self._fundamentals_cache.get(symbol, [])

    async def search_line_items(
        self,
        symbol: str,
        items: list[str],
        end_date: date | None = None,
        period: str = "ttm",
        limit: int = 10,
    ) -> list[dict]:
        return []

    async def get_company_facts(self, symbol: str) -> dict:
        if self._fundamentals_store is not None:
            info = self._fundamentals_store.get_company_info(symbol)
            return {
                "symbol": symbol,
                "name": info.get("entityName", symbol),
            }
        return self._facts_cache.get(symbol, {})
