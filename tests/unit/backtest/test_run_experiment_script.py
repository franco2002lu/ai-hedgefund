"""Unit tests for scripts/run_experiment.py — CLI argument parsing."""

from __future__ import annotations

import pytest

from scripts.run_experiment import build_parser


class TestRunExperimentArgParsing:
    def _parse(self, args: list[str]):
        return build_parser().parse_args(args)

    def test_required_args(self) -> None:
        with pytest.raises(SystemExit):
            self._parse([])

    def test_minimal_args(self) -> None:
        args = self._parse(
            [
                "--preset",
                "medium",
                "--branch",
                "growth",
                "--end-date",
                "2025-12-31",
                "--baseline-bundle",
                "baseline_v1",
                "--treatment-bundle",
                "live",
            ]
        )
        assert args.preset == "medium"
        assert args.branch == "growth"
        assert args.baseline_bundle == "baseline_v1"
        assert args.treatment_bundle == "live"

    def test_t_correction_flag(self) -> None:
        args = self._parse(
            [
                "--preset",
                "quick",
                "--branch",
                "growth",
                "--end-date",
                "2025-12-31",
                "--baseline-bundle",
                "a",
                "--treatment-bundle",
                "b",
                "--t-correction",
            ]
        )
        assert args.t_correction is True

    def test_t_correction_default_false(self) -> None:
        args = self._parse(
            [
                "--preset",
                "quick",
                "--branch",
                "growth",
                "--end-date",
                "2025-12-31",
                "--baseline-bundle",
                "a",
                "--treatment-bundle",
                "b",
            ]
        )
        assert args.t_correction is False

    def test_report_out_flag(self) -> None:
        args = self._parse(
            [
                "--preset",
                "quick",
                "--branch",
                "growth",
                "--end-date",
                "2025-12-31",
                "--baseline-bundle",
                "a",
                "--treatment-bundle",
                "b",
                "--report-out",
                "/tmp/report.txt",
            ]
        )
        assert args.report_out == "/tmp/report.txt"

    def test_json_flag(self) -> None:
        args = self._parse(
            [
                "--preset",
                "quick",
                "--branch",
                "growth",
                "--end-date",
                "2025-12-31",
                "--baseline-bundle",
                "a",
                "--treatment-bundle",
                "b",
                "--json",
            ]
        )
        assert args.json is True
