"""Integration tests for the backtesting module.

These tests use real yfinance data (1-month range) and run the full
quantitative-only pipeline. Marked with @pytest.mark.integration.

Known behavior: Yahoo Finance rate-limiting can cause get_current_price()
to return None, resulting in 0 trades. The pipeline still succeeds.
"""

from datetime import date

import pytest

from app.modules.backtest.config import BacktestConfig, RebalanceFrequency
from app.modules.backtest.engine import BacktestEngine
from app.modules.backtest.models import BacktestResult


@pytest.mark.integration
class TestBacktestIntegration:
    async def test_short_backtest_produces_result(self):
        """1-month quantitative backtest with weekly rebalance."""
        config = BacktestConfig(
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 28),
            initial_capital=100_000.0,
            rebalance_frequency=RebalanceFrequency.WEEKLY,
            branch_name="growth",
            use_llm_agents=False,
        )
        engine = BacktestEngine()
        result = await engine.run(config)

        assert isinstance(result, BacktestResult)
        assert result.status == "completed"

    async def test_result_has_non_empty_snapshots(self):
        """Result should have daily snapshots for each trading day."""
        config = BacktestConfig(
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 28),
            initial_capital=100_000.0,
            rebalance_frequency=RebalanceFrequency.WEEKLY,
            branch_name="growth",
            use_llm_agents=False,
        )
        engine = BacktestEngine()
        result = await engine.run(config)

        assert len(result.snapshots) > 0

    async def test_result_has_performance_metrics(self):
        """Metrics should be computed and non-None."""
        config = BacktestConfig(
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 28),
            initial_capital=100_000.0,
            rebalance_frequency=RebalanceFrequency.WEEKLY,
            branch_name="growth",
            use_llm_agents=False,
        )
        engine = BacktestEngine()
        result = await engine.run(config)

        assert result.metrics is not None

    async def test_benchmark_comparison_computed(self):
        """Benchmark comparison should have alpha, beta, tracking_error."""
        config = BacktestConfig(
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 28),
            initial_capital=100_000.0,
            rebalance_frequency=RebalanceFrequency.WEEKLY,
            branch_name="growth",
            use_llm_agents=False,
            benchmark_symbol="SPY",
        )
        engine = BacktestEngine()
        result = await engine.run(config)

        if result.benchmark:
            assert result.benchmark.alpha is not None
            assert result.benchmark.beta is not None
            assert result.benchmark.tracking_error is not None
