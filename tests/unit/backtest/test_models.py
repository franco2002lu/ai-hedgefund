"""Tests for backtest domain models: DailySnapshot, BacktestTrade, PerformanceMetrics, etc."""

from datetime import date

import pytest

from app.modules.backtest.models import (
    BacktestResult,
    BacktestTrade,
    BenchmarkComparison,
    DailySnapshot,
    PerformanceMetrics,
    ScreeningSnapshot,
)


class TestDailySnapshot:
    def test_create_with_all_fields(self):
        snap = DailySnapshot(
            date=date(2024, 6, 15),
            nav=1_050_000.0,
            cash=200_000.0,
            total_long_exposure=850_000.0,
            total_short_exposure=0.0,
            unrealized_pnl=50_000.0,
            realized_pnl=0.0,
            position_count=15,
            positions={"AAPL": 100_000.0, "MSFT": 80_000.0},
            daily_return=0.005,
            cumulative_return=0.05,
        )
        assert snap.date == date(2024, 6, 15)
        assert snap.nav == 1_050_000.0
        assert snap.cash == 200_000.0
        assert snap.position_count == 15
        assert snap.positions["AAPL"] == 100_000.0
        assert snap.daily_return == 0.005
        assert snap.cumulative_return == 0.05

    def test_short_exposure_tracked(self):
        snap = DailySnapshot(
            date=date(2024, 6, 15),
            nav=1_000_000.0,
            cash=500_000.0,
            total_long_exposure=600_000.0,
            total_short_exposure=100_000.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            position_count=5,
            positions={"AAPL": 600_000.0},
            daily_return=0.0,
            cumulative_return=0.0,
        )
        assert snap.total_short_exposure == 100_000.0


class TestBacktestTrade:
    def test_create_buy_trade(self):
        trade = BacktestTrade(
            trade_date=date(2024, 6, 15),
            symbol="AAPL",
            side="buy",
            quantity=100.0,
            price=153.0,
            commission=0.0,
            slippage=0.0765,
            reason="new_position",
        )
        assert trade.symbol == "AAPL"
        assert trade.side == "buy"
        assert trade.quantity == 100.0
        assert trade.price == 153.0
        assert trade.reason == "new_position"

    def test_create_sell_trade(self):
        trade = BacktestTrade(
            trade_date=date(2024, 6, 15),
            symbol="MSFT",
            side="sell",
            quantity=50.0,
            price=420.0,
            commission=1.50,
            slippage=0.21,
            reason="removed_position",
        )
        assert trade.side == "sell"
        assert trade.commission == 1.50
        assert trade.slippage == 0.21


class TestPerformanceMetrics:
    def test_create_with_all_metrics(self):
        metrics = PerformanceMetrics(
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
        )
        assert metrics.total_return == 0.15
        assert metrics.sharpe_ratio == 1.67
        assert metrics.max_drawdown == 0.10
        assert metrics.win_rate == 0.55
        assert metrics.profit_factor == 1.8

    def test_warnings_field_defaults_to_empty(self):
        metrics = PerformanceMetrics(
            total_return=0.0,
            annualized_return=0.0,
            volatility=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            max_drawdown=0.0,
            max_drawdown_duration_days=0,
            total_trades=0,
            win_rate=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            avg_position_count=0.0,
            max_position_count=0,
            avg_long_exposure=0.0,
        )
        assert metrics.warnings == []


class TestBenchmarkComparison:
    def test_create_with_relative_metrics(self):
        comp = BenchmarkComparison(
            benchmark_symbol="SPY",
            benchmark_total_return=0.12,
            benchmark_annualized_return=0.24,
            benchmark_sharpe=1.2,
            benchmark_max_drawdown=0.08,
            alpha=0.06,
            beta=1.1,
            information_ratio=0.45,
            tracking_error=0.05,
        )
        assert comp.benchmark_symbol == "SPY"
        assert comp.alpha == 0.06
        assert comp.beta == 1.1
        assert comp.information_ratio == 0.45
        assert comp.tracking_error == 0.05


class TestBacktestResult:
    def test_create_completed_result(self):
        result = BacktestResult(
            backtest_id="abc-123",
            status="completed",
            config={},
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
            snapshots=[],
            trades=[],
            benchmarks=[],
            rebalance_count=12,
            duration_seconds=45.2,
        )
        assert result.backtest_id == "abc-123"
        assert result.status == "completed"
        assert result.metrics.total_return == 0.15
        assert result.benchmarks == []
        assert result.rebalance_count == 12

    def test_failed_result_has_error_message(self):
        result = BacktestResult(
            backtest_id="abc-456",
            status="failed",
            config={},
            metrics=None,
            snapshots=[],
            trades=[],
            benchmarks=[],
            rebalance_count=0,
            duration_seconds=1.5,
            error_message="Pipeline crashed: division by zero",
        )
        assert result.status == "failed"
        assert result.metrics is None
        assert "division by zero" in result.error_message


class TestScreeningSnapshot:
    def test_create_with_filter_results(self):
        snap = ScreeningSnapshot(
            date=date(2024, 3, 15),
            passed_symbols=["AAPL", "MSFT", "GOOGL"],
            filter_results={
                "MarketCapFilter": ["AAPL", "MSFT", "GOOGL", "AMZN"],
                "LiquidityFilter": ["AAPL", "MSFT", "GOOGL"],
            },
        )
        assert snap.date == date(2024, 3, 15)
        assert len(snap.passed_symbols) == 3
        assert "MarketCapFilter" in snap.filter_results

    def test_forward_returns_optional(self):
        snap = ScreeningSnapshot(
            date=date(2024, 3, 15),
            passed_symbols=["AAPL"],
            filter_results={},
        )
        assert snap.forward_returns is None

    def test_forward_returns_with_data(self):
        snap = ScreeningSnapshot(
            date=date(2024, 3, 15),
            passed_symbols=["AAPL"],
            filter_results={},
            forward_returns={
                "AAPL": {"5d": 0.02, "10d": 0.05, "21d": 0.08},
            },
        )
        assert snap.forward_returns["AAPL"]["5d"] == pytest.approx(0.02)


class TestBacktestResultLLMFields:
    def test_signals_defaults_to_empty_list(self):
        from app.modules.backtest.models import BacktestResult

        result = BacktestResult(
            backtest_id="test",
            status="completed",
            config={},
            metrics=None,
            snapshots=[],
            trades=[],
            rebalance_count=0,
            duration_seconds=1.0,
        )
        assert result.signals == []
        assert result.llm_cache_hits == 0
        assert result.llm_cache_misses == 0

    def test_populated_signals_round_trip(self):
        from datetime import date

        from app.modules.backtest.models import BacktestResult
        from app.modules.backtest.result_store import StockSignalRecord

        signal = StockSignalRecord(
            date=date(2025, 6, 15),
            symbol="AAPL",
            analyst_type="fundamentals",
            bullish_score=7,
            confidence=8,
            summary="good",
        )
        result = BacktestResult(
            backtest_id="test",
            status="completed",
            config={},
            metrics=None,
            snapshots=[],
            trades=[],
            rebalance_count=0,
            duration_seconds=1.0,
            signals=[signal],
            llm_cache_hits=5,
            llm_cache_misses=3,
        )
        assert len(result.signals) == 1
        assert result.signals[0].symbol == "AAPL"
        assert result.llm_cache_hits == 5
