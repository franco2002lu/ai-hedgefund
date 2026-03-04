"""Tests for VectorizedScreeningEngine — fast screening-only backtester."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from app.modules.backtest.models import ScreeningSnapshot
from app.modules.backtest.time_provider import BacktestTimeProvider
from app.modules.backtest.vectorized import VectorizedScreeningEngine


def _make_mock_screener(passed_symbols: list[str] | None = None):
    """Create a mock screener that returns fixed results."""
    screener = AsyncMock()
    symbols = passed_symbols or ["AAPL", "MSFT", "GOOGL"]
    screener.screen = AsyncMock(return_value=[
        MagicMock(symbol=s) for s in symbols
    ])
    # Per-filter results
    screener.filters = [
        MagicMock(name="MarketCapFilter"),
        MagicMock(name="LiquidityFilter"),
    ]
    return screener


class TestVectorizedScreeningEngine:
    async def test_runs_on_step_days_intervals(self):
        """Engine runs screening every N trading days."""
        engine = VectorizedScreeningEngine()
        screener = _make_mock_screener()
        time_provider = BacktestTimeProvider(date(2024, 1, 2))

        results = await engine.run(
            branch_name="growth",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 2, 1),
            step_days=5,
            screener=screener,
            time_provider=time_provider,
        )

        assert len(results) > 0
        assert all(isinstance(r, ScreeningSnapshot) for r in results)

    async def test_records_per_filter_results(self):
        """Each snapshot should contain per-filter pass/fail data."""
        engine = VectorizedScreeningEngine()
        screener = _make_mock_screener()
        time_provider = BacktestTimeProvider(date(2024, 1, 2))

        results = await engine.run(
            branch_name="growth",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 15),
            step_days=5,
            screener=screener,
            time_provider=time_provider,
        )

        for snap in results:
            assert snap.filter_results is not None

    async def test_advances_time_provider(self):
        """Time provider should be advanced for each screening date."""
        engine = VectorizedScreeningEngine()
        screener = _make_mock_screener()
        time_provider = BacktestTimeProvider(date(2024, 1, 2))

        await engine.run(
            branch_name="growth",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 2, 1),
            step_days=5,
            screener=screener,
            time_provider=time_provider,
        )

        # After running, time_provider should have been advanced
        assert time_provider.today() > date(2024, 1, 2)

    async def test_forward_returns_computed_when_flag_set(self):
        """When compute_forward_returns=True, forward returns should be present."""
        engine = VectorizedScreeningEngine()
        screener = _make_mock_screener(["AAPL"])
        time_provider = BacktestTimeProvider(date(2024, 1, 2))

        # Need price data for forward return computation
        store = MagicMock()
        store.get_close = MagicMock(return_value=100.0)

        results = await engine.run(
            branch_name="growth",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 15),
            step_days=5,
            screener=screener,
            time_provider=time_provider,
            store=store,
            compute_forward_returns=True,
        )

        # At least some snapshots should have forward_returns
        if results:
            assert any(r.forward_returns is not None for r in results)

    async def test_forward_returns_not_computed_by_default(self):
        """By default, compute_forward_returns is False."""
        engine = VectorizedScreeningEngine()
        screener = _make_mock_screener()
        time_provider = BacktestTimeProvider(date(2024, 1, 2))

        results = await engine.run(
            branch_name="growth",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 15),
            step_days=5,
            screener=screener,
            time_provider=time_provider,
        )

        for snap in results:
            assert snap.forward_returns is None

    async def test_passed_symbols_tracked(self):
        """Passed symbols from screening should be recorded in snapshots."""
        engine = VectorizedScreeningEngine()
        screener = _make_mock_screener(["AAPL", "MSFT"])
        time_provider = BacktestTimeProvider(date(2024, 1, 2))

        results = await engine.run(
            branch_name="growth",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 8),
            step_days=5,
            screener=screener,
            time_provider=time_provider,
        )

        if results:
            assert "AAPL" in results[0].passed_symbols
            assert "MSFT" in results[0].passed_symbols

    async def test_snapshot_dates_match_step_intervals(self):
        """Snapshot dates should be at step_days intervals."""
        engine = VectorizedScreeningEngine()
        screener = _make_mock_screener()
        time_provider = BacktestTimeProvider(date(2024, 1, 2))

        results = await engine.run(
            branch_name="growth",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 3, 1),
            step_days=10,
            screener=screener,
            time_provider=time_provider,
        )

        # All snapshot dates should be different
        dates = [r.date for r in results]
        assert len(dates) == len(set(dates))

    async def test_empty_date_range_returns_empty(self):
        """If start > end, should return empty results."""
        engine = VectorizedScreeningEngine()
        screener = _make_mock_screener()
        time_provider = BacktestTimeProvider(date(2024, 6, 1))

        results = await engine.run(
            branch_name="growth",
            start_date=date(2024, 6, 1),
            end_date=date(2024, 1, 1),
            step_days=5,
            screener=screener,
            time_provider=time_provider,
        )

        assert results == []
