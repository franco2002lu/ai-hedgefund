"""BacktestEngine — event-driven simulation orchestrator."""

from __future__ import annotations

import logging
import time as _time
import uuid
from collections.abc import Callable
from datetime import date

from app.modules.backtest.config import BacktestConfig, RebalanceFrequency
from app.modules.backtest.models import (
    BacktestResult,
    BacktestTrade,
    DailySnapshot,
)

logger = logging.getLogger(__name__)


def compute_rebalance_schedule(
    trading_days: list[date],
    frequency: RebalanceFrequency,
) -> set[date]:
    """Compute which trading days are rebalance days given a frequency."""
    if not trading_days:
        return set()

    step_map = {
        RebalanceFrequency.DAILY: 1,
        RebalanceFrequency.WEEKLY: 5,
        RebalanceFrequency.BIWEEKLY: 10,
        RebalanceFrequency.MONTHLY: 21,
    }
    step = step_map[frequency]
    return {trading_days[i] for i in range(0, len(trading_days), step)}


class BacktestEngine:
    """Runs a backtest simulation over historical data."""

    # Keep backward-compatible instance method that delegates to module function
    def _compute_rebalance_schedule(
        self,
        trading_days: list[date],
        frequency: RebalanceFrequency,
    ) -> set[date]:
        return compute_rebalance_schedule(trading_days, frequency)

    async def _mark_to_market(self, ctx, branch_id: str) -> None:
        """Recompute NAV using today's close prices from the store."""
        portfolio = await ctx.portfolio_service.get_portfolio(branch_id)
        if portfolio is None:
            return
        today = ctx.time_provider.today()
        total_long = sum(
            (ctx.store.get_latest_close(p.symbol, today) or 0.0) * p.long_quantity
            for p in portfolio.positions
        )
        new_nav = float(portfolio.cash) + total_long
        await ctx.portfolio_service.portfolio_repo.update_portfolio_fields(
            branch_id,
            nav=new_nav,
            total_long_exposure=total_long,
        )

    async def _run_simulation(
        self,
        ctx,
        config: BacktestConfig,
        *,
        backtest_id: str | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> BacktestResult:
        from app.modules.backtest.analytics import PerformanceCalculator

        snapshots: list[DailySnapshot] = []
        status = "completed"
        error_message: str | None = None
        actual_rebalances = 0
        started = _time.monotonic()
        total_days = len(ctx.trading_days)

        for i, day in enumerate(ctx.trading_days):
            # Check cancellation
            if ctx.cancelled.is_set():
                status = "cancelled"
                break

            # Advance simulated clock
            ctx.time_provider.advance_to(day)

            # Mark-to-market positions with today's prices
            await self._mark_to_market(ctx, config.branch_name)

            # Rebalance if scheduled
            if day in ctx.rebalance_days:
                # Reset LLM call counter for this rebalance window
                for wrapper in getattr(ctx, "llm_wrappers", []):
                    wrapper.reset_rebalance_counter()
                try:
                    await ctx.equities_service.run_pipeline(
                        branch_name=config.branch_name,
                        branch_id=config.branch_name,
                        trade_execution_service=ctx.trade_execution_service,
                        portfolio_service=ctx.portfolio_service,
                        event_log_repo=ctx.event_log,
                        session=None,
                        instrument_ids=ctx.instrument_ids,
                    )
                    actual_rebalances += 1
                except Exception as exc:
                    status = "failed"
                    error_message = str(exc)
                    break

            # Read updated portfolio state
            portfolio = await ctx.portfolio_service.get_portfolio(config.branch_name)
            nav = float(portfolio.nav) if portfolio else config.initial_capital
            cash = float(portfolio.cash) if portfolio else config.initial_capital
            positions = portfolio.positions if portfolio else []

            # Compute daily and cumulative returns
            prev_nav = snapshots[-1].nav if snapshots else config.initial_capital
            daily_return = (nav - prev_nav) / prev_nav if prev_nav > 0 else 0.0
            cumulative_return = (nav - config.initial_capital) / config.initial_capital

            # Record daily snapshot
            snapshots.append(
                DailySnapshot(
                    date=day,
                    nav=nav,
                    cash=cash,
                    total_long_exposure=float(portfolio.total_long_exposure) if portfolio else 0.0,
                    total_short_exposure=float(portfolio.total_short_exposure) if portfolio else 0.0,
                    unrealized_pnl=float(portfolio.unrealized_pnl) if portfolio else 0.0,
                    realized_pnl=float(portfolio.realized_pnl) if portfolio else 0.0,
                    position_count=len(positions),
                    positions={
                        p.symbol: (ctx.store.get_latest_close(p.symbol, day) or 0.0) * p.long_quantity
                        for p in positions
                    },
                    daily_return=daily_return,
                    cumulative_return=cumulative_return,
                )
            )

            # Report progress
            if progress_callback is not None:
                progress_callback({
                    "current_day": day.isoformat(),
                    "pct_complete": (i + 1) / total_days * 100,
                })

        # Extract trades from in-memory store
        backtest_trades: list[BacktestTrade] = []
        if ctx.trade_repo is not None:
            for t in ctx.trade_repo.all_trades:
                backtest_trades.append(
                    BacktestTrade(
                        trade_date=t.executed_at.date(),
                        symbol=t.symbol,
                        side=str(t.side),
                        quantity=float(t.quantity),
                        price=float(t.price),
                        commission=float(t.commission),
                        slippage=float(t.slippage),
                        reason="pipeline",
                    )
                )

        # Compute analytics
        metrics = None
        benchmark = None
        if len(snapshots) >= 2:
            calculator = PerformanceCalculator()
            metrics = calculator.compute_metrics(snapshots, backtest_trades)

            benchmark_bars = ctx.store.get_bars(
                config.benchmark_symbol, config.start_date, config.end_date
            )
            if len(benchmark_bars) >= 2:
                benchmark_prices = {b.timestamp: b for b in benchmark_bars}
                try:
                    benchmark = calculator.compute_benchmark_comparison(
                        snapshots=snapshots,
                        benchmark_prices=benchmark_prices,
                        benchmark_symbol=config.benchmark_symbol,
                        start_date=config.start_date,
                        end_date=config.end_date,
                    )
                except Exception:
                    logger.warning("Benchmark comparison failed", exc_info=True)

        duration = _time.monotonic() - started

        return BacktestResult(
            backtest_id=backtest_id or str(uuid.uuid4()),
            status=status,
            config=config.model_dump(mode="json"),
            metrics=metrics,
            snapshots=snapshots,
            trades=backtest_trades,
            benchmark=benchmark,
            rebalance_count=actual_rebalances,
            duration_seconds=duration,
            error_message=error_message,
        )

    async def run(
        self,
        config: BacktestConfig,
        live_config=None,
        *,
        backtest_id: str | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> BacktestResult:
        from app.modules.backtest.context import BacktestContext
        from app.modules.equities.config import EquitiesConfig

        ctx = await BacktestContext.build(config, live_config or EquitiesConfig())
        return await self._run_simulation(
            ctx, config,
            backtest_id=backtest_id,
            progress_callback=progress_callback,
        )
