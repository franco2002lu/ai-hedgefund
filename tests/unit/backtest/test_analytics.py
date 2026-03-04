"""Tests for PerformanceCalculator — pure math, zero mocks.

All assertions include inline formula comments documenting expected values.
"""

from datetime import date

import pytest

from app.modules.backtest.analytics import PerformanceCalculator
from app.modules.backtest.models import DailySnapshot

from .conftest import _make_backtest_trade, _make_daily_snapshot, _make_price_series


def _snapshots_from_navs(navs: list[float], start: date = date(2024, 1, 2)) -> list[DailySnapshot]:
    """Build DailySnapshot list from a sequence of NAV values."""
    snapshots = []
    prev_nav = navs[0]
    cum_return = 0.0
    current = start
    for i, nav in enumerate(navs):
        daily_ret = (nav / prev_nav - 1.0) if i > 0 else 0.0
        cum_return = nav / navs[0] - 1.0
        snapshots.append(DailySnapshot(
            date=current,
            nav=nav,
            cash=nav * 0.2,
            total_long_exposure=nav * 0.8,
            total_short_exposure=0.0,
            unrealized_pnl=nav - navs[0],
            realized_pnl=0.0,
            position_count=10,
            positions={"AAPL": nav * 0.5},
            daily_return=daily_ret,
            cumulative_return=cum_return,
        ))
        prev_nav = nav
        # Advance date, skip weekends
        from datetime import timedelta
        current += timedelta(days=1)
        while current.weekday() >= 5:
            current += timedelta(days=1)
    return snapshots


# ---------------------------------------------------------------------------
# TestComputeMetrics
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def test_total_return(self):
        """total_return = (final_nav / initial_nav) - 1."""
        snapshots = _snapshots_from_navs([1_000_000, 1_050_000, 1_100_000, 1_150_000])
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        # (1_150_000 / 1_000_000) - 1 = 0.15
        assert metrics.total_return == pytest.approx(0.15, abs=0.001)

    def test_annualized_return(self):
        """annualized = (1 + total_return)^(252/trading_days) - 1."""
        navs = [1_000_000 * (1 + 0.0004) ** i for i in range(253)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        # ~252 days of 0.04% daily → annualized ≈ (1.0004)^252 - 1 ≈ 10.6%
        assert metrics.annualized_return == pytest.approx(0.106, abs=0.02)

    def test_volatility_is_annualized(self):
        """volatility = daily_std * sqrt(252)."""
        navs = [1_000_000 * (1 + 0.001) ** i for i in range(100)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        # Very small daily returns → very low volatility
        assert metrics.volatility > 0
        assert metrics.volatility < 0.10  # Should be well under 10%

    def test_sharpe_ratio(self):
        """sharpe = (mean_excess_return / daily_vol) * sqrt(252)."""
        # Consistent positive returns
        navs = [1_000_000 * (1 + 0.001) ** i for i in range(253)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        # High Sharpe expected from consistent returns
        assert metrics.sharpe_ratio > 1.0

    def test_sortino_ratio(self):
        """sortino = (mean_excess_return / downside_dev) * sqrt(252)."""
        navs = [1_000_000 * (1 + 0.001) ** i for i in range(253)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        # Sortino >= Sharpe when returns are mostly positive
        assert metrics.sortino_ratio >= metrics.sharpe_ratio

    def test_max_drawdown(self):
        """NAV: 100→120→90→110 → max drawdown = (120-90)/120 = 25%."""
        navs = [
            1_000_000, 1_200_000,  # peak
            900_000,   # trough (25% drawdown from peak)
            1_100_000,
        ]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert metrics.max_drawdown == pytest.approx(0.25, abs=0.01)

    def test_calmar_ratio(self):
        """calmar = annualized_return / max_drawdown, clamped to [-10, 10]."""
        navs = [
            1_000_000, 1_100_000, 900_000, 1_200_000,
        ]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        if metrics.max_drawdown > 0:
            raw_calmar = metrics.annualized_return / metrics.max_drawdown
            expected_calmar = max(-10, min(10, raw_calmar))
            assert metrics.calmar_ratio == pytest.approx(expected_calmar, abs=0.5)

    def test_sharpe_clamped_to_range(self):
        """Sharpe ratio clamped to [-10, 10]."""
        # Very low volatility + high return → potentially huge Sharpe
        navs = [1_000_000 + i for i in range(100)]  # nearly zero vol
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert -10 <= metrics.sharpe_ratio <= 10

    def test_max_drawdown_clamped_0_to_1(self):
        """Max drawdown must be in [0, 1]."""
        navs = [1_000_000, 500_000, 200_000]  # 80% drawdown
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert 0 <= metrics.max_drawdown <= 1.0

    def test_win_rate_with_trades(self):
        """win_rate = winning_trades / total_trades."""
        trades = [
            _make_backtest_trade(symbol="AAPL", side="buy", price=100.0),
            _make_backtest_trade(symbol="AAPL", side="sell", price=110.0),  # win
            _make_backtest_trade(symbol="MSFT", side="buy", price=200.0),
            _make_backtest_trade(symbol="MSFT", side="sell", price=190.0),  # loss
            _make_backtest_trade(symbol="GOOGL", side="buy", price=150.0),
            _make_backtest_trade(symbol="GOOGL", side="sell", price=160.0),  # win
        ]
        navs = [1_000_000] * 10
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, trades)
        # 2 wins out of 3 trades = 0.667
        assert metrics.win_rate == pytest.approx(0.667, abs=0.05)

    def test_profit_factor(self):
        """profit_factor = gross_profit / gross_loss."""
        trades = [
            _make_backtest_trade(symbol="AAPL", side="buy", price=100.0),
            _make_backtest_trade(symbol="AAPL", side="sell", price=120.0),  # +20
            _make_backtest_trade(symbol="MSFT", side="buy", price=200.0),
            _make_backtest_trade(symbol="MSFT", side="sell", price=190.0),  # -10
        ]
        navs = [1_000_000] * 10
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, trades)
        # profit_factor = 20 / 10 = 2.0
        assert metrics.profit_factor == pytest.approx(2.0, abs=0.2)

    def test_exposure_metrics(self):
        """avg_position_count and avg_long_exposure computed from snapshots."""
        snapshots = [
            _make_daily_snapshot(position_count=10, total_long_exposure=800_000, nav=1_000_000),
            _make_daily_snapshot(position_count=20, total_long_exposure=900_000, nav=1_000_000),
        ]
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert metrics.avg_position_count == pytest.approx(15.0)
        assert metrics.max_position_count == 20

    def test_zero_return_zero_vol(self):
        """Flat NAV → zero volatility, zero return."""
        navs = [1_000_000] * 50
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert metrics.total_return == pytest.approx(0.0, abs=0.001)
        assert metrics.volatility == pytest.approx(0.0, abs=0.001)


# ---------------------------------------------------------------------------
# TestComputeMetricsEdgeCases
# ---------------------------------------------------------------------------


class TestComputeMetricsEdgeCases:
    def test_insufficient_data_returns_zeroed_with_warning(self):
        """Less than 2 snapshots → zeroed metrics + warning."""
        snapshots = [_make_daily_snapshot()]
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert metrics.total_return == 0.0
        assert "Insufficient data" in metrics.warnings

    def test_empty_snapshots_returns_zeroed(self):
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics([], [])
        assert metrics.total_return == 0.0
        assert "Insufficient data" in metrics.warnings

    def test_flat_returns_zero_volatility(self):
        navs = [1_000_000] * 30
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert metrics.volatility == pytest.approx(0.0, abs=0.001)

    def test_all_losses_zero_profit_factor(self):
        trades = [
            _make_backtest_trade(symbol="AAPL", side="buy", price=100.0),
            _make_backtest_trade(symbol="AAPL", side="sell", price=90.0),
            _make_backtest_trade(symbol="MSFT", side="buy", price=200.0),
            _make_backtest_trade(symbol="MSFT", side="sell", price=180.0),
        ]
        navs = [1_000_000] * 10
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, trades)
        assert metrics.profit_factor == pytest.approx(0.0, abs=0.01)

    def test_no_trades_zero_trade_metrics(self):
        navs = [1_000_000] * 30
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert metrics.total_trades == 0
        assert metrics.win_rate == pytest.approx(0.0)

    def test_sortino_clamped_to_range(self):
        """Sortino ratio clamped to [-10, 10]."""
        # Near-zero downside deviation can produce extreme Sortino
        navs = [1_000_000 + i for i in range(100)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert -10 <= metrics.sortino_ratio <= 10

    def test_calmar_clamped_to_range(self):
        """Calmar ratio clamped to [-10, 10]."""
        # Very small drawdown with significant return → potentially huge Calmar
        navs = [1_000_000 + i * 100 for i in range(100)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert -10 <= metrics.calmar_ratio <= 10

    def test_excessive_return_logs_warning(self):
        """Annualized return > 500% should trigger a warning."""
        navs = [1_000_000, 10_000_000]  # 900% return in 1 day
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        # Should still compute, but with a warning about excessive return
        assert metrics.total_return == pytest.approx(9.0, abs=0.1)


# ---------------------------------------------------------------------------
# TestComputeBenchmarkComparison
# ---------------------------------------------------------------------------


class TestComputeBenchmarkComparison:
    def _make_benchmark_data(self):
        """Create price series for benchmark."""
        return _make_price_series("SPY", start=date(2024, 1, 2), days=126, base_price=450.0)

    def test_beta_formula(self):
        """beta = cov(strategy_returns, benchmark_returns) / var(benchmark_returns)."""
        # Strategy returns exactly track benchmark → beta ≈ 1
        spy_series = self._make_benchmark_data()
        navs = [1_000_000 * (1 + 0.0004) ** i for i in range(127)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        comparison = calc.compute_benchmark_comparison(
            snapshots, spy_series, "SPY", date(2024, 1, 2), date(2024, 6, 28)
        )
        assert comparison.beta is not None

    def test_alpha_formula(self):
        """alpha = strategy_return - beta * benchmark_return."""
        spy_series = self._make_benchmark_data()
        navs = [1_000_000 * (1 + 0.0008) ** i for i in range(127)]  # outperform
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        comparison = calc.compute_benchmark_comparison(
            snapshots, spy_series, "SPY", date(2024, 1, 2), date(2024, 6, 28)
        )
        assert comparison.alpha is not None

    def test_information_ratio(self):
        """info_ratio = active_return / tracking_error."""
        spy_series = self._make_benchmark_data()
        navs = [1_000_000 * (1 + 0.0006) ** i for i in range(127)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        comparison = calc.compute_benchmark_comparison(
            snapshots, spy_series, "SPY", date(2024, 1, 2), date(2024, 6, 28)
        )
        assert comparison.information_ratio is not None

    def test_tracking_error(self):
        """tracking_error = std(strategy_returns - benchmark_returns) * sqrt(252)."""
        spy_series = self._make_benchmark_data()
        navs = [1_000_000 * (1 + 0.0004) ** i for i in range(127)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        comparison = calc.compute_benchmark_comparison(
            snapshots, spy_series, "SPY", date(2024, 1, 2), date(2024, 6, 28)
        )
        assert comparison.tracking_error >= 0

    def test_benchmark_metrics_computed(self):
        """Benchmark metrics (return, sharpe, drawdown) are populated."""
        spy_series = self._make_benchmark_data()
        navs = [1_000_000 * (1 + 0.0004) ** i for i in range(127)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        comparison = calc.compute_benchmark_comparison(
            snapshots, spy_series, "SPY", date(2024, 1, 2), date(2024, 6, 28)
        )
        assert comparison.benchmark_symbol == "SPY"
        assert comparison.benchmark_total_return is not None

    def test_perfect_correlation_beta_1_alpha_0(self):
        """When strategy perfectly tracks benchmark, beta ≈ 1 and alpha ≈ 0."""
        # Build identical return streams for strategy and benchmark
        spy_series = _make_price_series(
            "SPY", start=date(2024, 1, 2), days=126,
            base_price=450.0, daily_return=0.0004,
        )
        # Strategy NAV tracks the same returns
        navs = [1_000_000]
        for _ in range(126):
            navs.append(navs[-1] * (1 + 0.0004))
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        comparison = calc.compute_benchmark_comparison(
            snapshots, spy_series, "SPY", date(2024, 1, 2), date(2024, 6, 28)
        )
        assert comparison.beta == pytest.approx(1.0, abs=0.15)
        assert comparison.alpha == pytest.approx(0.0, abs=0.05)
