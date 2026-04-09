"""Unit tests for result_store — backtest run persistence and prompt fingerprinting."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.modules.backtest.config import BacktestConfig
from app.modules.backtest.models import PerformanceMetrics
from app.modules.backtest.result_store import (
    BacktestRun,
    StockSignalRecord,
    hash_skill_bundle,
    list_runs,
    load_run,
    save_run,
)


class TestHashSkillBundle:
    def test_same_content_produces_same_hash(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        for d in (dir_a, dir_b):
            (d / "base").mkdir(parents=True)
            (d / "base" / "fundamentals.md").write_text("# Fundamentals")
            (d / "base" / "news.md").write_text("# News")
            (d / "output_format.md").write_text("## Output")
        assert hash_skill_bundle(dir_a) == hash_skill_bundle(dir_b)

    def test_different_content_produces_different_hash(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        for d in (dir_a, dir_b):
            (d / "base").mkdir(parents=True)
            (d / "output_format.md").write_text("## Output")
        (dir_a / "base" / "fundamentals.md").write_text("# Version A")
        (dir_b / "base" / "fundamentals.md").write_text("# Version B")
        assert hash_skill_bundle(dir_a) != hash_skill_bundle(dir_b)

    def test_ignores_pycache(self, tmp_path):
        """__pycache__ directories should be skipped — they're non-deterministic
        compiled artifacts that shouldn't affect the content hash."""
        dir_base = tmp_path / "bundle"
        (dir_base / "base").mkdir(parents=True)
        (dir_base / "base" / "fundamentals.md").write_text("# content")
        (dir_base / "output_format.md").write_text("## out")
        hash_before = hash_skill_bundle(dir_base)

        # Add a __pycache__ dir with files
        (dir_base / "__pycache__").mkdir()
        (dir_base / "__pycache__" / "something.pyc").write_bytes(b"compiled")
        hash_after = hash_skill_bundle(dir_base)

        assert hash_before == hash_after

    def test_hash_is_stable_hex_string(self, tmp_path):
        (tmp_path / "base").mkdir()
        (tmp_path / "base" / "f.md").write_text("x")
        (tmp_path / "output_format.md").write_text("y")
        h = hash_skill_bundle(tmp_path)
        assert isinstance(h, str)
        assert len(h) == 64  # full sha256 hex
        int(h, 16)  # raises if not valid hex

    def test_missing_directory_raises_file_not_found(self, tmp_path):
        """Hashing a nonexistent path must raise rather than silently
        returning the empty-input sha256, which would tag a backtest run
        with a meaningless 'valid'-looking hash."""
        missing = tmp_path / "does_not_exist"
        with pytest.raises(FileNotFoundError, match="does_not_exist"):
            hash_skill_bundle(missing)

    def test_path_pointing_to_file_raises_file_not_found(self, tmp_path):
        """A path that exists but is a file (not a directory) must also raise."""
        file_path = tmp_path / "not_a_dir.md"
        file_path.write_text("oops")
        with pytest.raises(FileNotFoundError, match="not_a_dir"):
            hash_skill_bundle(file_path)


class TestBacktestRunModel:
    def _make_metrics(self) -> PerformanceMetrics:
        return PerformanceMetrics(
            total_return=0.1, annualized_return=0.1, volatility=0.15, sharpe_ratio=0.67,
            sortino_ratio=1.0, calmar_ratio=0.5, max_drawdown=-0.05, max_drawdown_duration_days=10,
            value_at_risk_95=-0.02, conditional_var_95=-0.03, ulcer_index=0.02, total_trades=5,
            win_rate=0.6, profit_factor=1.5, avg_win=100.0, avg_loss=-50.0, turnover_rate=0.5,
            avg_position_count=10.0, max_position_count=12, avg_long_exposure=0.95,
        )

    def test_backtest_run_round_trips_through_json(self):
        run = BacktestRun(
            run_id="2026-04-07T12-00-00_abc123_medium",
            timestamp=datetime(2026, 4, 7, 12, 0, 0),
            git_sha="deadbeef",
            config=BacktestConfig(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)),
            skill_bundle_name=None,
            skill_bundle_hash="a" * 64,
            metrics=self._make_metrics(),
            benchmarks=[],
            snapshots=[],
            trades=[],
            signals=[
                StockSignalRecord(
                    date=date(2025, 6, 15),
                    symbol="AAPL",
                    analyst_type="fundamentals",
                    bullish_score=7,
                    confidence=8,
                    summary="strong",
                )
            ],
            llm_cache_hits=42,
            llm_cache_misses=8,
        )
        json_str = run.model_dump_json()
        rehydrated = BacktestRun.model_validate_json(json_str)
        assert rehydrated.run_id == run.run_id
        assert rehydrated.skill_bundle_hash == "a" * 64
        assert len(rehydrated.signals) == 1
        assert rehydrated.signals[0].symbol == "AAPL"
        assert rehydrated.llm_cache_hits == 42


class TestSaveLoadListRuns:
    def _make_run(self, run_id: str) -> BacktestRun:
        return BacktestRun(
            run_id=run_id,
            timestamp=datetime(2026, 4, 7, 12, 0, 0),
            git_sha="deadbeef",
            config=BacktestConfig(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)),
            skill_bundle_name=None,
            skill_bundle_hash="a" * 64,
            metrics=TestBacktestRunModel()._make_metrics(),
        )

    def test_save_creates_json_file_and_returns_path(self, tmp_path):
        run = self._make_run("2026-04-07_aaa_medium")
        path = save_run(run, runs_dir=tmp_path)
        assert path.exists()
        assert path.parent == tmp_path
        assert path.name == "2026-04-07_aaa_medium.json"

    def test_load_round_trips(self, tmp_path):
        run = self._make_run("2026-04-07_bbb_quick")
        save_run(run, runs_dir=tmp_path)
        loaded = load_run("2026-04-07_bbb_quick", runs_dir=tmp_path)
        assert loaded.run_id == "2026-04-07_bbb_quick"
        assert loaded.skill_bundle_hash == "a" * 64

    def test_load_missing_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="no_such_run"):
            load_run("no_such_run", runs_dir=tmp_path)

    def test_list_runs_returns_all_saved_runs_sorted_by_timestamp_desc(self, tmp_path):
        from datetime import timedelta

        base_time = datetime(2026, 4, 7, 12, 0, 0)
        for i in range(3):
            run = self._make_run(f"run_{i}")
            run.timestamp = base_time + timedelta(hours=i)
            save_run(run, runs_dir=tmp_path)

        entries = list_runs(runs_dir=tmp_path)
        assert len(entries) == 3
        # Most recent first
        assert entries[0]["run_id"] == "run_2"
        assert entries[2]["run_id"] == "run_0"

    def test_list_runs_empty_dir_returns_empty_list(self, tmp_path):
        entries = list_runs(runs_dir=tmp_path)
        assert entries == []


class TestBacktestRunEffectiveAgentsConfig:
    """effective_agents_config captures the per-analyst LLM settings that actually
    ran, so compare_runs can detect model/temperature drift between runs."""

    def test_round_trip_with_agents_config(self, tmp_path) -> None:
        from app.modules.equities.config import AgentsConfig, AnalystLLMConfig

        agents = AgentsConfig(
            news_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            fundamentals_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
            technical_analyst=AnalystLLMConfig(model="claude-sonnet-4-6", temperature=0.3),
        )
        run = BacktestRun(
            run_id="test_run_with_agents",
            timestamp=datetime(2026, 4, 8, 16, 5, 14),
            git_sha="abc123",
            config=BacktestConfig(
                start_date=date(2025, 1, 1),
                end_date=date(2025, 6, 30),
                branch_name="growth",
            ),
            skill_bundle_name=None,
            skill_bundle_hash="a" * 64,
            metrics=None,
            effective_agents_config=agents,
        )

        save_run(run, runs_dir=tmp_path)
        loaded = load_run("test_run_with_agents", runs_dir=tmp_path)

        assert loaded.effective_agents_config is not None
        assert loaded.effective_agents_config.news_analyst.model == "claude-sonnet-4-6"
        assert loaded.effective_agents_config.fundamentals_analyst.temperature == 0.3
        assert loaded.effective_agents_config.technical_analyst.model == "claude-sonnet-4-6"

    def test_legacy_run_without_agents_config_loads(self, tmp_path) -> None:
        """Existing saved runs from Phase 1 have no effective_agents_config field;
        they must still load, with the field defaulting to None."""
        legacy_json = """{
            "run_id": "legacy_run",
            "timestamp": "2026-04-08T16:05:14",
            "git_sha": "deadbeef",
            "config": {
                "start_date": "2025-01-01",
                "end_date": "2025-06-30",
                "branch_name": "growth"
            },
            "skill_bundle_name": null,
            "skill_bundle_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "metrics": null
        }"""
        (tmp_path / "legacy_run.json").write_text(legacy_json)

        loaded = load_run("legacy_run", runs_dir=tmp_path)
        assert loaded.effective_agents_config is None
