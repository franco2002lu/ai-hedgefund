"""Shared test builders for the backtest module."""

from datetime import date, datetime, timedelta

from app.common.interfaces.price_data import PriceBar
from app.modules.backtest.config import BacktestConfig
from app.modules.backtest.models import (
    BacktestTrade,
    BenchmarkComparison,
    DailySnapshot,
    PerformanceMetrics,
)
from app.modules.backtest.result_store import BacktestRun, StockSignalRecord
from app.modules.equities.config import AgentsConfig, AnalystLLMConfig


def _make_price_bar(**overrides) -> PriceBar:
    defaults = dict(
        timestamp=date(2024, 6, 14),
        open=150.0,
        high=155.0,
        low=149.0,
        close=153.0,
        volume=1_000_000,
    )
    defaults.update(overrides)
    return PriceBar(**defaults)


def _make_price_series(
    symbol: str = "AAPL",
    start: date = date(2024, 1, 2),
    days: int = 252,
    base_price: float = 100.0,
    daily_return: float = 0.0004,
) -> dict[date, PriceBar]:
    """Generate deterministic daily bars, skipping weekends.

    Returns {date: PriceBar} dict for a single symbol.
    """
    result: dict[date, PriceBar] = {}
    current = start
    price = base_price
    generated = 0
    while generated < days:
        # Skip weekends
        while current.weekday() >= 5:
            current += timedelta(days=1)
        close = round(price, 4)
        bar = PriceBar(
            timestamp=current,
            open=round(close * 0.999, 4),
            high=round(close * 1.005, 4),
            low=round(close * 0.995, 4),
            close=close,
            volume=1_000_000,
        )
        result[current] = bar
        price *= 1 + daily_return
        current += timedelta(days=1)
        generated += 1
    return result


def _make_backtest_config(**overrides) -> BacktestConfig:
    defaults = dict(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 6, 28),
        initial_capital=1_000_000.0,
        rebalance_frequency="weekly",
        branch_name="growth",
        use_llm_agents=False,
        slippage_bps=10.0,
        commission_per_trade=0.0,
        benchmark_symbols=["SPY"],
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def _make_daily_snapshot(**overrides) -> DailySnapshot:
    defaults = dict(
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
    defaults.update(overrides)
    return DailySnapshot(**defaults)


def _make_backtest_trade(**overrides) -> BacktestTrade:
    defaults = dict(
        trade_date=date(2024, 6, 15),
        symbol="AAPL",
        side="buy",
        quantity=100.0,
        price=153.0,
        commission=0.0,
        slippage=0.0765,
        reason="new_position",
    )
    defaults.update(overrides)
    return BacktestTrade(**defaults)


def _make_performance_metrics(**overrides) -> PerformanceMetrics:
    defaults = dict(
        total_return=0.05,
        annualized_return=0.12,
        volatility=0.18,
        sharpe_ratio=1.2,
        sortino_ratio=1.8,
        calmar_ratio=2.0,
        max_drawdown=0.06,
        max_drawdown_duration_days=14,
        total_trades=10,
        win_rate=0.6,
        profit_factor=1.5,
        avg_win=0.02,
        avg_loss=0.01,
        avg_position_count=5.0,
        max_position_count=8,
        avg_long_exposure=0.9,
        turnover_rate=3.0,
        value_at_risk_95=-0.015,
        conditional_var_95=-0.022,
        ulcer_index=0.03,
    )
    defaults.update(overrides)
    return PerformanceMetrics(**defaults)


def _make_benchmark_comparison(**overrides) -> BenchmarkComparison:
    defaults = dict(
        benchmark_symbol="SPY",
        benchmark_total_return=0.04,
        benchmark_annualized_return=0.10,
        benchmark_sharpe=1.1,
        benchmark_max_drawdown=0.05,
        alpha=0.02,
        beta=0.9,
        information_ratio=0.3,
        tracking_error=0.08,
        up_capture_ratio=95.0,
        down_capture_ratio=85.0,
    )
    defaults.update(overrides)
    return BenchmarkComparison(**defaults)


def _make_stock_signal_record(**overrides) -> StockSignalRecord:
    defaults = dict(
        date=date(2025, 6, 2),
        symbol="AMZN",
        analyst_type="fundamentals",
        bullish_score=7,
        confidence=6,
        summary="Strong earnings growth offset by elevated P/E; moderate conviction.",
    )
    defaults.update(overrides)
    return StockSignalRecord(**defaults)


def _make_agents_config(
    model: str = "claude-sonnet-4-6",
    temperature: float = 0.3,
) -> AgentsConfig:
    return AgentsConfig(
        news_analyst=AnalystLLMConfig(model=model, temperature=temperature),
        fundamentals_analyst=AnalystLLMConfig(model=model, temperature=temperature),
        technical_analyst=AnalystLLMConfig(model=model, temperature=temperature),
    )


def _make_backtest_run(**overrides) -> BacktestRun:
    defaults = dict(
        run_id="test_run",
        timestamp=datetime(2026, 4, 8, 16, 5, 14),
        git_sha="abc123def456",
        config=_make_backtest_config(),
        skill_bundle_name=None,
        skill_bundle_hash="a" * 64,
        metrics=_make_performance_metrics(),
        benchmarks=[_make_benchmark_comparison()],
        snapshots=[],
        trades=[],
        signals=[],
        llm_cache_hits=0,
        llm_cache_misses=0,
        effective_agents_config=_make_agents_config(),
    )
    defaults.update(overrides)
    return BacktestRun(**defaults)
