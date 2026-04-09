"""Unit tests for statistics.py — noise floor computation, verdict logic, cost estimation."""

from __future__ import annotations

import math
from datetime import date, datetime

import pytest

from app.modules.backtest.comparison import compare_runs
from app.modules.backtest.statistics import (
    MetricNoiseFloor,
    NoiseFloor,
    Verdict,
    compute_metric_stats,
    compute_noise_floor,
    compute_verdicts,
    estimate_experiment_cost,
    format_verdict_table,
    hash_experiment_config,
)
from tests.unit.backtest.conftest import (
    _make_agents_config,
    _make_backtest_config,
    _make_backtest_run,
    _make_benchmark_comparison,
    _make_performance_metrics,
)

# ── hash_experiment_config ────────────────────────────────────────────────


class TestHashExperimentConfig:
    def test_stable_across_calls(self) -> None:
        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()
        h1 = hash_experiment_config(cfg, agents)
        h2 = hash_experiment_config(cfg, agents)
        assert h1 == h2

    def test_different_start_date_different_hash(self) -> None:
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(start_date=date(2024, 1, 2), use_llm_agents=True)
        cfg2 = _make_backtest_config(start_date=date(2024, 2, 1), use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) != hash_experiment_config(cfg2, agents)

    def test_different_skills_bundle_same_hash(self) -> None:
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(skills_bundle="baseline_v1", use_llm_agents=True)
        cfg2 = _make_backtest_config(skills_bundle="treatment_v2", use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) == hash_experiment_config(cfg2, agents)

    def test_different_llm_cache_setting_same_hash(self) -> None:
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(use_llm_response_cache=True, use_llm_agents=True)
        cfg2 = _make_backtest_config(use_llm_response_cache=False, use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) == hash_experiment_config(cfg2, agents)

    def test_different_benchmark_symbols_same_hash(self) -> None:
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(benchmark_symbols=["SPY"], use_llm_agents=True)
        cfg2 = _make_backtest_config(benchmark_symbols=["QQQ", "SPY"], use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) == hash_experiment_config(cfg2, agents)

    def test_different_initial_capital_different_hash(self) -> None:
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(initial_capital=1_000_000.0, use_llm_agents=True)
        cfg2 = _make_backtest_config(initial_capital=10_000.0, use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) != hash_experiment_config(cfg2, agents)

    def test_different_slippage_different_hash(self) -> None:
        agents = _make_agents_config()
        cfg1 = _make_backtest_config(slippage_bps=10.0, use_llm_agents=True)
        cfg2 = _make_backtest_config(slippage_bps=5.0, use_llm_agents=True)
        assert hash_experiment_config(cfg1, agents) != hash_experiment_config(cfg2, agents)

    def test_different_model_different_hash(self) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        cfg = _make_backtest_config(use_llm_agents=True)
        agents1 = _make_agents_config(model="claude-sonnet-4-6")
        agents2 = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-opus-4-6", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
        )
        assert hash_experiment_config(cfg, agents1) != hash_experiment_config(cfg, agents2)

    def test_different_temperature_different_hash(self) -> None:
        cfg = _make_backtest_config(use_llm_agents=True)
        agents1 = _make_agents_config(temperature=0.3)
        agents2 = _make_agents_config(temperature=0.0)
        assert hash_experiment_config(cfg, agents1) != hash_experiment_config(cfg, agents2)

    def test_returns_hex_string(self) -> None:
        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()
        h = hash_experiment_config(cfg, agents)
        assert isinstance(h, str)
        assert len(h) == 64
        int(h, 16)  # valid hex


# ── compute_metric_stats ─────────────────────────────────────────────────


class TestComputeMetricStats:
    def test_basic_mean_and_stddev(self) -> None:
        result = compute_metric_stats([10.0, 20.0, 30.0, 40.0, 50.0], "total_return")
        assert result.metric_name == "total_return"
        assert result.mean == 30.0
        assert result.n == 5
        assert result.sample_values == [10.0, 20.0, 30.0, 40.0, 50.0]
        assert abs(result.stddev - math.sqrt(250.0)) < 0.01

    def test_single_element_stddev_is_zero(self) -> None:
        result = compute_metric_stats([42.0], "sharpe_ratio")
        assert result.mean == 42.0
        assert result.stddev == 0.0
        assert result.n == 1

    def test_two_elements(self) -> None:
        result = compute_metric_stats([10.0, 20.0], "volatility")
        assert result.n == 2
        assert result.mean == 15.0
        assert abs(result.stddev - math.sqrt(50.0)) < 0.01

    def test_identical_values_stddev_zero(self) -> None:
        result = compute_metric_stats([5.0, 5.0, 5.0], "max_drawdown")
        assert result.stddev == 0.0

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            compute_metric_stats([], "total_return")


# ── compute_noise_floor ──────────────────────────────────────────────────


class TestComputeNoiseFloor:
    def _make_runs_with_varying_returns(self, returns: list[float]) -> list:
        runs = []
        for i, ret in enumerate(returns):
            runs.append(
                _make_backtest_run(
                    run_id=f"probe_{i}",
                    metrics=_make_performance_metrics(total_return=ret),
                )
            )
        return runs

    def test_basic_noise_floor(self) -> None:
        runs = self._make_runs_with_varying_returns([0.10, 0.12, 0.11, 0.13, 0.09])
        nf = compute_noise_floor(
            probe_runs=runs,
            config_hash="abc123",
            skill_bundle_hash="def456",
        )
        assert nf.config_hash == "abc123"
        assert nf.skill_bundle_hash == "def456"
        assert nf.n_runs == 5
        assert len(nf.sample_run_ids) == 5
        assert "total_return" in nf.metrics
        tr = nf.metrics["total_return"]
        assert tr.n == 5
        assert abs(tr.mean - 0.11) < 0.001

    def test_noise_floor_includes_curated_metrics(self) -> None:
        runs = self._make_runs_with_varying_returns([0.10, 0.12, 0.11, 0.13, 0.09])
        nf = compute_noise_floor(runs, "h", "s")
        for name in (
            "total_return",
            "annualized_return",
            "sharpe_ratio",
            "sortino_ratio",
            "max_drawdown",
            "win_rate",
        ):
            assert name in nf.metrics, f"missing {name}"

    def test_noise_floor_includes_benchmark_alpha(self) -> None:
        runs = []
        for i in range(3):
            runs.append(
                _make_backtest_run(
                    run_id=f"probe_{i}",
                    benchmarks=[_make_benchmark_comparison(alpha=0.02 + i * 0.01)],
                )
            )
        nf = compute_noise_floor(runs, "h", "s")
        assert "SPY.alpha" in nf.metrics

    def test_noise_floor_timestamps_set(self) -> None:
        runs = self._make_runs_with_varying_returns([0.10, 0.12])
        nf = compute_noise_floor(runs, "h", "s")
        assert isinstance(nf.created_at, datetime)
        assert isinstance(nf.last_updated_at, datetime)
        assert nf.created_at == nf.last_updated_at

    def test_empty_runs_raises(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            compute_noise_floor([], "h", "s")

    def test_runs_with_none_metrics_raises(self) -> None:
        runs = [_make_backtest_run(run_id="probe_0", metrics=None)]
        with pytest.raises(ValueError, match="metrics"):
            compute_noise_floor(runs, "h", "s")


# ── compute_verdicts ─────────────────────────────────────────────────────


def _make_noise_floor_with_stddev(stddev: float, mean: float = 0.10) -> NoiseFloor:
    """Build a NoiseFloor where every verdict metric has the same mean/stddev."""
    metrics = {}
    for name in (
        "total_return",
        "annualized_return",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown",
        "win_rate",
    ):
        metrics[name] = MetricNoiseFloor(
            metric_name=name,
            mean=mean,
            stddev=stddev,
            n=5,
            sample_values=[mean] * 5,
        )
    metrics["SPY.alpha"] = MetricNoiseFloor(
        metric_name="SPY.alpha",
        mean=0.02,
        stddev=stddev,
        n=5,
        sample_values=[0.02] * 5,
    )
    return NoiseFloor(
        config_hash="test",
        config_label="test",
        skill_bundle_hash="abc",
        n_runs=5,
        created_at=datetime.now(),
        last_updated_at=datetime.now(),
        metrics=metrics,
        sample_run_ids=[f"probe_{i}" for i in range(5)],
    )


class TestComputeVerdicts:
    def test_within_noise_verdict(self) -> None:
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.105),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = _make_noise_floor_with_stddev(stddev=0.01)
        verdicts = compute_verdicts(cmp, nf)
        tr_verdict = next(v for v in verdicts if v.metric_name == "total_return")
        assert tr_verdict.label == "WITHIN NOISE"
        assert abs(tr_verdict.sigma - 0.5) < 0.01

    def test_possible_signal_verdict(self) -> None:
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.115),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = _make_noise_floor_with_stddev(stddev=0.01)
        verdicts = compute_verdicts(cmp, nf)
        tr_verdict = next(v for v in verdicts if v.metric_name == "total_return")
        assert tr_verdict.label == "POSSIBLE SIGNAL"

    def test_likely_signal_verdict(self) -> None:
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.13),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = _make_noise_floor_with_stddev(stddev=0.01)
        verdicts = compute_verdicts(cmp, nf)
        tr_verdict = next(v for v in verdicts if v.metric_name == "total_return")
        assert tr_verdict.label == "LIKELY SIGNAL"
        assert abs(tr_verdict.sigma - 3.0) < 0.01

    def test_zero_stddev_raises(self) -> None:
        """stddev == 0 with non-zero delta → ValueError."""
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.12),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = _make_noise_floor_with_stddev(stddev=0.0)
        with pytest.raises(ValueError, match="ZERO NOISE"):
            compute_verdicts(cmp, nf)

    def test_delta_zero_within_noise(self) -> None:
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = _make_noise_floor_with_stddev(stddev=0.01)
        verdicts = compute_verdicts(cmp, nf)
        tr_verdict = next(v for v in verdicts if v.metric_name == "total_return")
        assert tr_verdict.label == "WITHIN NOISE"
        assert tr_verdict.sigma == 0.0

    def test_t_correction_widens_thresholds(self) -> None:
        baseline = _make_backtest_run(
            run_id="base",
            metrics=_make_performance_metrics(total_return=0.10),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treat",
            metrics=_make_performance_metrics(total_return=0.125),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        nf = _make_noise_floor_with_stddev(stddev=0.01)
        # sigma = 2.5 → LIKELY with fixed thresholds
        verdicts_fixed = compute_verdicts(cmp, nf, use_t_correction=False)
        tr_fixed = next(v for v in verdicts_fixed if v.metric_name == "total_return")
        assert tr_fixed.label == "LIKELY SIGNAL"
        # sigma = 2.5 → POSSIBLE with t-correction (threshold at ~2.78 for N=5, df=4)
        verdicts_t = compute_verdicts(cmp, nf, use_t_correction=True)
        tr_t = next(v for v in verdicts_t if v.metric_name == "total_return")
        assert tr_t.label == "POSSIBLE SIGNAL"

    def test_metric_without_noise_floor_gets_no_verdict(self) -> None:
        baseline = _make_backtest_run(run_id="base", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="treat", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        nf = NoiseFloor(
            config_hash="test",
            config_label="test",
            skill_bundle_hash="abc",
            n_runs=5,
            created_at=datetime.now(),
            last_updated_at=datetime.now(),
            metrics={
                "total_return": MetricNoiseFloor(
                    metric_name="total_return",
                    mean=0.10,
                    stddev=0.01,
                    n=5,
                    sample_values=[0.10] * 5,
                ),
            },
            sample_run_ids=[],
        )
        verdicts = compute_verdicts(cmp, nf)
        names = [v.metric_name for v in verdicts]
        assert "total_return" in names
        assert "annualized_return" not in names


# ── format_verdict_table ─────────────────────────────────────────────────


class TestFormatVerdictTable:
    def test_basic_format(self) -> None:
        verdicts = [
            Verdict(
                metric_name="total_return",
                baseline=0.10,
                treatment=0.12,
                delta=0.02,
                sigma=2.5,
                label="LIKELY SIGNAL",
            ),
            Verdict(
                metric_name="sharpe_ratio",
                baseline=1.2,
                treatment=1.25,
                delta=0.05,
                sigma=0.8,
                label="WITHIN NOISE",
            ),
        ]
        output = format_verdict_table(verdicts, n_runs=5)
        assert "LIKELY SIGNAL" in output
        assert "WITHIN NOISE" in output
        assert "total_return" in output
        assert "sharpe_ratio" in output

    def test_low_sample_footnote_when_n_below_5(self) -> None:
        verdicts = [
            Verdict(
                metric_name="total_return",
                baseline=0.10,
                treatment=0.12,
                delta=0.02,
                sigma=2.5,
                label="LIKELY SIGNAL",
            ),
        ]
        output = format_verdict_table(verdicts, n_runs=3)
        assert "N=3" in output
        assert "wide confidence intervals" in output.lower()

    def test_no_footnote_when_n_is_5(self) -> None:
        verdicts = [
            Verdict(
                metric_name="total_return",
                baseline=0.10,
                treatment=0.12,
                delta=0.02,
                sigma=2.5,
                label="LIKELY SIGNAL",
            ),
        ]
        output = format_verdict_table(verdicts, n_runs=5)
        assert "wide confidence intervals" not in output.lower()

    def test_t_correction_note_in_header(self) -> None:
        verdicts = [
            Verdict(
                metric_name="total_return",
                baseline=0.10,
                treatment=0.12,
                delta=0.02,
                sigma=2.5,
                label="LIKELY SIGNAL",
            ),
        ]
        output = format_verdict_table(verdicts, n_runs=5, t_correction=True)
        assert "t-corrected" in output.lower()

    def test_fixed_threshold_note_in_header(self) -> None:
        verdicts = [
            Verdict(
                metric_name="total_return",
                baseline=0.10,
                treatment=0.12,
                delta=0.02,
                sigma=2.5,
                label="LIKELY SIGNAL",
            ),
        ]
        output = format_verdict_table(verdicts, n_runs=5, t_correction=False)
        assert "fixed" in output.lower()


# ── estimate_experiment_cost ─────────────────────────────────────────────


class TestEstimateExperimentCost:
    def test_basic_cost_with_uniform_models(self) -> None:
        agents = _make_agents_config(model="claude-sonnet-4-6")
        cfg = _make_backtest_config(
            start_date=date(2025, 1, 2),
            end_date=date(2025, 6, 30),
            top_n=20,
            use_llm_agents=True,
        )
        est = estimate_experiment_cost(cfg, agents, n_runs=5)
        assert est.n_runs == 5
        assert est.top_n == 20
        assert est.total_calls > 0
        assert est.total_cost > 0
        assert len(est.per_analyst_costs) == 3
        costs = [c["cost_per_call"] for c in est.per_analyst_costs]
        assert len(set(costs)) == 1

    def test_mixed_model_cost(self) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-opus-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-haiku-4-5-20251001", temperature=0.3),
        )
        cfg = _make_backtest_config(
            start_date=date(2025, 1, 2),
            end_date=date(2025, 6, 30),
            top_n=20,
            use_llm_agents=True,
        )
        est = estimate_experiment_cost(cfg, agents, n_runs=5)
        costs = {c["analyst"]: c["cost_per_call"] for c in est.per_analyst_costs}
        assert costs["fundamentals_analyst"] > costs["news_analyst"]
        assert costs["news_analyst"] > costs["technical_analyst"]

    def test_unknown_model_uses_default(self) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-future-9000", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
        )
        cfg = _make_backtest_config(
            start_date=date(2025, 1, 2),
            end_date=date(2025, 6, 30),
            top_n=20,
            use_llm_agents=True,
        )
        est = estimate_experiment_cost(cfg, agents, n_runs=5)
        news_cost = next(c for c in est.per_analyst_costs if c["analyst"] == "news_analyst")
        assert news_cost["unknown_model"] is True

    def test_format_output(self) -> None:
        agents = _make_agents_config(model="claude-sonnet-4-6")
        cfg = _make_backtest_config(
            start_date=date(2025, 1, 2),
            end_date=date(2025, 6, 30),
            top_n=20,
            use_llm_agents=True,
        )
        est = estimate_experiment_cost(cfg, agents, n_runs=5)
        text = est.format()
        assert "Estimated cost" in text
        assert "news_analyst" in text
        assert "fundamentals_analyst" in text
        assert "technical_analyst" in text
