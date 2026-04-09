"""Unit tests for app/modules/backtest/comparison.py — Phase 2 run comparison."""
from __future__ import annotations

from dataclasses import fields
from datetime import date

import pytest

from app.modules.backtest.comparison import (
    MetricDelta,
    RunComparison,
    SignalDivergence,
    UniverseDriftCell,
    compare_runs,
    format_drift_section,
    format_metric_table,
    format_signal_drilldown,
)
from tests.unit.backtest.conftest import (
    _make_agents_config,
    _make_backtest_config,
    _make_backtest_run,
    _make_benchmark_comparison,
    _make_performance_metrics,
    _make_stock_signal_record,
)


class TestDataclassDefinitions:
    def test_metric_delta_fields(self) -> None:
        field_names = {f.name for f in fields(MetricDelta)}
        assert field_names == {"name", "baseline", "treatment", "delta"}

    def test_signal_divergence_fields(self) -> None:
        field_names = {f.name for f in fields(SignalDivergence)}
        assert field_names == {
            "date",
            "symbol",
            "analyst_type",
            "baseline_score",
            "treatment_score",
            "score_delta",
            "baseline_confidence",
            "treatment_confidence",
            "impact",
            "baseline_summary",
            "treatment_summary",
        }

    def test_universe_drift_cell_fields(self) -> None:
        field_names = {f.name for f in fields(UniverseDriftCell)}
        assert field_names == {"date", "symbol", "analyst_type", "present_in"}

    def test_run_comparison_fields(self) -> None:
        field_names = {f.name for f in fields(RunComparison)}
        assert field_names == {
            "baseline",
            "treatment",
            "metric_deltas",
            "signal_divergences",
            "conviction_shifts",
            "compatibility_warnings",
            "universe_drift_cells",
            "high_drift_warning",
        }


# ── Task 4: Compatibility warnings ──────────────────────────────────────────


class TestCompareRunsCompatibilityWarnings:
    def test_identical_configs_produce_no_warnings(self) -> None:
        baseline = _make_backtest_run(run_id="b", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="t", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        assert cmp.compatibility_warnings == []

    def test_start_date_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(start_date=date(2024, 1, 2), end_date=date(2024, 6, 28)),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(start_date=date(2024, 2, 1), end_date=date(2024, 6, 28)),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("start_date" in w for w in cmp.compatibility_warnings)

    def test_end_date_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(start_date=date(2024, 1, 2), end_date=date(2024, 6, 30)),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(start_date=date(2024, 1, 2), end_date=date(2024, 7, 31)),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("end_date" in w for w in cmp.compatibility_warnings)

    def test_top_n_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(top_n=20),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(top_n=50),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("top_n" in w for w in cmp.compatibility_warnings)

    def test_rebalance_frequency_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(rebalance_frequency="weekly"),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(rebalance_frequency="monthly"),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("rebalance_frequency" in w for w in cmp.compatibility_warnings)

    def test_branch_name_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(branch_name="growth"),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(branch_name="value"),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("branch_name" in w for w in cmp.compatibility_warnings)

    def test_max_llm_calls_per_rebalance_mismatch_warns(self) -> None:
        from app.modules.backtest.config import LLMBacktestConfig

        baseline = _make_backtest_run(
            run_id="b",
            config=_make_backtest_config(llm_config=LLMBacktestConfig(max_llm_calls_per_rebalance=60)),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="t",
            config=_make_backtest_config(llm_config=LLMBacktestConfig(max_llm_calls_per_rebalance=90)),
            skill_bundle_hash="b" * 64,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("max_llm_calls_per_rebalance" in w for w in cmp.compatibility_warnings)

    def test_git_sha_mismatch_warns(self) -> None:
        baseline = _make_backtest_run(run_id="b", git_sha="aaa111", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="t", git_sha="bbb222", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        assert any("git_sha" in w and "non-prompt code" in w for w in cmp.compatibility_warnings)

    def test_identical_skill_bundle_hash_warns_nothing_to_attribute(self) -> None:
        baseline = _make_backtest_run(run_id="b", skill_bundle_hash="c" * 64)
        treatment = _make_backtest_run(run_id="t", skill_bundle_hash="c" * 64)
        cmp = compare_runs(baseline, treatment)
        assert any("nothing to attribute" in w for w in cmp.compatibility_warnings)

    def test_model_mismatch_warns_per_analyst(self) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        baseline = _make_backtest_run(
            run_id="b",
            skill_bundle_hash="a" * 64,
            effective_agents_config=_make_agents_config(model="claude-sonnet-4-6"),
        )
        treatment_agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-opus-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
        )
        treatment = _make_backtest_run(
            run_id="t",
            skill_bundle_hash="b" * 64,
            effective_agents_config=treatment_agents,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("fundamentals_analyst.model" in w for w in cmp.compatibility_warnings)

    def test_temperature_mismatch_warns_per_analyst(self) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        baseline = _make_backtest_run(
            run_id="b",
            skill_bundle_hash="a" * 64,
            effective_agents_config=_make_agents_config(temperature=0.3),
        )
        treatment_agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.7),
            fundamentals_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
        )
        treatment = _make_backtest_run(
            run_id="t",
            skill_bundle_hash="b" * 64,
            effective_agents_config=treatment_agents,
        )
        cmp = compare_runs(baseline, treatment)
        assert any("news_analyst.temperature" in w for w in cmp.compatibility_warnings)

    def test_legacy_run_without_agents_config_emits_unverifiable_warning(self) -> None:
        baseline = _make_backtest_run(
            run_id="b", skill_bundle_hash="a" * 64, effective_agents_config=None
        )
        treatment = _make_backtest_run(run_id="t", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        assert any("cannot verify model/temperature" in w for w in cmp.compatibility_warnings)


# ── Task 5: Metric delta computation ────────────────────────────────────────


class TestCompareRunsMetricDeltas:
    def test_metric_deltas_for_performance_metrics(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            metrics=_make_performance_metrics(
                total_return=0.05, sharpe_ratio=1.2, max_drawdown=0.06
            ),
        )
        treatment = _make_backtest_run(
            run_id="t",
            metrics=_make_performance_metrics(
                total_return=0.07, sharpe_ratio=1.35, max_drawdown=0.055
            ),
        )
        cmp = compare_runs(baseline, treatment)

        deltas_by_name = {d.name: d for d in cmp.metric_deltas}
        assert deltas_by_name["total_return"].delta == pytest.approx(0.02)
        assert deltas_by_name["sharpe_ratio"].delta == pytest.approx(0.15)
        assert deltas_by_name["max_drawdown"].delta == pytest.approx(-0.005)

    def test_metric_deltas_preserve_display_order(self) -> None:
        """Order matters for the rendered table. Returns -> risk -> activity -> exposure."""
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        names = [d.name for d in cmp.metric_deltas]
        # Assert a few anchors rather than the full list (keeps the test flexible).
        assert names.index("total_return") < names.index("sharpe_ratio")
        assert names.index("sharpe_ratio") < names.index("max_drawdown")
        assert names.index("max_drawdown") < names.index("total_trades")

    def test_metric_deltas_include_benchmarks(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            benchmarks=[_make_benchmark_comparison(benchmark_symbol="SPY", alpha=0.02, beta=0.9)],
        )
        treatment = _make_backtest_run(
            run_id="t",
            benchmarks=[_make_benchmark_comparison(benchmark_symbol="SPY", alpha=0.035, beta=0.88)],
        )
        cmp = compare_runs(baseline, treatment)
        deltas_by_name = {d.name: d for d in cmp.metric_deltas}
        assert deltas_by_name["SPY.alpha"].delta == pytest.approx(0.015)
        assert deltas_by_name["SPY.beta"].delta == pytest.approx(-0.02)

    def test_metric_deltas_skip_when_metrics_none(self) -> None:
        baseline = _make_backtest_run(run_id="b", metrics=None, benchmarks=[])
        treatment = _make_backtest_run(run_id="t", metrics=None, benchmarks=[])
        cmp = compare_runs(baseline, treatment)
        # When both metrics are None and no benchmarks, no deltas are produced.
        assert cmp.metric_deltas == []

    def test_metric_deltas_skip_benchmarks_with_mismatched_symbols(self) -> None:
        """Benchmarks are keyed by symbol; a benchmark present in only one run is skipped."""
        baseline = _make_backtest_run(
            run_id="b",
            benchmarks=[_make_benchmark_comparison(benchmark_symbol="SPY")],
        )
        treatment = _make_backtest_run(
            run_id="t",
            benchmarks=[_make_benchmark_comparison(benchmark_symbol="VOOG")],
        )
        cmp = compare_runs(baseline, treatment)
        deltas_by_name = {d.name for d in cmp.metric_deltas}
        # Neither SPY.* nor VOOG.* deltas should appear — no intersection.
        assert not any(n.startswith("SPY.") or n.startswith("VOOG.") for n in deltas_by_name)

    def test_metric_deltas_need_helper_import(self) -> None:
        # Ensures _make_benchmark_comparison is importable from the conftest module.
        from tests.unit.backtest.conftest import _make_benchmark_comparison, _make_performance_metrics  # noqa: F401


# ── Task 6: Signal divergence computation ────────────────────────────────────


class TestCompareRunsSignalDivergences:
    def test_disagreeing_signals_produce_divergence(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2),
                    symbol="AMZN",
                    analyst_type="fundamentals",
                    bullish_score=7,
                    confidence=5,
                    summary="baseline view",
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2),
                    symbol="AMZN",
                    analyst_type="fundamentals",
                    bullish_score=8,
                    confidence=7,
                    summary="treatment view",
                )
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert len(cmp.signal_divergences) == 1
        div = cmp.signal_divergences[0]
        assert div.symbol == "AMZN"
        assert div.score_delta == 1
        assert div.baseline_score == 7
        assert div.treatment_score == 8
        assert div.baseline_confidence == 5
        assert div.treatment_confidence == 7
        assert div.impact == 7  # |1| * max(5, 7) == 7
        assert div.baseline_summary == "baseline view"
        assert div.treatment_summary == "treatment view"

    def test_matching_signals_excluded_from_divergences(self) -> None:
        identical = _make_stock_signal_record(
            date=date(2025, 6, 2),
            symbol="AMZN",
            analyst_type="fundamentals",
            bullish_score=7,
            confidence=5,
        )
        baseline = _make_backtest_run(run_id="b", signals=[identical])
        treatment = _make_backtest_run(run_id="t", signals=[identical])
        cmp = compare_runs(baseline, treatment)
        assert cmp.signal_divergences == []

    def test_score_delta_zero_excluded_by_default(self) -> None:
        """Confidence-only shifts are NOT in the default divergences list."""
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2),
                    symbol="AMZN",
                    analyst_type="fundamentals",
                    bullish_score=7,
                    confidence=3,
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2),
                    symbol="AMZN",
                    analyst_type="fundamentals",
                    bullish_score=7,
                    confidence=9,
                )
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert cmp.signal_divergences == []
        # Conviction shifts live in a separate list populated by Task 7.
        assert cmp.conviction_shifts == []

    def test_divergences_sorted_by_impact_descending(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AAA", analyst_type="technical",
                    bullish_score=5, confidence=5,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="BBB", analyst_type="technical",
                    bullish_score=5, confidence=5,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="CCC", analyst_type="technical",
                    bullish_score=5, confidence=5,
                ),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AAA", analyst_type="technical",
                    bullish_score=6, confidence=5,  # impact 1*5=5
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="BBB", analyst_type="technical",
                    bullish_score=8, confidence=9,  # impact 3*9=27
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="CCC", analyst_type="technical",
                    bullish_score=7, confidence=8,  # impact 2*8=16
                ),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        symbols_in_order = [d.symbol for d in cmp.signal_divergences]
        assert symbols_in_order == ["BBB", "CCC", "AAA"]

    def test_divergences_skip_cells_present_in_only_one_run(self) -> None:
        """Cells present in only one run are drift, not divergence."""
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="GOOGL", analyst_type="fundamentals",
                    bullish_score=8, confidence=6,
                ),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=6, confidence=5,
                ),
                # GOOGL missing in treatment
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert len(cmp.signal_divergences) == 1
        assert cmp.signal_divergences[0].symbol == "AMZN"


# ── Task 7: Signal divergence filters ───────────────────────────────────────


class TestCompareRunsFilters:
    def test_min_confidence_filter_drops_low_conviction_rows(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AAA", analyst_type="technical",
                    bullish_score=5, confidence=3,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="BBB", analyst_type="technical",
                    bullish_score=5, confidence=8,
                ),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AAA", analyst_type="technical",
                    bullish_score=7, confidence=3,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="BBB", analyst_type="technical",
                    bullish_score=6, confidence=8,
                ),
            ],
        )
        cmp_no_filter = compare_runs(baseline, treatment)
        assert {d.symbol for d in cmp_no_filter.signal_divergences} == {"AAA", "BBB"}

        cmp_filtered = compare_runs(baseline, treatment, min_confidence=5)
        assert {d.symbol for d in cmp_filtered.signal_divergences} == {"BBB"}

    def test_conviction_shifts_default_empty(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    bullish_score=7, confidence=3,
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    bullish_score=7, confidence=9,
                )
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert cmp.conviction_shifts == []

    def test_include_conviction_shifts_populates_list(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=3,
                    summary="low conviction baseline",
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=9,
                    summary="high conviction treatment",
                )
            ],
        )
        cmp = compare_runs(baseline, treatment, include_conviction_shifts=True)
        assert len(cmp.conviction_shifts) == 1
        shift = cmp.conviction_shifts[0]
        assert shift.score_delta == 0
        assert shift.baseline_confidence == 3
        assert shift.treatment_confidence == 9
        assert shift.impact == 42  # |9 - 3| * 7 == 42

    def test_conviction_shifts_zero_conf_delta_excluded(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[_make_stock_signal_record(bullish_score=7, confidence=5)],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[_make_stock_signal_record(bullish_score=7, confidence=5)],
        )
        cmp = compare_runs(baseline, treatment, include_conviction_shifts=True)
        assert cmp.conviction_shifts == []


# ── Task 8: Universe drift computation ──────────────────────────────────────


class TestCompareRunsUniverseDrift:
    def test_cells_in_baseline_only_become_drift_cells(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="GOOGL", analyst_type="fundamentals",
                    bullish_score=8, confidence=6,
                ),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert len(cmp.universe_drift_cells) == 1
        drift = cmp.universe_drift_cells[0]
        assert drift.symbol == "GOOGL"
        assert drift.present_in == "baseline"

    def test_cells_in_treatment_only_become_drift_cells(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="MSFT", analyst_type="technical",
                    bullish_score=6, confidence=7,
                ),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert len(cmp.universe_drift_cells) == 1
        drift = cmp.universe_drift_cells[0]
        assert drift.symbol == "MSFT"
        assert drift.present_in == "treatment"
        assert drift.analyst_type == "technical"

    def test_no_drift_leaves_lists_empty(self) -> None:
        sig = _make_stock_signal_record(
            date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
            bullish_score=7, confidence=5,
        )
        baseline = _make_backtest_run(run_id="b", signals=[sig])
        treatment = _make_backtest_run(run_id="t", signals=[sig])
        cmp = compare_runs(baseline, treatment)
        assert cmp.universe_drift_cells == []
        assert cmp.high_drift_warning is None

    def test_high_drift_above_10_percent_sets_warning(self) -> None:
        # 1 shared signal + 1 baseline-only + 1 treatment-only = 3 total signals;
        # 2 drift cells / 3 total = 66.6% -> warning should fire.
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(symbol="AAA", analyst_type="technical",
                                          bullish_score=5, confidence=5),
                _make_stock_signal_record(symbol="BBB", analyst_type="technical",
                                          bullish_score=5, confidence=5),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(symbol="AAA", analyst_type="technical",
                                          bullish_score=6, confidence=5),
                _make_stock_signal_record(symbol="CCC", analyst_type="technical",
                                          bullish_score=5, confidence=5),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        assert len(cmp.universe_drift_cells) == 2
        assert cmp.high_drift_warning is not None
        assert "HIGH DRIFT" in cmp.high_drift_warning
        assert "%" in cmp.high_drift_warning

    def test_low_drift_below_10_percent_no_warning(self) -> None:
        # 9 shared + 1 drift = 10 total -> 10% -> NOT above 10% -> no warning.
        shared_symbols = [f"S{i:02d}" for i in range(9)]
        baseline_signals = [
            _make_stock_signal_record(
                symbol=sym, analyst_type="technical",
                bullish_score=5, confidence=5,
            )
            for sym in shared_symbols
        ]
        baseline_signals.append(
            _make_stock_signal_record(
                symbol="DRIFT", analyst_type="technical",
                bullish_score=5, confidence=5,
            )
        )
        treatment_signals = [
            _make_stock_signal_record(
                symbol=sym, analyst_type="technical",
                bullish_score=6, confidence=5,  # score differs so they count as shared divergences
            )
            for sym in shared_symbols
        ]
        baseline = _make_backtest_run(run_id="b", signals=baseline_signals)
        treatment = _make_backtest_run(run_id="t", signals=treatment_signals)
        cmp = compare_runs(baseline, treatment)
        # Drift = 1 cell, total signals = 10 (9 shared union + 1 baseline-only),
        # drift_pct = 10.0% -> NOT strictly above 10% -> no warning.
        assert len(cmp.universe_drift_cells) == 1
        assert cmp.high_drift_warning is None


# ── Task 9: format_metric_table ─────────────────────────────────────────────


class TestFormatMetricTable:
    def test_includes_loud_banner(self) -> None:
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        output = format_metric_table(cmp)
        assert "RAW METRIC DELTAS" in output
        assert "NO NOISE FLOOR" in output
        assert "NO SIGNIFICANCE TESTING" in output
        assert "run_experiment" in output

    def test_uses_delta_raw_column_label(self) -> None:
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        output = format_metric_table(cmp)
        assert "ΔRAW" in output

    def test_does_not_include_percent_change_column(self) -> None:
        """Percent changes invite causal interpretation — omit entirely."""
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        output = format_metric_table(cmp)
        # No '%change' column header; the banner may contain '%' but the column row shouldn't.
        header_line = next(
            (ln for ln in output.splitlines() if "ΔRAW" in ln),
            "",
        )
        assert "%" not in header_line

    def test_empty_metrics_prints_placeholder(self) -> None:
        baseline = _make_backtest_run(run_id="b", metrics=None, benchmarks=[])
        treatment = _make_backtest_run(run_id="t", metrics=None, benchmarks=[])
        cmp = compare_runs(baseline, treatment)
        output = format_metric_table(cmp)
        assert "no metrics" in output.lower() or "no deltas" in output.lower()

    def test_renders_each_metric_row(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            metrics=_make_performance_metrics(total_return=0.05, sharpe_ratio=1.2),
        )
        treatment = _make_backtest_run(
            run_id="t",
            metrics=_make_performance_metrics(total_return=0.07, sharpe_ratio=1.35),
        )
        cmp = compare_runs(baseline, treatment)
        output = format_metric_table(cmp)
        assert "total_return" in output
        assert "sharpe_ratio" in output


# ── Task 10: format_signal_drilldown ────────────────────────────────────────


class TestFormatSignalDrilldown:
    def test_empty_divergences_prints_placeholder(self) -> None:
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        output = format_signal_drilldown(cmp)
        assert "no signal divergences" in output.lower()

    def test_renders_divergence_fields(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                    summary="baseline says moderately bullish",
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=8, confidence=7,
                    summary="treatment says more bullish after margin expansion",
                )
            ],
        )
        cmp = compare_runs(baseline, treatment)
        output = format_signal_drilldown(cmp)
        assert "AMZN" in output
        assert "fundamentals" in output
        assert "2025-06-02" in output
        assert "7" in output and "8" in output  # scores
        assert "baseline says moderately bullish" in output
        assert "treatment says more bullish after margin expansion" in output

    def test_honors_top_n_truncation(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    symbol=f"S{i}", analyst_type="technical",
                    bullish_score=5, confidence=5,
                )
                for i in range(30)
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    symbol=f"S{i}", analyst_type="technical",
                    bullish_score=6, confidence=5,
                )
                for i in range(30)
            ],
        )
        cmp = compare_runs(baseline, treatment)
        output = format_signal_drilldown(cmp, top_n=5)
        # Only 5 divergence blocks should be rendered; count by looking for
        # a distinctive per-row marker like "Score:" or the symbol prefix.
        displayed_symbols = [f"S{i}" for i in range(30) if f" S{i} " in output or f"S{i},".strip(",") in output]
        # We at least expect significantly fewer than 30 to appear.
        assert len(displayed_symbols) <= 10
        # And a truncation notice should be present.
        assert "showing top" in output.lower() or "truncated" in output.lower()

    def test_conviction_shifts_subsection_when_nonempty(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="GOOGL", analyst_type="technical",
                    bullish_score=6, confidence=3,
                    summary="cautious baseline",
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="GOOGL", analyst_type="technical",
                    bullish_score=6, confidence=9,
                    summary="confident treatment",
                )
            ],
        )
        cmp = compare_runs(baseline, treatment, include_conviction_shifts=True)
        output = format_signal_drilldown(cmp)
        assert "CONVICTION SHIFTS" in output.upper() or "conviction shift" in output.lower()
        assert "GOOGL" in output


# ── Task 11: format_drift_section + to_json_dict ────────────────────────────


class TestFormatDriftSection:
    def test_empty_drift_shows_no_drift_footer(self) -> None:
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        output = format_drift_section(cmp, show_drift=False)
        assert "no drift" in output.lower() or "0 cells" in output.lower()

    def test_drift_footer_shows_count_and_percent(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(symbol="AAA", bullish_score=5, confidence=5),
                _make_stock_signal_record(symbol="BBB", bullish_score=5, confidence=5),
                _make_stock_signal_record(symbol="CCC", bullish_score=5, confidence=5),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(symbol="AAA", bullish_score=5, confidence=5),
                _make_stock_signal_record(symbol="BBB", bullish_score=5, confidence=5),
                _make_stock_signal_record(symbol="DDD", bullish_score=5, confidence=5),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        output = format_drift_section(cmp, show_drift=False)
        assert "2" in output  # 2 drift cells (CCC baseline-only, DDD treatment-only)
        assert "%" in output

    def test_show_drift_dumps_full_list(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[
                _make_stock_signal_record(symbol="BASELINE_ONLY", bullish_score=5, confidence=5),
            ],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[
                _make_stock_signal_record(symbol="TREATMENT_ONLY", bullish_score=5, confidence=5),
            ],
        )
        cmp = compare_runs(baseline, treatment)
        output = format_drift_section(cmp, show_drift=True)
        assert "BASELINE_ONLY" in output
        assert "TREATMENT_ONLY" in output


class TestRunComparisonToJsonDict:
    def test_curated_schema_has_expected_keys(self) -> None:
        baseline = _make_backtest_run(run_id="b", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="t", skill_bundle_hash="b" * 64)
        cmp = compare_runs(baseline, treatment)
        d = cmp.to_json_dict()
        expected_keys = {
            "baseline_run_id",
            "treatment_run_id",
            "baseline_skill_bundle_hash",
            "treatment_skill_bundle_hash",
            "generated_at",
            "compatibility_warnings",
            "metric_deltas",
            "signal_divergences",
            "conviction_shifts",
            "universe_drift",
        }
        assert set(d.keys()) == expected_keys
        assert d["baseline_run_id"] == "b"
        assert d["treatment_run_id"] == "t"

    def test_json_dict_does_not_duplicate_full_runs(self) -> None:
        """Consumers should join by run_id; the curated schema must not embed
        the full BacktestRun payloads on top of signal_divergences."""
        baseline = _make_backtest_run(run_id="b")
        treatment = _make_backtest_run(run_id="t")
        cmp = compare_runs(baseline, treatment)
        d = cmp.to_json_dict()
        assert "baseline" not in d
        assert "treatment" not in d
        assert "snapshots" not in d  # definitely shouldn't be in there

    def test_json_dict_drift_always_populated(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[_make_stock_signal_record(symbol="BASELINE_ONLY")],
        )
        treatment = _make_backtest_run(run_id="t", signals=[])
        cmp = compare_runs(baseline, treatment)
        d = cmp.to_json_dict()
        assert d["universe_drift"]["count"] == 1
        assert len(d["universe_drift"]["cells"]) == 1
        assert d["universe_drift"]["cells"][0]["symbol"] == "BASELINE_ONLY"

    def test_json_dict_divergence_entries_include_confidences(self) -> None:
        baseline = _make_backtest_run(
            run_id="b",
            signals=[_make_stock_signal_record(bullish_score=7, confidence=5)],
        )
        treatment = _make_backtest_run(
            run_id="t",
            signals=[_make_stock_signal_record(bullish_score=8, confidence=7)],
        )
        cmp = compare_runs(baseline, treatment)
        d = cmp.to_json_dict()
        assert len(d["signal_divergences"]) == 1
        div = d["signal_divergences"][0]
        assert div["baseline_confidence"] == 5
        assert div["treatment_confidence"] == 7
        assert div["impact"] == 7  # |1| * max(5, 7) == 7
