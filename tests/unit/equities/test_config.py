"""Unit tests for equities branch configuration."""

import pytest
from pydantic import ValidationError

from app.modules.equities.config import (
    AdaptiveWeightsConfig,
    AgentsConfig,
    AnalystLLMConfig,
    EquitiesConfig,
    PortfolioConfig,
    ScreeningConfig,
)


class TestScreeningConfig:
    def test_default_shared_thresholds(self):
        cfg = ScreeningConfig()
        assert cfg.min_avg_daily_volume == 500_000
        assert cfg.min_market_cap == 2_000_000_000
        assert cfg.max_days_since_earnings == 120
        assert cfg.max_volatility_percentile == 95.0
        assert cfg.max_debt_to_equity == 5.0

    def test_default_growth_thresholds(self):
        cfg = ScreeningConfig()
        assert cfg.min_revenue_growth_yoy == pytest.approx(0.05)
        assert cfg.min_earnings_growth_yoy == pytest.approx(0.0)
        assert cfg.margin_declining_quarters == 2
        assert cfg.min_surprise_pct == pytest.approx(-0.05)
        assert cfg.min_return_6m == pytest.approx(-0.10)
        assert cfg.max_peg_ratio == pytest.approx(3.0)

    def test_default_value_thresholds(self):
        cfg = ScreeningConfig()
        assert cfg.max_pe_percentile == pytest.approx(60.0)
        assert cfg.max_pb_percentile == pytest.approx(60.0)
        assert cfg.min_fcf_yield == pytest.approx(0.0)
        assert cfg.min_dividend_yield == pytest.approx(0.005)
        assert cfg.min_roe == pytest.approx(0.08)
        assert cfg.max_52w_range_percentile == pytest.approx(90.0)

    def test_custom_override(self):
        cfg = ScreeningConfig(min_avg_daily_volume=1_000_000, min_market_cap=5e9)
        assert cfg.min_avg_daily_volume == 1_000_000
        assert cfg.min_market_cap == pytest.approx(5e9)
        # Other defaults remain unchanged
        assert cfg.max_days_since_earnings == 120


class TestPortfolioConfig:
    def test_defaults(self):
        cfg = PortfolioConfig()
        assert cfg.target_holdings == 20
        assert cfg.min_holdings == 10
        assert cfg.max_holdings == 30
        assert cfg.max_position_weight == pytest.approx(0.50)
        assert cfg.min_rebalance_threshold == pytest.approx(0.02)
        assert cfg.min_composite_score == pytest.approx(4.0)

    def test_custom_overrides(self):
        cfg = PortfolioConfig(target_holdings=15, max_position_weight=0.25)
        assert cfg.target_holdings == 15
        assert cfg.max_position_weight == pytest.approx(0.25)


class TestAnalystLLMConfig:
    def test_default_model(self):
        cfg = AnalystLLMConfig()
        assert cfg.model == "claude-sonnet-4-6"

    def test_default_temperature(self):
        cfg = AnalystLLMConfig()
        assert cfg.temperature == pytest.approx(0.3)

    def test_custom_override(self):
        cfg = AnalystLLMConfig(model="claude-opus-4", temperature=0.0)
        assert cfg.model == "claude-opus-4"
        assert cfg.temperature == pytest.approx(0.0)


class TestAgentsConfig:
    def test_weights_sum_to_one(self):
        cfg = AgentsConfig()
        total = cfg.weight_fundamentals + cfg.weight_news + cfg.weight_technical
        assert total == pytest.approx(1.0)

    def test_custom_weights_sum_to_one(self):
        cfg = AgentsConfig(weight_fundamentals=0.5, weight_news=0.3, weight_technical=0.2)
        total = cfg.weight_fundamentals + cfg.weight_news + cfg.weight_technical
        assert total == pytest.approx(1.0)

    def test_invalid_weights_raise_value_error(self):
        with pytest.raises(ValueError, match="must sum to 1.0"):
            AgentsConfig(
                weight_fundamentals=0.5,
                weight_news=0.5,
                weight_technical=0.5,
            )

    def test_each_analyst_has_config(self):
        cfg = AgentsConfig()
        assert isinstance(cfg.news_analyst, AnalystLLMConfig)
        assert isinstance(cfg.fundamentals_analyst, AnalystLLMConfig)
        assert isinstance(cfg.technical_analyst, AnalystLLMConfig)
        assert isinstance(cfg.portfolio_manager, AnalystLLMConfig)

    def test_default_analyst_model_and_temperature(self):
        cfg = AgentsConfig()
        assert cfg.news_analyst.model == "claude-sonnet-4-6"
        assert cfg.news_analyst.temperature == pytest.approx(0.3)
        assert cfg.fundamentals_analyst.model == "claude-sonnet-4-6"
        assert cfg.technical_analyst.model == "claude-sonnet-4-6"

    def test_portfolio_manager_lower_temperature(self):
        cfg = AgentsConfig()
        assert cfg.portfolio_manager.temperature == pytest.approx(0.2)

    def test_max_concurrent_analyses_default(self):
        cfg = AgentsConfig()
        assert cfg.max_concurrent_analyses == 10


class TestEquitiesConfig:
    def test_default_etfs(self):
        cfg = EquitiesConfig()
        assert cfg.growth_etf == "VOOG"
        assert cfg.value_etf == "VOOV"

    def test_nested_configs_accessible(self):
        cfg = EquitiesConfig()
        assert isinstance(cfg.screening, ScreeningConfig)
        assert isinstance(cfg.portfolio, PortfolioConfig)
        assert isinstance(cfg.agents, AgentsConfig)

    def test_nested_config_override(self):
        cfg = EquitiesConfig(
            screening=ScreeningConfig(min_avg_daily_volume=1_000_000),
            portfolio=PortfolioConfig(target_holdings=25),
        )
        assert cfg.screening.min_avg_daily_volume == 1_000_000
        assert cfg.portfolio.target_holdings == 25

    def test_agents_weights_accessible_through_parent(self):
        cfg = EquitiesConfig()
        total = cfg.agents.weight_fundamentals + cfg.agents.weight_news + cfg.agents.weight_technical
        assert total == pytest.approx(1.0)


def test_composite_weights_favor_fundamentals():
    cfg = AgentsConfig()
    assert cfg.weight_fundamentals == 0.60
    assert cfg.weight_news == 0.20
    assert cfg.weight_technical == 0.20


class TestAdaptiveWeightsConfig:
    def test_defaults(self):
        cfg = AdaptiveWeightsConfig()
        assert cfg.enabled is True
        assert cfg.lookback_weeks == 12
        assert cfg.half_life_weeks == pytest.approx(4.0)
        assert cfg.min_history_weeks == 6
        assert cfg.weight_floor == pytest.approx(0.10)
        assert cfg.max_weekly_shift == pytest.approx(0.05)
        assert cfg.ic_tilt_scale == pytest.approx(0.40)
        assert cfg.alert_streak_weeks == 4

    def test_nested_on_agents_config(self):
        assert AgentsConfig().adaptive.enabled is True

    def test_floor_must_leave_feasible_simplex(self):
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(weight_floor=0.40)  # 3*0.40 > 1

    def test_lookback_must_cover_min_history(self):
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(lookback_weeks=4, min_history_weeks=6)

    def test_positive_scales(self):
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(half_life_weeks=0)
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(ic_tilt_scale=0)
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(max_weekly_shift=0)
        with pytest.raises(ValidationError):
            AdaptiveWeightsConfig(max_weekly_shift=1.5)
