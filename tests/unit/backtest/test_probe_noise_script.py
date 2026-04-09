"""Unit tests for scripts/probe_noise.py — CLI argument parsing and validation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts.probe_noise import build_parser, validate_args


class TestProbeNoiseArgParsing:
    def _parse(self, args: list[str]):
        return build_parser().parse_args(args)

    def test_preset_and_branch_required(self) -> None:
        with pytest.raises(SystemExit):
            self._parse([])

    def test_minimal_args(self) -> None:
        args = self._parse(["--preset", "medium", "--branch", "growth", "--end-date", "2025-12-31"])
        assert args.preset == "medium"
        assert args.branch == "growth"
        assert args.end_date == "2025-12-31"

    def test_runs_default_is_5(self) -> None:
        args = self._parse(["--preset", "quick", "--branch", "growth", "--end-date", "2025-12-31"])
        assert args.runs == 5

    def test_runs_custom(self) -> None:
        args = self._parse(
            [
                "--preset",
                "quick",
                "--branch",
                "growth",
                "--end-date",
                "2025-12-31",
                "--runs",
                "7",
            ]
        )
        assert args.runs == 7

    def test_invalidate_flag(self) -> None:
        args = self._parse(
            [
                "--preset",
                "quick",
                "--branch",
                "growth",
                "--end-date",
                "2025-12-31",
                "--invalidate",
            ]
        )
        assert args.invalidate is True

    def test_force_flag(self) -> None:
        args = self._parse(
            [
                "--preset",
                "quick",
                "--branch",
                "growth",
                "--end-date",
                "2025-12-31",
                "--force",
            ]
        )
        assert args.force is True

    def test_yes_flag(self) -> None:
        args = self._parse(
            [
                "--preset",
                "quick",
                "--branch",
                "growth",
                "--end-date",
                "2025-12-31",
                "--yes",
            ]
        )
        assert args.yes is True

    def test_skills_bundle_flag(self) -> None:
        args = self._parse(
            [
                "--preset",
                "quick",
                "--branch",
                "growth",
                "--end-date",
                "2025-12-31",
                "--skills-bundle",
                "baseline_v1",
            ]
        )
        assert args.skills_bundle == "baseline_v1"


class TestProbeNoiseValidation:
    def test_runs_below_minimum_exits(self) -> None:
        args = MagicMock(runs=2)
        with pytest.raises(SystemExit):
            validate_args(args)

    def test_runs_above_10_warns(self, capsys) -> None:
        args = MagicMock(runs=12)
        validate_args(args)
        captured = capsys.readouterr()
        assert "marginal improvement" in captured.out.lower()

    def test_runs_5_no_warning(self, capsys) -> None:
        args = MagicMock(runs=5)
        validate_args(args)
        captured = capsys.readouterr()
        assert "marginal" not in captured.out.lower()
