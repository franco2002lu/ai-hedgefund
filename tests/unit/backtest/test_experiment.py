"""Unit tests for experiment.py — experiment result containers, formatters, runner, persistence."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.backtest.comparison import compare_runs
from app.modules.backtest.experiment import (
    ExperimentResult,
    ExperimentRunner,
    format_experiment_report,
    save_experiment_result,
)
from app.modules.backtest.noise_floor_store import NoiseFloorStore
from app.modules.backtest.statistics import MetricNoiseFloor, NoiseFloor, Verdict
from tests.unit.backtest.conftest import (
    _make_agents_config,
    _make_backtest_config,
    _make_backtest_run,
    _make_performance_metrics,
)

# ── Shared test helpers ──────────────────────────────────────────────────


def _make_test_noise_floor(**overrides) -> NoiseFloor:
    defaults = dict(
        config_hash="test_hash",
        config_label="medium / growth / 2025-12-31",
        skill_bundle_hash="abc" * 20 + "abcd",
        n_runs=5,
        created_at=datetime(2026, 4, 5, 12, 0, 0),
        last_updated_at=datetime(2026, 4, 5, 12, 0, 0),
        metrics={
            "total_return": MetricNoiseFloor(
                metric_name="total_return",
                mean=0.10,
                stddev=0.015,
                n=5,
                sample_values=[0.09, 0.10, 0.11, 0.10, 0.10],
            ),
        },
        sample_run_ids=["probe_0", "probe_1", "probe_2", "probe_3", "probe_4"],
    )
    defaults.update(overrides)
    return NoiseFloor(**defaults)


def _make_test_experiment_result(**overrides) -> ExperimentResult:
    baseline = _make_backtest_run(run_id="baseline_run", skill_bundle_hash="a" * 64)
    treatment = _make_backtest_run(run_id="treatment_run", skill_bundle_hash="b" * 64)
    cmp = compare_runs(baseline, treatment)
    nf = _make_test_noise_floor()
    defaults = dict(
        baseline_run_id="baseline_run",
        treatment_run_id="treatment_run",
        noise_floor=nf,
        noise_floor_age_days=5,
        noise_floor_stale=False,
        bundle_mismatch_warning=False,
        comparison=cmp,
        verdicts=[
            Verdict(
                metric_name="total_return",
                baseline=0.10,
                treatment=0.12,
                delta=0.02,
                sigma=1.3,
                label="POSSIBLE SIGNAL",
            ),
        ],
        t_correction_used=False,
    )
    defaults.update(overrides)
    return ExperimentResult(**defaults)


# ── ExperimentResult ─────────────────────────────────────────────────────


class TestExperimentResult:
    def test_dataclass_fields(self) -> None:
        result = _make_test_experiment_result()
        assert result.baseline_run_id == "baseline_run"
        assert result.treatment_run_id == "treatment_run"
        assert result.noise_floor_age_days == 5
        assert result.noise_floor_stale is False
        assert result.bundle_mismatch_warning is False
        assert len(result.verdicts) == 1


# ── to_json_dict ─────────────────────────────────────────────────────────


class TestExperimentResultToJsonDict:
    def test_curated_schema_keys(self) -> None:
        result = _make_test_experiment_result()
        d = result.to_json_dict()
        expected_keys = {
            "experiment_id",
            "generated_at",
            "config_summary",
            "baseline_run_id",
            "treatment_run_id",
            "baseline_skill_bundle_hash",
            "treatment_skill_bundle_hash",
            "noise_floor_summary",
            "verdicts",
            "metric_deltas",
            "signal_divergences",
            "compatibility_warnings",
            "t_correction_used",
        }
        assert set(d.keys()) == expected_keys

    def test_verdicts_serialized(self) -> None:
        result = _make_test_experiment_result()
        d = result.to_json_dict()
        assert len(d["verdicts"]) == 1
        v = d["verdicts"][0]
        assert v["metric_name"] == "total_return"
        assert v["label"] == "POSSIBLE SIGNAL"

    def test_noise_floor_summary_no_sample_values(self) -> None:
        result = _make_test_experiment_result()
        d = result.to_json_dict()
        nf_summary = d["noise_floor_summary"]
        assert "sample_values" not in json.dumps(nf_summary)
        assert "config_hash" in nf_summary
        assert "n_runs" in nf_summary

    def test_run_ids_not_full_runs(self) -> None:
        result = _make_test_experiment_result()
        d = result.to_json_dict()
        assert isinstance(d["baseline_run_id"], str)
        assert isinstance(d["treatment_run_id"], str)


# ── format_experiment_report ─────────────────────────────────────────────


class TestFormatExperimentReport:
    def test_contains_header(self) -> None:
        result = _make_test_experiment_result()
        report = format_experiment_report(result)
        assert "EXPERIMENT REPORT" in report

    def test_contains_verdict(self) -> None:
        result = _make_test_experiment_result()
        report = format_experiment_report(result)
        assert "POSSIBLE SIGNAL" in report

    def test_stale_warning_shown(self) -> None:
        result = _make_test_experiment_result(
            noise_floor_stale=True,
            noise_floor_age_days=47,
        )
        report = format_experiment_report(result)
        assert "stale" in report.lower()

    def test_bundle_mismatch_warning_shown(self) -> None:
        result = _make_test_experiment_result(bundle_mismatch_warning=True)
        report = format_experiment_report(result)
        assert "different bundle" in report.lower()

    def test_fresh_noise_floor_marker(self) -> None:
        result = _make_test_experiment_result(noise_floor_stale=False)
        report = format_experiment_report(result)
        assert "fresh" in report.lower() or "\u2713" in report

    def test_signal_drilldown_included(self) -> None:
        result = _make_test_experiment_result()
        report = format_experiment_report(result, top_n_signals=5)
        assert "SIGNAL DRILLDOWN" in report or "divergen" in report.lower()

    def test_t_correction_noted(self) -> None:
        result = _make_test_experiment_result(t_correction_used=True)
        report = format_experiment_report(result)
        assert "t-correct" in report.lower()


# ── ExperimentRunner ─────────────────────────────────────────────────────


class TestExperimentRunner:
    def _make_store_with_floor(self, tmp_path, config_hash="test_hash", age_days=5):
        store = NoiseFloorStore(tmp_path / "nf.db")
        nf = _make_test_noise_floor(
            config_hash=config_hash,
            created_at=datetime.now() - timedelta(days=age_days),
            last_updated_at=datetime.now() - timedelta(days=age_days),
        )
        store.put(nf)
        return store

    @pytest.mark.asyncio
    async def test_missing_noise_floor_raises_with_command(self, tmp_path) -> None:
        store = NoiseFloorStore(tmp_path / "nf.db")
        runner = ExperimentRunner(
            result_store_path=tmp_path / "runs",
            noise_floor_store=store,
        )
        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()
        with pytest.raises(RuntimeError, match="probe_noise"):
            await runner.run_experiment(
                config=cfg,
                agents_config=agents,
                baseline_skills_bundle="baseline_v1",
                treatment_skills_bundle="live",
            )
        store.close()

    @pytest.mark.asyncio
    async def test_stale_noise_floor_warns_but_proceeds(self, tmp_path) -> None:
        store = self._make_store_with_floor(tmp_path, age_days=45)

        mock_run = _make_backtest_run(skill_bundle_hash="a" * 64)

        runner = ExperimentRunner(
            result_store_path=tmp_path / "runs",
            noise_floor_store=store,
        )
        runner._run_backtest = AsyncMock(return_value=mock_run)

        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()

        with patch(
            "app.modules.backtest.experiment.hash_experiment_config",
            return_value="test_hash",
        ):
            result = await runner.run_experiment(
                config=cfg,
                agents_config=agents,
                baseline_skills_bundle="baseline_v1",
                treatment_skills_bundle="live",
            )
        assert result.noise_floor_stale is True
        assert result.noise_floor_age_days >= 44
        store.close()

    @pytest.mark.asyncio
    async def test_bundle_mismatch_warning(self, tmp_path) -> None:
        store = self._make_store_with_floor(tmp_path)

        mock_run = _make_backtest_run(skill_bundle_hash="different" * 8)

        runner = ExperimentRunner(
            result_store_path=tmp_path / "runs",
            noise_floor_store=store,
        )
        runner._run_backtest = AsyncMock(return_value=mock_run)

        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()

        with patch(
            "app.modules.backtest.experiment.hash_experiment_config",
            return_value="test_hash",
        ):
            result = await runner.run_experiment(
                config=cfg,
                agents_config=agents,
                baseline_skills_bundle="baseline_v1",
                treatment_skills_bundle="live",
            )
        assert result.bundle_mismatch_warning is True
        store.close()

    @pytest.mark.asyncio
    async def test_successful_experiment_produces_verdicts(self, tmp_path) -> None:
        store = self._make_store_with_floor(tmp_path)

        # Use the noise floor's skill_bundle_hash so no mismatch
        nf = store.get("test_hash")
        mock_run = _make_backtest_run(
            skill_bundle_hash=nf.skill_bundle_hash,
            metrics=_make_performance_metrics(total_return=0.12),
        )

        runner = ExperimentRunner(
            result_store_path=tmp_path / "runs",
            noise_floor_store=store,
        )
        runner._run_backtest = AsyncMock(return_value=mock_run)

        cfg = _make_backtest_config(use_llm_agents=True)
        agents = _make_agents_config()

        with patch(
            "app.modules.backtest.experiment.hash_experiment_config",
            return_value="test_hash",
        ):
            result = await runner.run_experiment(
                config=cfg,
                agents_config=agents,
                baseline_skills_bundle="baseline_v1",
                treatment_skills_bundle="live",
            )
        assert result.baseline_run_id is not None
        assert result.treatment_run_id is not None
        assert result.noise_floor_stale is False
        assert result.bundle_mismatch_warning is False
        store.close()


# ── save_experiment_result ───────────────────────────────────────────────


class TestSaveExperimentResult:
    def test_saves_json_file(self, tmp_path: Path) -> None:
        result = _make_test_experiment_result()
        path = save_experiment_result(result, experiments_dir=tmp_path)
        assert path.exists()
        assert path.suffix == ".json"
        data = json.loads(path.read_text())
        assert data["baseline_run_id"] == "baseline_run"

    def test_creates_dir_if_missing(self, tmp_path: Path) -> None:
        experiments_dir = tmp_path / "nested" / "experiments"
        result = _make_test_experiment_result()
        path = save_experiment_result(result, experiments_dir=experiments_dir)
        assert path.exists()
