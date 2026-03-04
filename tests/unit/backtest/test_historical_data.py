"""Tests for HistoricalPriceStore and HistoricalDataAdapter."""

from datetime import date

import pytest

from app.common.interfaces.fundamentals import FundamentalsAdapter
from app.common.interfaces.price_data import PriceBar, PriceDataAdapter
from app.modules.backtest.adapters.historical_data import (
    HistoricalDataAdapter,
    HistoricalPriceStore,
)
from app.modules.backtest.time_provider import BacktestTimeProvider

from .conftest import _make_price_bar, _make_price_series

# ---------------------------------------------------------------------------
# HistoricalPriceStore — populated manually, zero mocks
# ---------------------------------------------------------------------------


class TestHistoricalPriceStore:
    def _make_store(self, data: dict[str, dict[date, PriceBar]] | None = None) -> HistoricalPriceStore:
        """Create a store and populate it from a {symbol: {date: bar}} dict."""
        store = HistoricalPriceStore()
        if data:
            for symbol, bars_by_date in data.items():
                store._data[symbol] = bars_by_date
                store._sorted[symbol] = sorted(bars_by_date.values(), key=lambda b: b.timestamp)
        return store

    def test_get_bar_returns_correct_bar(self):
        bar = _make_price_bar(timestamp=date(2024, 3, 15), close=155.0)
        store = self._make_store({"AAPL": {date(2024, 3, 15): bar}})

        result = store.get_bar("AAPL", date(2024, 3, 15))
        assert result is not None
        assert result.close == 155.0

    def test_get_bar_returns_none_for_missing_date(self):
        bar = _make_price_bar(timestamp=date(2024, 3, 15))
        store = self._make_store({"AAPL": {date(2024, 3, 15): bar}})

        assert store.get_bar("AAPL", date(2024, 3, 16)) is None

    def test_get_bar_returns_none_for_missing_symbol(self):
        store = self._make_store({})
        assert store.get_bar("AAPL", date(2024, 3, 15)) is None

    def test_get_bars_returns_range(self):
        series = _make_price_series("AAPL", start=date(2024, 1, 2), days=60)
        store = self._make_store({"AAPL": series})

        bars = store.get_bars("AAPL", date(2024, 1, 2), date(2024, 1, 31))
        assert len(bars) > 0
        assert all(date(2024, 1, 2) <= b.timestamp <= date(2024, 1, 31) for b in bars)

    def test_get_bars_empty_for_invalid_range(self):
        series = _make_price_series("AAPL", start=date(2024, 1, 2), days=60)
        store = self._make_store({"AAPL": series})

        # start after end
        bars = store.get_bars("AAPL", date(2024, 6, 1), date(2024, 1, 1))
        assert bars == []

    def test_get_close_returns_close_price(self):
        bar = _make_price_bar(timestamp=date(2024, 3, 15), close=155.50)
        store = self._make_store({"AAPL": {date(2024, 3, 15): bar}})

        assert store.get_close("AAPL", date(2024, 3, 15)) == pytest.approx(155.50)

    def test_get_close_returns_none_for_missing(self):
        store = self._make_store({})
        assert store.get_close("AAPL", date(2024, 3, 15)) is None

    def test_get_latest_close_handles_weekends(self):
        """Friday's close should be returned when asking for Saturday/Sunday."""
        friday = date(2024, 3, 15)  # This is a Friday
        bar = _make_price_bar(timestamp=friday, close=155.0)
        store = self._make_store({"AAPL": {friday: bar}})

        saturday = date(2024, 3, 16)
        assert store.get_latest_close("AAPL", saturday) == pytest.approx(155.0)

        sunday = date(2024, 3, 17)
        assert store.get_latest_close("AAPL", sunday) == pytest.approx(155.0)

    def test_get_latest_close_returns_none_before_data(self):
        bar = _make_price_bar(timestamp=date(2024, 3, 15), close=155.0)
        store = self._make_store({"AAPL": {date(2024, 3, 15): bar}})

        # Asking for a date before any data exists
        assert store.get_latest_close("AAPL", date(2024, 1, 1)) is None

    def test_get_trading_days_sorted(self):
        series = _make_price_series("AAPL", start=date(2024, 1, 2), days=20)
        store = self._make_store({"AAPL": series})

        days = store.get_trading_days(date(2024, 1, 2), date(2024, 2, 1))
        assert days == sorted(days)

    def test_get_trading_days_excludes_weekends(self):
        series = _make_price_series("AAPL", start=date(2024, 1, 2), days=20)
        store = self._make_store({"AAPL": series})

        days = store.get_trading_days(date(2024, 1, 2), date(2024, 2, 1))
        assert all(d.weekday() < 5 for d in days)


# ---------------------------------------------------------------------------
# HistoricalDataAdapter — real store + BacktestTimeProvider
# ---------------------------------------------------------------------------


class TestHistoricalDataAdapter:
    def _make_adapter(
        self,
        sim_date: date = date(2024, 3, 15),
        days: int = 120,
    ) -> tuple[HistoricalDataAdapter, HistoricalPriceStore, BacktestTimeProvider]:
        """Create adapter with real store and time provider."""
        tp = BacktestTimeProvider(sim_date)
        store = HistoricalPriceStore()
        series = _make_price_series("AAPL", start=date(2024, 1, 2), days=days)
        store._data["AAPL"] = series
        store._sorted["AAPL"] = sorted(series.values(), key=lambda b: b.timestamp)

        # Also load SPY for benchmark tests
        spy_series = _make_price_series("SPY", start=date(2024, 1, 2), days=days, base_price=450.0)
        store._data["SPY"] = spy_series
        store._sorted["SPY"] = sorted(spy_series.values(), key=lambda b: b.timestamp)

        adapter = HistoricalDataAdapter(
            store=store,
            time_provider=tp,
            fundamentals_cache={},
            facts_cache={},
        )
        return adapter, store, tp

    async def test_get_prices_clamps_end_date_to_simulated_today(self):
        """Key test: adapter simulating 2024-03-15 must return NO bars after that date."""
        adapter, store, tp = self._make_adapter(sim_date=date(2024, 3, 15), days=252)

        bars = await adapter.get_prices("AAPL", date(2024, 1, 2), date(2024, 12, 31))
        assert len(bars) > 0
        # No bar should have a timestamp after the simulated today
        assert all(b.timestamp <= date(2024, 3, 15) for b in bars)

    async def test_get_prices_returns_bars_within_range(self):
        adapter, store, tp = self._make_adapter(sim_date=date(2024, 3, 15))

        bars = await adapter.get_prices("AAPL", date(2024, 2, 1), date(2024, 3, 1))
        assert len(bars) > 0
        assert all(date(2024, 2, 1) <= b.timestamp <= date(2024, 3, 1) for b in bars)

    async def test_get_current_price_returns_latest_close(self):
        adapter, store, tp = self._make_adapter(sim_date=date(2024, 3, 15))

        price = await adapter.get_current_price("AAPL")
        assert price is not None
        assert isinstance(price, float)
        assert price > 0

    async def test_get_current_price_returns_none_for_unknown_symbol(self):
        adapter, store, tp = self._make_adapter()

        price = await adapter.get_current_price("UNKNOWN")
        assert price is None

    async def test_get_metrics_returns_from_cache(self):
        tp = BacktestTimeProvider(date(2024, 3, 15))
        store = HistoricalPriceStore()
        cached_metrics = [{"revenue_growth": 0.15, "roe": 0.20}]
        adapter = HistoricalDataAdapter(
            store=store,
            time_provider=tp,
            fundamentals_cache={"AAPL": cached_metrics},
            facts_cache={},
        )

        result = await adapter.get_metrics("AAPL", end_date=date(2024, 3, 15))
        assert result == cached_metrics

    async def test_get_company_facts_returns_from_cache(self):
        tp = BacktestTimeProvider(date(2024, 3, 15))
        store = HistoricalPriceStore()
        cached_facts = {"sector": "Technology", "industry": "Consumer Electronics"}
        adapter = HistoricalDataAdapter(
            store=store,
            time_provider=tp,
            fundamentals_cache={},
            facts_cache={"AAPL": cached_facts},
        )

        result = await adapter.get_company_facts("AAPL")
        assert result == cached_facts

    def test_implements_price_data_adapter(self):
        adapter, _, _ = self._make_adapter()
        assert isinstance(adapter, PriceDataAdapter)

    def test_implements_fundamentals_adapter(self):
        adapter, _, _ = self._make_adapter()
        assert isinstance(adapter, FundamentalsAdapter)
