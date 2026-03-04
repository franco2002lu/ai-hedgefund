"""Tests for PostgresBacktestRepository — results persistence.

All DB access mocked via AsyncMock session.
"""

from unittest.mock import AsyncMock, MagicMock

from app.modules.backtest.models import BacktestResult, PerformanceMetrics
from app.modules.backtest.repository import PostgresBacktestRepository

from .conftest import _make_backtest_trade, _make_daily_snapshot


def _make_result(**overrides) -> BacktestResult:
    defaults = dict(
        backtest_id="test-run-123",
        status="completed",
        config={"start_date": "2024-01-02", "end_date": "2024-06-28"},
        metrics=PerformanceMetrics(
            total_return=0.15,
            annualized_return=0.30,
            volatility=0.18,
            sharpe_ratio=1.67,
            sortino_ratio=2.10,
            calmar_ratio=3.0,
            max_drawdown=0.10,
            max_drawdown_duration_days=15,
            total_trades=120,
            win_rate=0.55,
            profit_factor=1.8,
            avg_win=500.0,
            avg_loss=-300.0,
            avg_position_count=12.0,
            max_position_count=20,
            avg_long_exposure=0.85,
        ),
        snapshots=[_make_daily_snapshot()],
        trades=[_make_backtest_trade()],
        benchmark=None,
        rebalance_count=12,
        duration_seconds=45.2,
    )
    defaults.update(overrides)
    return BacktestResult(**defaults)


class TestPostgresBacktestRepository:
    async def test_save_result_adds_to_session(self):
        """save_result should call session.add for the run and add_all for snapshots/trades."""
        session = AsyncMock()
        repo = PostgresBacktestRepository(session)
        result = _make_result()

        await repo.save_result(result)

        assert session.add.called
        assert session.add_all.called

    async def test_get_result_returns_none_for_missing(self):
        """get_result should return None when no run with that ID exists."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=mock_result)
        repo = PostgresBacktestRepository(session)

        result = await repo.get_result("nonexistent-id")
        assert result is None

    async def test_get_result_returns_backtest_result(self):
        """get_result should reconstruct a BacktestResult from the DB model."""
        session = AsyncMock()
        mock_run = MagicMock()
        mock_run.id = "test-run-123"
        mock_run.status = "completed"
        mock_run.config = {}
        mock_run.metrics = {}
        mock_run.benchmark = None
        mock_run.duration_seconds = 45.2
        mock_run.error_message = None
        mock_run.rebalance_count = 12
        mock_run.snapshots = []
        mock_run.trades = []
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_run)
        session.execute = AsyncMock(return_value=mock_result)
        repo = PostgresBacktestRepository(session)

        result = await repo.get_result("test-run-123")
        assert result is not None

    async def test_list_runs_paginates(self):
        """list_runs should support limit/offset pagination."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        session.execute = AsyncMock(return_value=mock_result)

        # Mock count query
        count_result = MagicMock()
        count_result.scalar_one = MagicMock(return_value=0)
        session.execute = AsyncMock(return_value=count_result)

        repo = PostgresBacktestRepository(session)
        runs, total = await repo.list_runs(limit=10, offset=0)
        assert isinstance(runs, list)

    async def test_list_runs_filters_by_status(self):
        """list_runs should accept optional status filter."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        count_result = MagicMock()
        count_result.scalar_one = MagicMock(return_value=0)
        session.execute = AsyncMock(return_value=count_result)

        repo = PostgresBacktestRepository(session)
        await repo.list_runs(status="completed", limit=10, offset=0)
        assert session.execute.called


class TestMarkStaleRunsFailed:
    async def test_executes_update_statement(self):
        """mark_stale_runs_failed should execute an UPDATE on running rows."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 2
        session.execute = AsyncMock(return_value=mock_result)

        repo = PostgresBacktestRepository(session)
        count = await repo.mark_stale_runs_failed()

        assert count == 2
        session.execute.assert_called_once()

    async def test_returns_zero_when_no_stale_runs(self):
        """Should return 0 when no running rows exist."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        session.execute = AsyncMock(return_value=mock_result)

        repo = PostgresBacktestRepository(session)
        count = await repo.mark_stale_runs_failed()

        assert count == 0


class TestGetResultHydration:
    async def test_get_result_loads_snapshots(self):
        """get_result should return snapshots from the run relationship, not empty list."""
        session = AsyncMock()

        mock_snap = MagicMock()
        mock_snap.snapshot_date = "2024-01-02"
        mock_snap.nav = 1_000_000.0
        mock_snap.cash = 200_000.0
        mock_snap.total_long_exposure = 800_000.0
        mock_snap.total_short_exposure = 0.0
        mock_snap.unrealized_pnl = 0.0
        mock_snap.realized_pnl = 0.0
        mock_snap.position_count = 10
        mock_snap.positions = {}
        mock_snap.daily_return = 0.0
        mock_snap.cumulative_return = 0.0

        mock_run = MagicMock()
        mock_run.id = "test-run-123"
        mock_run.status = "completed"
        mock_run.config = {}
        mock_run.metrics = None
        mock_run.benchmark = None
        mock_run.duration_seconds = 10.0
        mock_run.error_message = None
        mock_run.rebalance_count = 2
        mock_run.snapshots = [mock_snap]
        mock_run.trades = []

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_run)
        session.execute = AsyncMock(return_value=mock_result)

        repo = PostgresBacktestRepository(session)
        result = await repo.get_result("test-run-123")

        assert result is not None
        assert len(result.snapshots) == 1

    async def test_get_result_loads_trades(self):
        """get_result should return trades from the run relationship, not empty list."""
        session = AsyncMock()

        mock_trade = MagicMock()
        mock_trade.trade_date = "2024-01-02"
        mock_trade.symbol = "AAPL"
        mock_trade.side = "buy"
        mock_trade.quantity = 10.0
        mock_trade.price = 150.0
        mock_trade.commission = 1.0
        mock_trade.slippage = 0.05
        mock_trade.reason = "new_position"

        mock_run = MagicMock()
        mock_run.id = "test-run-123"
        mock_run.status = "completed"
        mock_run.config = {}
        mock_run.metrics = None
        mock_run.benchmark = None
        mock_run.duration_seconds = 10.0
        mock_run.error_message = None
        mock_run.rebalance_count = 2
        mock_run.snapshots = []
        mock_run.trades = [mock_trade]

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_run)
        session.execute = AsyncMock(return_value=mock_result)

        repo = PostgresBacktestRepository(session)
        result = await repo.get_result("test-run-123")

        assert result is not None
        assert len(result.trades) == 1
