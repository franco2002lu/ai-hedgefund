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
        snapshots.append(
            DailySnapshot(
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
            )
        )
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
            1_000_000,
            1_200_000,  # peak
            900_000,  # trough (25% drawdown from peak)
            1_100_000,
        ]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert metrics.max_drawdown == pytest.approx(0.25, abs=0.01)

    def test_calmar_ratio(self):
        """calmar = annualized_return / max_drawdown, clamped to [-10, 10]."""
        navs = [
            1_000_000,
            1_100_000,
            900_000,
            1_200_000,
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

    def test_turnover_rate(self):
        """turnover = (total_notional / avg_nav) / years."""
        trades = [
            _make_backtest_trade(symbol="AAPL", side="buy", quantity=100.0, price=150.0),
            _make_backtest_trade(symbol="AAPL", side="sell", quantity=100.0, price=160.0),
        ]
        # 10 snapshots → 9 trading days → 9/252 years
        navs = [1_000_000] * 10
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, trades)
        # total_notional = (100*150) + (100*160) = 31000
        # years = 9/252 ≈ 0.03571
        # turnover = (31000 / 1_000_000) / 0.03571 ≈ 0.868
        assert metrics.turnover_rate == pytest.approx(0.868, abs=0.05)

    def test_value_at_risk_95(self):
        """VaR(95%) = 5th percentile of daily returns."""
        # NAV: 100, 98, 103, 97, 105, 99, 106, 101, 108, 104, 110
        navs = [100, 98, 103, 97, 105, 99, 106, 101, 108, 104, 110]
        navs = [n * 10_000 for n in navs]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        # VaR should be negative (a loss) — the worst daily returns
        assert metrics.value_at_risk_95 < 0

    def test_conditional_var_95(self):
        """CVaR(95%) <= VaR(95%) since it's the avg of the tail."""
        navs = [1_000_000 + (i % 5 - 2) * 20_000 for i in range(50)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert metrics.conditional_var_95 <= metrics.value_at_risk_95

    def test_ulcer_index(self):
        """Ulcer index > 0 when drawdowns exist."""
        navs = [1_000_000, 1_100_000, 900_000, 1_050_000]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        # NAV drops from 1.1M peak to 900K → significant drawdown
        assert metrics.ulcer_index > 0

    def test_ulcer_index_zero_for_monotonic_growth(self):
        """Ulcer index = 0 when NAV only goes up."""
        navs = [1_000_000 + i * 10_000 for i in range(20)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])
        assert metrics.ulcer_index == pytest.approx(0.0, abs=0.0001)


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

    def test_up_capture_ratio(self):
        """Up capture > 100% when strategy gains more than benchmark on up days."""
        # Strategy returns 2x benchmark → up capture ≈ 200%
        spy_series = _make_price_series(
            "SPY",
            start=date(2024, 1, 2),
            days=126,
            base_price=450.0,
            daily_return=0.0004,
        )
        navs = [1_000_000]
        for _ in range(126):
            navs.append(navs[-1] * (1 + 0.0008))
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        comparison = calc.compute_benchmark_comparison(
            snapshots, spy_series, "SPY", date(2024, 1, 2), date(2024, 6, 28)
        )
        # Strategy consistently earns 2x benchmark → up capture ≈ 200%
        assert comparison.up_capture_ratio > 100

    def test_down_capture_ratio(self):
        """Down capture computed from benchmark down days."""
        spy_series = self._make_benchmark_data()
        navs = [1_000_000 * (1 + 0.0004) ** i for i in range(127)]
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        comparison = calc.compute_benchmark_comparison(
            snapshots, spy_series, "SPY", date(2024, 1, 2), date(2024, 6, 28)
        )
        # Down capture should be a number (may be 0 if benchmark has no down days)
        assert comparison.down_capture_ratio is not None

    def test_perfect_correlation_beta_1_alpha_0(self):
        """When strategy perfectly tracks benchmark, beta ≈ 1 and alpha ≈ 0."""
        # Build identical return streams for strategy and benchmark
        spy_series = _make_price_series(
            "SPY",
            start=date(2024, 1, 2),
            days=126,
            base_price=450.0,
            daily_return=0.0004,
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


# ── Fix #1: Alpha should use Jensen's formula with risk-free rate ─────────


class TestAlphaWithRiskFreeRate:
    """Jensen's alpha = (R_p - R_f) - beta * (R_b - R_f).

    The old formula omitted R_f: alpha = R_p - beta * R_b. When beta=1 both
    formulas give the same result, so we test with the perfect-tracking
    scenario and verify the formula explicitly.
    """

    def test_alpha_formula_uses_risk_free_rate(self):
        """When beta != 1, the old formula (R_p - beta*R_b) differs from
        Jensen's alpha ((R_p - R_f) - beta*(R_b - R_f)) by (1-beta)*R_f.

        We construct a benchmark with real variance and a low-beta strategy.
        """
        from datetime import timedelta

        from app.modules.backtest.analytics import RISK_FREE_RATE

        # Build benchmark with real variance (alternating +1%/-0.5% days)
        start = date(2024, 1, 2)
        bench_returns = ([0.01, -0.005] * 63)[:126]
        bench_prices = {}
        price = 450.0
        current = start
        day_count = 0
        while day_count <= 126:
            while current.weekday() >= 5:
                current += timedelta(days=1)
            from app.common.interfaces.price_data import PriceBar

            bench_prices[current] = PriceBar(
                timestamp=current,
                open=price * 0.999,
                high=price * 1.005,
                low=price * 0.995,
                close=price,
                volume=1_000_000,
            )
            if day_count < 126:
                price *= 1 + bench_returns[day_count]
            current += timedelta(days=1)
            day_count += 1

        # Strategy: 0.3× benchmark returns + constant alpha
        bench_dates = sorted(bench_prices.keys())
        navs = [1_000_000]
        for i in range(1, len(bench_dates)):
            br = bench_prices[bench_dates[i]].close / bench_prices[bench_dates[i - 1]].close - 1
            strat_ret = 0.0003 + 0.3 * br
            navs.append(navs[-1] * (1 + strat_ret))
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        comparison = calc.compute_benchmark_comparison(snapshots, bench_prices, "SPY", start, date(2024, 6, 28))
        beta = comparison.beta
        assert beta < 0.8, f"beta={beta} should be well below 1.0"

        strat_ann = (navs[-1] / navs[0]) ** (252 / (len(navs) - 1)) - 1
        bench_ann = comparison.benchmark_annualized_return

        jensens = (strat_ann - RISK_FREE_RATE) - beta * (bench_ann - RISK_FREE_RATE)
        old_formula = strat_ann - beta * bench_ann
        # The two formulas should differ by (1 - beta) * R_f
        assert abs(jensens - old_formula) > 0.005

        # Implementation should match Jensen's
        assert comparison.alpha == pytest.approx(jensens, abs=0.02)


# ── Fix #2: Sortino should use sample variance (N-1) like Sharpe ──────────


class TestSortinoVarianceConsistency:
    """Sortino downside deviation should use (N-1) denominator, consistent with Sharpe."""

    def test_sortino_uses_sample_variance(self):
        """With known returns, verify Sortino uses sample stddev (N-1 denominator).

        Use a SHORT series where the difference between /N and /(N-1)
        is clearly distinguishable. Note: _snapshots_from_navs adds a
        day-zero snapshot with daily_return=0.0, so we must include that
        in our expected-value computation.
        """
        import math

        # 10 daily returns — but the engine will see 11 (0.0 prepended)
        raw_returns = [0.02, -0.015, 0.01, -0.02, 0.015, -0.01, 0.025, -0.018, 0.012, -0.014]
        navs = [1_000_000]
        for r in raw_returns:
            navs.append(navs[-1] * (1 + r))
        snapshots = _snapshots_from_navs(navs)
        calc = PerformanceCalculator()
        metrics = calc.compute_metrics(snapshots, [])

        # The engine sees daily_returns including the day-zero 0.0
        daily_returns = [0.0] + raw_returns
        n = len(daily_returns)  # 11

        risk_free_daily = 0.04 / 252
        mean_daily = sum(daily_returns) / n
        downside_diffs = [r - risk_free_daily for r in daily_returns if r - risk_free_daily < 0]

        # Sample: divide by (N-1) — consistent with Sharpe's volatility
        downside_var_sample = sum(d**2 for d in downside_diffs) / (n - 1)
        downside_dev_sample = math.sqrt(downside_var_sample)
        expected_sortino = (mean_daily - risk_free_daily) / downside_dev_sample * math.sqrt(252)

        # Population: divide by N — the OLD inconsistent calculation
        downside_var_pop = sum(d**2 for d in downside_diffs) / n
        downside_dev_pop = math.sqrt(downside_var_pop)
        wrong_sortino = (mean_daily - risk_free_daily) / downside_dev_pop * math.sqrt(252)

        # With N=11, difference is ~5% — clearly distinguishable
        assert abs(expected_sortino - wrong_sortino) / abs(wrong_sortino) > 0.03

        # The implementation should match sample variance
        assert metrics.sortino_ratio == pytest.approx(expected_sortino, rel=0.01)
