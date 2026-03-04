"""PostgresBacktestRepository — persistence for backtest results."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from app.common.interfaces.repositories import BacktestRepository
from app.db.models import BacktestRunModel, BacktestSnapshotModel, BacktestTradeModel
from app.modules.backtest.models import (
    BacktestResult,
    BacktestTrade,
    BenchmarkComparison,
    DailySnapshot,
    PerformanceMetrics,
)


def _row_to_result(run, *, include_benchmark: bool = True, hydrate: bool = False) -> BacktestResult:
    """Convert a BacktestRunModel row to a BacktestResult domain object.

    When hydrate=True, populates snapshots and trades from the ORM relationships.
    """
    snapshots: list[DailySnapshot] = []
    trades: list[BacktestTrade] = []

    if hydrate and hasattr(run, "snapshots"):
        snapshots = [
            DailySnapshot(
                date=s.snapshot_date,
                nav=float(s.nav),
                cash=float(s.cash),
                total_long_exposure=float(s.total_long_exposure),
                total_short_exposure=float(s.total_short_exposure),
                unrealized_pnl=float(s.unrealized_pnl),
                realized_pnl=float(s.realized_pnl),
                position_count=s.position_count,
                positions=s.positions or {},
                daily_return=float(s.daily_return),
                cumulative_return=float(s.cumulative_return),
            )
            for s in run.snapshots
        ]
    if hydrate and hasattr(run, "trades"):
        trades = [
            BacktestTrade(
                trade_date=t.trade_date,
                symbol=t.symbol,
                side=t.side,
                quantity=float(t.quantity),
                price=float(t.price),
                commission=float(t.commission),
                slippage=float(t.slippage),
                reason=t.reason,
            )
            for t in run.trades
        ]

    return BacktestResult(
        backtest_id=str(run.id),
        status=run.status,
        config=run.config or {},
        metrics=PerformanceMetrics(**run.metrics) if run.metrics else None,
        snapshots=snapshots,
        trades=trades,
        benchmark=BenchmarkComparison(**run.benchmark) if run.benchmark and include_benchmark else None,
        rebalance_count=run.rebalance_count,
        duration_seconds=float(run.duration_seconds) if run.duration_seconds else 0.0,
        error_message=run.error_message,
    )


class PostgresBacktestRepository(BacktestRepository):
    """Persists backtest results to PostgreSQL via an async session."""

    def __init__(self, session) -> None:
        self._session = session

    async def save_result(self, result: BacktestResult) -> None:
        run_model = BacktestRunModel(
            id=result.backtest_id,
            status=result.status,
            config=result.config,
            metrics=result.metrics.model_dump() if result.metrics else None,
            benchmark=result.benchmark.model_dump() if result.benchmark else None,
            duration_seconds=result.duration_seconds,
            error_message=result.error_message,
            rebalance_count=result.rebalance_count,
            completed_at=datetime.now(UTC) if result.status in ("completed", "failed", "cancelled") else None,
        )
        self._session.add(run_model)

        snapshot_models = [
            BacktestSnapshotModel(
                backtest_run_id=run_model.id,
                snapshot_date=s.date,
                nav=s.nav,
                cash=s.cash,
                total_long_exposure=s.total_long_exposure,
                total_short_exposure=s.total_short_exposure,
                unrealized_pnl=s.unrealized_pnl,
                realized_pnl=s.realized_pnl,
                position_count=s.position_count,
                positions=s.positions,
                daily_return=s.daily_return,
                cumulative_return=s.cumulative_return,
            )
            for s in result.snapshots
        ]
        trade_models = [
            BacktestTradeModel(
                backtest_run_id=run_model.id,
                trade_date=t.trade_date,
                symbol=t.symbol,
                side=t.side,
                quantity=t.quantity,
                price=t.price,
                commission=t.commission,
                slippage=t.slippage,
                reason=t.reason,
            )
            for t in result.trades
        ]
        self._session.add_all(snapshot_models + trade_models)

    async def get_result(self, backtest_id: str) -> BacktestResult | None:
        stmt = (
            select(BacktestRunModel)
            .where(BacktestRunModel.id == backtest_id)
            .options(
                selectinload(BacktestRunModel.snapshots),
                selectinload(BacktestRunModel.trades),
            )
        )
        result = await self._session.execute(stmt)
        run = result.scalar_one_or_none()
        if run is None:
            return None
        return _row_to_result(run, hydrate=True)

    async def list_runs(
        self,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[BacktestResult], int]:
        count_stmt = select(func.count()).select_from(BacktestRunModel)
        stmt = select(BacktestRunModel).order_by(BacktestRunModel.created_at.desc())

        if status is not None:
            count_stmt = count_stmt.where(BacktestRunModel.status == status)
            stmt = stmt.where(BacktestRunModel.status == status)

        count_result = await self._session.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        runs = result.scalars().all()

        return [_row_to_result(r, include_benchmark=False) for r in runs], total

    async def mark_stale_runs_failed(self) -> int:
        """Mark any 'running' backtest rows as 'failed' (crash recovery)."""
        stmt = (
            update(BacktestRunModel)
            .where(BacktestRunModel.status == "running")
            .values(status="failed", error_message="Server restarted during execution")
        )
        result = await self._session.execute(stmt)
        return result.rowcount
