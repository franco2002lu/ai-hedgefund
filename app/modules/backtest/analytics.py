"""Performance analytics for backtesting.

Computes portfolio metrics, trade statistics, and benchmark comparisons
from backtest snapshots and trade records.
"""

import math
from collections import defaultdict
from datetime import date

from app.common.interfaces.price_data import PriceBar
from app.modules.backtest.models import (
    BacktestTrade,
    BenchmarkComparison,
    DailySnapshot,
    PerformanceMetrics,
)

TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE = 0.04


class PerformanceCalculator:
    def compute_metrics(
        self,
        snapshots: list[DailySnapshot],
        trades: list[BacktestTrade],
    ) -> PerformanceMetrics:
        if len(snapshots) < 2:
            return PerformanceMetrics(
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
                warnings=["Insufficient data"],
            )

        initial_nav = snapshots[0].nav
        final_nav = snapshots[-1].nav
        trading_days = len(snapshots) - 1

        # Total return
        total_return = (final_nav / initial_nav) - 1

        # Daily returns
        daily_returns = [s.daily_return for s in snapshots]

        # Annualized return
        annualized_return = (1 + total_return) ** (TRADING_DAYS_PER_YEAR / trading_days) - 1

        # Volatility
        mean_daily = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_daily) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        daily_std = math.sqrt(variance)
        volatility = daily_std * math.sqrt(TRADING_DAYS_PER_YEAR)

        # Sharpe ratio
        risk_free_daily = RISK_FREE_RATE / TRADING_DAYS_PER_YEAR
        if daily_std > 0:
            sharpe_ratio = (mean_daily - risk_free_daily) / daily_std * math.sqrt(TRADING_DAYS_PER_YEAR)
            sharpe_ratio = max(-10, min(10, sharpe_ratio))
        else:
            sharpe_ratio = 0.0

        # Sortino ratio
        downside_diffs = [r - risk_free_daily for r in daily_returns if r - risk_free_daily < 0]
        if downside_diffs:
            downside_var = sum(d**2 for d in downside_diffs) / len(daily_returns)
            downside_dev = math.sqrt(downside_var)
            if downside_dev > 0:
                sortino_ratio = (mean_daily - risk_free_daily) / downside_dev * math.sqrt(TRADING_DAYS_PER_YEAR)
            else:
                sortino_ratio = max(sharpe_ratio, 0.0)
        else:
            sortino_ratio = max(sharpe_ratio, 0.0)
        sortino_ratio = max(-10, min(10, sortino_ratio))

        # Max drawdown
        peak_nav = snapshots[0].nav
        max_drawdown = 0.0
        max_drawdown_duration_days = 0
        current_dd_days = 0
        for s in snapshots:
            if s.nav > peak_nav:
                peak_nav = s.nav
                current_dd_days = 0
            else:
                current_dd_days += 1
            dd = (peak_nav - s.nav) / peak_nav
            if dd > max_drawdown:
                max_drawdown = dd
            if current_dd_days > max_drawdown_duration_days:
                max_drawdown_duration_days = current_dd_days
        max_drawdown = max(0.0, min(1.0, max_drawdown))

        # Calmar ratio
        calmar_ratio = annualized_return / max_drawdown if max_drawdown > 0 else 0.0
        calmar_ratio = max(-10, min(10, calmar_ratio))

        # Trade metrics
        total_trades, win_rate, profit_factor, avg_win, avg_loss = self._compute_trade_metrics(trades)

        # Exposure metrics
        avg_position_count = sum(s.position_count for s in snapshots) / len(snapshots)
        max_position_count = max(s.position_count for s in snapshots)
        avg_long_exposure = sum(
            s.total_long_exposure / s.nav if s.nav > 0 else 0.0 for s in snapshots
        ) / len(snapshots)

        # Warnings
        warnings: list[str] = []
        if annualized_return > 5.0:
            warnings.append("Excessive annualized return (>500%)")

        return PerformanceMetrics(
            total_return=total_return,
            annualized_return=annualized_return,
            volatility=volatility,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            calmar_ratio=calmar_ratio,
            max_drawdown=max_drawdown,
            max_drawdown_duration_days=max_drawdown_duration_days,
            total_trades=total_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            avg_position_count=avg_position_count,
            max_position_count=max_position_count,
            avg_long_exposure=avg_long_exposure,
            warnings=warnings,
        )

    def _compute_trade_metrics(self, trades: list[BacktestTrade]) -> tuple[int, float, float, float, float]:
        if not trades:
            return 0, 0.0, 0.0, 0.0, 0.0

        # Group trades by symbol and pair buys with sells
        by_symbol: dict[str, list[BacktestTrade]] = defaultdict(list)
        for t in trades:
            by_symbol[t.symbol].append(t)

        profits: list[float] = []
        losses: list[float] = []

        for _symbol, symbol_trades in by_symbol.items():
            buys = [t for t in symbol_trades if t.side == "buy"]
            sells = [t for t in symbol_trades if t.side == "sell"]
            pairs = min(len(buys), len(sells))
            for i in range(pairs):
                pnl = (sells[i].price - buys[i].price) * buys[i].quantity
                if pnl > 0:
                    profits.append(pnl)
                else:
                    losses.append(abs(pnl))

        total_trades = len(profits) + len(losses)
        if total_trades == 0:
            return 0, 0.0, 0.0, 0.0, 0.0

        win_rate = len(profits) / total_trades
        gross_profit = sum(profits)
        gross_loss = sum(losses)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        avg_win = gross_profit / len(profits) if profits else 0.0
        avg_loss = gross_loss / len(losses) if losses else 0.0

        return total_trades, win_rate, profit_factor, avg_win, avg_loss

    def compute_benchmark_comparison(
        self,
        snapshots: list[DailySnapshot],
        benchmark_prices: dict[date, PriceBar],
        benchmark_symbol: str,
        start_date: date,
        end_date: date,
    ) -> BenchmarkComparison:
        # Extract benchmark daily returns from sorted price data
        sorted_dates = sorted(benchmark_prices.keys())
        bench_closes = [benchmark_prices[d].close for d in sorted_dates]

        bench_daily_returns = []
        for i in range(1, len(bench_closes)):
            bench_daily_returns.append(bench_closes[i] / bench_closes[i - 1] - 1)

        # Benchmark total and annualized return
        benchmark_total_return = (bench_closes[-1] / bench_closes[0]) - 1
        bench_trading_days = len(bench_closes) - 1
        benchmark_annualized_return = (
            (1 + benchmark_total_return) ** (TRADING_DAYS_PER_YEAR / bench_trading_days) - 1
            if bench_trading_days > 0
            else 0.0
        )

        # Strategy daily returns computed from NAV (consistent with benchmark calculation)
        strategy_daily_returns = [snapshots[i].nav / snapshots[i - 1].nav - 1 for i in range(1, len(snapshots))]

        # Align lengths
        min_len = min(len(strategy_daily_returns), len(bench_daily_returns))
        strat_aligned = strategy_daily_returns[:min_len]
        bench_aligned = bench_daily_returns[:min_len]

        # Strategy metrics
        strategy_total_return = (snapshots[-1].nav / snapshots[0].nav) - 1
        strat_trading_days = len(snapshots) - 1
        strategy_annualized_return = (
            (1 + strategy_total_return) ** (TRADING_DAYS_PER_YEAR / strat_trading_days) - 1
            if strat_trading_days > 0
            else 0.0
        )

        # Beta = cov(strat, bench) / var(bench)
        if min_len > 1:
            mean_s = sum(strat_aligned) / min_len
            mean_b = sum(bench_aligned) / min_len
            cov = sum((strat_aligned[i] - mean_s) * (bench_aligned[i] - mean_b) for i in range(min_len)) / min_len
            var_b = sum((b - mean_b) ** 2 for b in bench_aligned) / min_len
            var_s = sum((s - mean_s) ** 2 for s in strat_aligned) / min_len
            if var_b < 1e-12:
                # Near-constant benchmark returns
                beta = 1.0
            elif var_s < 1e-12 and abs(mean_s - mean_b) < 1e-6:
                # Near-constant strategy returns tracking benchmark
                beta = 1.0
            else:
                beta = cov / var_b
        else:
            beta = 1.0

        # Alpha (simplified Jensen's alpha)
        alpha = strategy_annualized_return - beta * benchmark_annualized_return

        # Tracking error
        if min_len > 1:
            active_returns = [strat_aligned[i] - bench_aligned[i] for i in range(min_len)]
            mean_active = sum(active_returns) / min_len
            active_var = sum((a - mean_active) ** 2 for a in active_returns) / min_len
            tracking_error = math.sqrt(active_var) * math.sqrt(TRADING_DAYS_PER_YEAR)
        else:
            tracking_error = 0.0

        # Information ratio
        if tracking_error > 0:
            information_ratio = (strategy_annualized_return - benchmark_annualized_return) / tracking_error
        else:
            information_ratio = 0.0

        # Benchmark Sharpe
        risk_free_daily = RISK_FREE_RATE / TRADING_DAYS_PER_YEAR
        if bench_daily_returns:
            mean_bench = sum(bench_daily_returns) / len(bench_daily_returns)
            n = len(bench_daily_returns)
            bench_var = sum((r - mean_bench) ** 2 for r in bench_daily_returns) / (n - 1) if n > 1 else 0.0
            bench_std = math.sqrt(bench_var)
            benchmark_sharpe = (
                (mean_bench - risk_free_daily) / bench_std * math.sqrt(TRADING_DAYS_PER_YEAR) if bench_std > 0 else 0.0
            )
        else:
            benchmark_sharpe = 0.0

        # Benchmark max drawdown
        peak = bench_closes[0]
        benchmark_max_drawdown = 0.0
        for c in bench_closes:
            if c > peak:
                peak = c
            dd = (peak - c) / peak
            if dd > benchmark_max_drawdown:
                benchmark_max_drawdown = dd

        return BenchmarkComparison(
            benchmark_symbol=benchmark_symbol,
            benchmark_total_return=benchmark_total_return,
            benchmark_annualized_return=benchmark_annualized_return,
            benchmark_sharpe=benchmark_sharpe,
            benchmark_max_drawdown=benchmark_max_drawdown,
            alpha=alpha,
            beta=beta,
            information_ratio=information_ratio,
            tracking_error=tracking_error,
        )
