"""Unit tests for scripts/compare_runs.py — Phase 2 CLI."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.modules.backtest.result_store import save_run
from scripts import compare_runs as compare_runs_script
from tests.unit.backtest.conftest import (
    _make_backtest_config,
    _make_backtest_run,
    _make_stock_signal_record,
)


class TestCompareRunsCLI:
    def test_missing_baseline_run_exits_1(self, tmp_path: Path, capsys) -> None:
        # Only save the treatment run.
        treatment = _make_backtest_run(run_id="treatment1", skill_bundle_hash="b" * 64)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["baseline_does_not_exist", "treatment1", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "baseline_does_not_exist" in captured.err

    def test_missing_treatment_run_exits_1(self, tmp_path: Path, capsys) -> None:
        baseline = _make_backtest_run(run_id="baseline1", skill_bundle_hash="a" * 64)
        save_run(baseline, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["baseline1", "treatment_missing", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 1

    def test_wholly_incompatible_dates_exits_2(self, tmp_path: Path) -> None:
        baseline = _make_backtest_run(
            run_id="baseline_old",
            config=_make_backtest_config(
                start_date=date(2024, 1, 1), end_date=date(2024, 6, 30)
            ),
            skill_bundle_hash="a" * 64,
        )
        treatment = _make_backtest_run(
            run_id="treatment_new",
            config=_make_backtest_config(
                start_date=date(2025, 1, 1), end_date=date(2025, 6, 30)
            ),
            skill_bundle_hash="b" * 64,
        )
        save_run(baseline, runs_dir=tmp_path)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["baseline_old", "treatment_new", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 2

    def test_happy_path_exits_0_with_text_output(self, tmp_path: Path, capsys) -> None:
        baseline = _make_backtest_run(
            run_id="baseline_happy",
            skill_bundle_hash="a" * 64,
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="treatment_happy",
            skill_bundle_hash="b" * 64,
            signals=[
                _make_stock_signal_record(
                    date=date(2025, 6, 2), symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=8, confidence=7,
                )
            ],
        )
        save_run(baseline, runs_dir=tmp_path)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["baseline_happy", "treatment_happy", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        # Banner + metric table + drilldown + drift footer should all appear.
        assert "RAW METRIC DELTAS" in captured.out
        assert "SIGNAL DRILLDOWN" in captured.out
        assert "UNIVERSE DRIFT" in captured.out
        assert "AMZN" in captured.out

    def test_json_flag_emits_curated_json_only(self, tmp_path: Path, capsys) -> None:
        baseline = _make_backtest_run(run_id="b_json", skill_bundle_hash="a" * 64)
        treatment = _make_backtest_run(run_id="t_json", skill_bundle_hash="b" * 64)
        save_run(baseline, runs_dir=tmp_path)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["b_json", "t_json", "--runs-dir", str(tmp_path), "--json"]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        # Output must be valid JSON with no text banner leaking in.
        data = json.loads(captured.out)
        assert data["baseline_run_id"] == "b_json"
        assert data["treatment_run_id"] == "t_json"
        assert "RAW METRIC DELTAS" not in captured.out

    def test_metrics_only_flag_suppresses_drilldown(
        self, tmp_path: Path, capsys
    ) -> None:
        baseline = _make_backtest_run(
            run_id="b_mo",
            skill_bundle_hash="a" * 64,
            signals=[_make_stock_signal_record(bullish_score=7)],
        )
        treatment = _make_backtest_run(
            run_id="t_mo",
            skill_bundle_hash="b" * 64,
            signals=[_make_stock_signal_record(bullish_score=9)],
        )
        save_run(baseline, runs_dir=tmp_path)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["b_mo", "t_mo", "--runs-dir", str(tmp_path), "--metrics-only"]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "RAW METRIC DELTAS" in captured.out
        assert "SIGNAL DRILLDOWN" not in captured.out

    def test_min_confidence_flag_passes_through(
        self, tmp_path: Path, capsys
    ) -> None:
        baseline = _make_backtest_run(
            run_id="b_mc",
            skill_bundle_hash="a" * 64,
            signals=[
                _make_stock_signal_record(
                    symbol="LOW_CONF", bullish_score=5, confidence=3
                )
            ],
        )
        treatment = _make_backtest_run(
            run_id="t_mc",
            skill_bundle_hash="b" * 64,
            signals=[
                _make_stock_signal_record(
                    symbol="LOW_CONF", bullish_score=7, confidence=3
                )
            ],
        )
        save_run(baseline, runs_dir=tmp_path)
        save_run(treatment, runs_dir=tmp_path)

        exit_code = compare_runs_script.main(
            ["b_mc", "t_mc", "--runs-dir", str(tmp_path), "--min-confidence", "5"]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        # The divergence should have been filtered out; drilldown says no divergences.
        assert "no signal divergences" in captured.out.lower()
