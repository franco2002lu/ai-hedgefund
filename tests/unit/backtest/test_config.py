"""Tests for BacktestConfig, RebalanceFrequency, BacktestStatus, LLMBacktestConfig."""

from datetime import date
from pathlib import Path

import pytest

from app.modules.backtest.config import (
    BacktestConfig,
    BacktestStatus,
    LLMBacktestConfig,
    RebalanceFrequency,
)


class TestRebalanceFrequency:
    def test_enum_values(self):
        assert RebalanceFrequency.DAILY == "daily"
        assert RebalanceFrequency.WEEKLY == "weekly"
        assert RebalanceFrequency.BIWEEKLY == "biweekly"
        assert RebalanceFrequency.MONTHLY == "monthly"

    def test_is_str_enum(self):
        assert RebalanceFrequency.WEEKLY == "weekly"
        assert isinstance(RebalanceFrequency.WEEKLY, str)


class TestBacktestStatus:
    def test_enum_values(self):
        assert BacktestStatus.PENDING == "pending"
        assert BacktestStatus.RUNNING == "running"
        assert BacktestStatus.COMPLETED == "completed"
        assert BacktestStatus.FAILED == "failed"
        assert BacktestStatus.CANCELLED == "cancelled"


class TestLLMBacktestConfig:
    def test_defaults(self):
        config = LLMBacktestConfig()
        assert config.cache_signals is True
        assert config.max_llm_calls_per_rebalance == 60

    def test_custom_override(self):
        config = LLMBacktestConfig(cache_signals=False, max_llm_calls_per_rebalance=100)
        assert config.cache_signals is False
        assert config.max_llm_calls_per_rebalance == 100


class TestBacktestConfig:
    def test_defaults(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 6, 28),
        )
        assert config.initial_capital == 1_000_000.0
        assert config.rebalance_frequency == RebalanceFrequency.WEEKLY
        assert config.branch_name == "growth"
        assert config.use_llm_agents is False
        assert config.slippage_bps == 10.0
        assert config.commission_per_trade == 0.0
        assert config.max_participation_rate == 0.10
        assert config.benchmark_symbols == ["SPY"]
        assert config.equities_config_override is None
        assert config.top_n is None

    def test_end_date_must_be_after_start_date(self):
        with pytest.raises(ValueError):
            BacktestConfig(
                start_date=date(2024, 6, 28),
                end_date=date(2024, 1, 2),
            )

    def test_same_start_and_end_date_raises(self):
        with pytest.raises(ValueError):
            BacktestConfig(
                start_date=date(2024, 6, 15),
                end_date=date(2024, 6, 15),
            )

    def test_initial_capital_must_be_positive(self):
        with pytest.raises(ValueError):
            BacktestConfig(
                start_date=date(2024, 1, 2),
                end_date=date(2024, 6, 28),
                initial_capital=0,
            )

    def test_negative_initial_capital_raises(self):
        with pytest.raises(ValueError):
            BacktestConfig(
                start_date=date(2024, 1, 2),
                end_date=date(2024, 6, 28),
                initial_capital=-100_000,
            )

    def test_slippage_bps_must_be_non_negative(self):
        with pytest.raises(ValueError):
            BacktestConfig(
                start_date=date(2024, 1, 2),
                end_date=date(2024, 6, 28),
                slippage_bps=-1,
            )

    def test_zero_slippage_is_valid(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 6, 28),
            slippage_bps=0.0,
        )
        assert config.slippage_bps == 0.0

    def test_custom_equities_config_override(self):
        from app.modules.equities.config import EquitiesConfig

        override = EquitiesConfig()
        config = BacktestConfig(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 6, 28),
            equities_config_override=override,
        )
        assert config.equities_config_override is not None

    def test_llm_config_default_embedded(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 6, 28),
        )
        assert config.llm_config.cache_signals is True


class TestLLMModeConfigFields:
    def test_defaults(self):
        cfg = BacktestConfig(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        assert cfg.skills_bundle is None
        assert cfg.use_llm_response_cache is True
        assert cfg.llm_response_cache_path == Path("data/llm_response_cache.db")

    def test_override_skills_bundle(self):
        cfg = BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            skills_bundle="baseline_v1",
        )
        assert cfg.skills_bundle == "baseline_v1"

    def test_disable_response_cache(self):
        cfg = BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            use_llm_response_cache=False,
        )
        assert cfg.use_llm_response_cache is False
