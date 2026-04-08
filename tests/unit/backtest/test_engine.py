"""Tests for BacktestEngine — the event-driven simulation orchestrator.

Uses a heavily mocked BacktestContext to isolate engine logic from DI wiring.
"""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.modules.backtest.config import RebalanceFrequency
from app.modules.backtest.engine import BacktestEngine
from app.modules.backtest.models import BacktestResult

from .conftest import _make_backtest_config


def _make_trading_days(start: date = date(2024, 1, 2), count: int = 20) -> list[date]:
    """Generate a list of weekday dates."""
    days = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _make_mock_context(
    trading_days: list[date] | None = None,
    rebalance_days: set[date] | None = None,
) -> MagicMock:
    """Build a mock BacktestContext with sensible defaults."""
    days = trading_days or _make_trading_days()

    ctx = MagicMock()
    ctx.time_provider = MagicMock()
    ctx.time_provider.advance_to = MagicMock()
    ctx.time_provider.today = MagicMock(return_value=days[0])
    ctx.trading_days = days
    ctx.rebalance_days = rebalance_days or {days[0]}
    ctx.cancelled = asyncio.Event()
    ctx.store = MagicMock()
    ctx.store.get_latest_close = MagicMock(return_value=100.0)
    ctx.equities_service = AsyncMock()
    ctx.equities_service.run_pipeline = AsyncMock(return_value=MagicMock(
        orders=[], scores=[], signals=[], trades_executed=0,
    ))
    ctx.portfolio_service = AsyncMock()
    ctx.portfolio_service.get_portfolio = AsyncMock(return_value=MagicMock(
        nav=1_000_000.0,
        cash=1_000_000.0,
        positions=[],
        total_long_exposure=0.0,
        total_short_exposure=0.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
    ))
    ctx.trade_execution_service = AsyncMock()
    ctx.event_log = AsyncMock()
    ctx.instrument_ids = {"AAPL": "uuid-aapl"}
    return ctx


# ---------------------------------------------------------------------------
# TestRebalanceSchedule
# ---------------------------------------------------------------------------


class TestRebalanceSchedule:
    def test_first_day_always_rebalances(self):
        """The first trading day always triggers a rebalance."""
        days = _make_trading_days(count=30)
        engine = BacktestEngine()
        schedule = engine._compute_rebalance_schedule(days, RebalanceFrequency.WEEKLY)
        assert days[0] in schedule

    def test_daily_every_day(self):
        days = _make_trading_days(count=10)
        engine = BacktestEngine()
        schedule = engine._compute_rebalance_schedule(days, RebalanceFrequency.DAILY)
        assert schedule == set(days)

    def test_weekly_every_5_trading_days(self):
        days = _make_trading_days(count=25)
        engine = BacktestEngine()
        schedule = engine._compute_rebalance_schedule(days, RebalanceFrequency.WEEKLY)
        # First day + every 5th: indices 0, 5, 10, 15, 20
        expected = {days[i] for i in range(0, 25, 5)}
        assert schedule == expected

    def test_biweekly_every_10_trading_days(self):
        days = _make_trading_days(count=30)
        engine = BacktestEngine()
        schedule = engine._compute_rebalance_schedule(days, RebalanceFrequency.BIWEEKLY)
        expected = {days[i] for i in range(0, 30, 10)}
        assert schedule == expected

    def test_monthly_every_21_trading_days(self):
        days = _make_trading_days(count=63)
        engine = BacktestEngine()
        schedule = engine._compute_rebalance_schedule(days, RebalanceFrequency.MONTHLY)
        expected = {days[i] for i in range(0, 63, 21)}
        assert schedule == expected


# ---------------------------------------------------------------------------
# TestSimulationLoop
# ---------------------------------------------------------------------------


class TestSimulationLoop:
    async def test_advances_time_each_day(self):
        days = _make_trading_days(count=5)
        ctx = _make_mock_context(trading_days=days)
        engine = BacktestEngine()

        await engine._run_simulation(ctx, _make_backtest_config())

        # advance_to should be called for each trading day
        assert ctx.time_provider.advance_to.call_count == len(days)

    async def test_calls_pipeline_on_rebalance_days_only(self):
        days = _make_trading_days(count=10)
        rebalance = {days[0], days[5]}
        ctx = _make_mock_context(trading_days=days, rebalance_days=rebalance)
        engine = BacktestEngine()

        await engine._run_simulation(ctx, _make_backtest_config())

        assert ctx.equities_service.run_pipeline.call_count == 2

    async def test_records_snapshot_each_day(self):
        days = _make_trading_days(count=5)
        ctx = _make_mock_context(trading_days=days)
        engine = BacktestEngine()

        result = await engine._run_simulation(ctx, _make_backtest_config())

        assert len(result.snapshots) == 5

    async def test_produces_backtest_result(self):
        days = _make_trading_days(count=5)
        ctx = _make_mock_context(trading_days=days)
        engine = BacktestEngine()

        result = await engine._run_simulation(ctx, _make_backtest_config())

        assert isinstance(result, BacktestResult)
        assert result.status in ("completed", "running")

    async def test_does_not_call_pipeline_on_non_rebalance_days(self):
        days = _make_trading_days(count=5)
        # Only first day is rebalance day
        ctx = _make_mock_context(trading_days=days, rebalance_days={days[0]})
        engine = BacktestEngine()

        await engine._run_simulation(ctx, _make_backtest_config())

        assert ctx.equities_service.run_pipeline.call_count == 1

    async def test_mark_to_market_updates_nav(self):
        """NAV should be updated via mark-to-market each day."""
        days = _make_trading_days(count=3)
        ctx = _make_mock_context(trading_days=days)
        engine = BacktestEngine()

        result = await engine._run_simulation(ctx, _make_backtest_config())

        # Each snapshot should have a NAV value
        assert all(snap.nav > 0 for snap in result.snapshots)


# ---------------------------------------------------------------------------
# TestCancellation
# ---------------------------------------------------------------------------


class TestCancellation:
    async def test_cancel_stops_early(self):
        days = _make_trading_days(count=20)
        ctx = _make_mock_context(trading_days=days)
        # Set the cancelled event after a few iterations
        call_count = 0

        def advance_with_cancel(d):
            nonlocal call_count
            call_count += 1
            if call_count >= 5:
                ctx.cancelled.set()

        ctx.time_provider.advance_to = advance_with_cancel
        engine = BacktestEngine()

        result = await engine._run_simulation(ctx, _make_backtest_config())

        # Should have stopped before processing all 20 days
        assert len(result.snapshots) < 20

    async def test_cancel_produces_partial_result(self):
        days = _make_trading_days(count=20)
        ctx = _make_mock_context(trading_days=days)
        ctx.cancelled.set()  # Immediately cancelled
        engine = BacktestEngine()

        result = await engine._run_simulation(ctx, _make_backtest_config())

        assert result.status == "cancelled"


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestBacktestId
# ---------------------------------------------------------------------------


class TestBacktestId:
    async def test_uses_provided_backtest_id(self):
        """When backtest_id is given, the result should use it."""
        days = _make_trading_days(count=3)
        ctx = _make_mock_context(trading_days=days)
        engine = BacktestEngine()

        result = await engine._run_simulation(
            ctx, _make_backtest_config(), backtest_id="my-custom-id"
        )
        assert result.backtest_id == "my-custom-id"

    async def test_generates_uuid_when_not_provided(self):
        """When no backtest_id given, should auto-generate a UUID."""
        days = _make_trading_days(count=3)
        ctx = _make_mock_context(trading_days=days)
        engine = BacktestEngine()

        result = await engine._run_simulation(ctx, _make_backtest_config())
        assert len(result.backtest_id) == 36  # UUID format


# ---------------------------------------------------------------------------
# TestProgressCallback
# ---------------------------------------------------------------------------


class TestProgressCallback:
    async def test_callback_called_per_trading_day(self):
        """progress_callback should fire once per trading day in the loop."""
        days = _make_trading_days(count=5)
        ctx = _make_mock_context(trading_days=days)
        engine = BacktestEngine()
        calls: list[dict] = []

        await engine._run_simulation(
            ctx, _make_backtest_config(),
            progress_callback=lambda p: calls.append(p),
        )
        assert len(calls) == 5

    async def test_callback_includes_current_day_and_pct(self):
        """Each progress dict must contain current_day and pct_complete."""
        days = _make_trading_days(count=3)
        ctx = _make_mock_context(trading_days=days)
        engine = BacktestEngine()
        calls: list[dict] = []

        await engine._run_simulation(
            ctx, _make_backtest_config(),
            progress_callback=lambda p: calls.append(p),
        )
        for p in calls:
            assert "current_day" in p
            assert "pct_complete" in p

    async def test_last_progress_is_100_pct(self):
        """The final progress update should be 100%."""
        days = _make_trading_days(count=10)
        ctx = _make_mock_context(trading_days=days)
        engine = BacktestEngine()
        calls: list[dict] = []

        await engine._run_simulation(
            ctx, _make_backtest_config(),
            progress_callback=lambda p: calls.append(p),
        )
        assert calls[-1]["pct_complete"] == 100.0


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_pipeline_error_sets_failed_status(self):
        days = _make_trading_days(count=5)
        ctx = _make_mock_context(trading_days=days, rebalance_days={days[0]})
        ctx.equities_service.run_pipeline = AsyncMock(
            side_effect=RuntimeError("Pipeline exploded")
        )
        engine = BacktestEngine()

        result = await engine._run_simulation(ctx, _make_backtest_config())

        assert result.status == "failed"

    async def test_error_message_captured(self):
        days = _make_trading_days(count=5)
        ctx = _make_mock_context(trading_days=days, rebalance_days={days[0]})
        ctx.equities_service.run_pipeline = AsyncMock(
            side_effect=RuntimeError("Pipeline exploded")
        )
        engine = BacktestEngine()

        result = await engine._run_simulation(ctx, _make_backtest_config())

        assert "Pipeline exploded" in result.error_message

    async def test_partial_snapshots_preserved(self):
        """Snapshots collected before the error should be in the result."""
        days = _make_trading_days(count=10)
        # Rebalance only on day 5 where it will fail
        ctx = _make_mock_context(trading_days=days, rebalance_days={days[5]})
        ctx.equities_service.run_pipeline = AsyncMock(
            side_effect=RuntimeError("Crash")
        )
        engine = BacktestEngine()

        result = await engine._run_simulation(ctx, _make_backtest_config())

        # Should have snapshots for days 0-4 (before the crash on day 5)
        assert len(result.snapshots) >= 5


# ---------------------------------------------------------------------------
# TestEngineLLMSignalCapture
# ---------------------------------------------------------------------------


class TestEngineLLMSignalCapture:
    async def test_signals_populated_from_ctx_llm_cache(self, monkeypatch):
        """When ctx.llm_cache has entries at end-of-run, they land in result.signals."""
        from app.modules.backtest.context import BacktestContext
        from app.modules.equities.models import StockSignal

        # Fake a minimal BacktestContext with a populated llm_cache
        fake_ctx = MagicMock(spec=BacktestContext)
        fake_ctx.llm_cache = {
            (date(2025, 6, 15), "AAPL", "fundamentals"): StockSignal(
                symbol="AAPL",
                analyst_type="fundamentals",
                bullish_score=7,
                confidence=8,
                summary="strong margin",
            ),
            (date(2025, 6, 15), "AAPL", "news"): StockSignal(
                symbol="AAPL",
                analyst_type="news",
                bullish_score=6,
                confidence=5,
                summary="mixed",
            ),
        }
        fake_cache = MagicMock()
        fake_cache.hits = 2
        fake_cache.misses = 0
        fake_ctx.llm_response_cache = fake_cache

        signals = BacktestEngine._collect_signals_from_context(fake_ctx)
        hits, misses = BacktestEngine._collect_cache_stats_from_context(fake_ctx)

        assert len(signals) == 2
        symbols = {s.symbol for s in signals}
        assert symbols == {"AAPL"}
        analyst_types = {s.analyst_type for s in signals}
        assert analyst_types == {"fundamentals", "news"}
        assert hits == 2
        assert misses == 0

    def test_no_llm_cache_returns_empty_signals(self):
        from app.modules.backtest.context import BacktestContext

        fake_ctx = MagicMock(spec=BacktestContext)
        fake_ctx.llm_cache = {}
        fake_ctx.llm_response_cache = None

        signals = BacktestEngine._collect_signals_from_context(fake_ctx)
        hits, misses = BacktestEngine._collect_cache_stats_from_context(fake_ctx)

        assert signals == []
        assert hits == 0
        assert misses == 0
