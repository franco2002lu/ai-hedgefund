from datetime import date

from pydantic import BaseModel


class DailySnapshot(BaseModel):
    date: date
    nav: float
    cash: float
    total_long_exposure: float
    total_short_exposure: float
    unrealized_pnl: float
    realized_pnl: float
    position_count: int
    positions: dict[str, float]
    daily_return: float
    cumulative_return: float


class BacktestTrade(BaseModel):
    trade_date: date
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float
    slippage: float
    reason: str


class PerformanceMetrics(BaseModel):
    total_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown: float
    max_drawdown_duration_days: int
    total_trades: int
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    avg_position_count: float
    max_position_count: int
    avg_long_exposure: float
    warnings: list[str] = []


class BenchmarkComparison(BaseModel):
    benchmark_symbol: str
    benchmark_total_return: float
    benchmark_annualized_return: float
    benchmark_sharpe: float
    benchmark_max_drawdown: float
    alpha: float
    beta: float
    information_ratio: float
    tracking_error: float


class BacktestResult(BaseModel):
    backtest_id: str
    status: str
    config: dict
    metrics: PerformanceMetrics | None
    snapshots: list[DailySnapshot]
    trades: list[BacktestTrade]
    benchmarks: list[BenchmarkComparison] = []
    rebalance_count: int
    duration_seconds: float
    error_message: str | None = None


class ScreeningSnapshot(BaseModel):
    date: date
    passed_symbols: list[str]
    filter_results: dict[str, list[str]]
    forward_returns: dict[str, dict[str, float]] | None = None
