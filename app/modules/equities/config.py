from pydantic import BaseModel, model_validator


class ScreeningConfig(BaseModel):
    """All screening thresholds. Adjust without code changes."""

    # Shared filters
    min_avg_daily_volume: int = 500_000
    min_market_cap: float = 2_000_000_000
    max_days_since_earnings: int = 120
    max_volatility_percentile: float = 95.0
    max_debt_to_equity: float = 5.0

    # Growth-specific
    min_revenue_growth_yoy: float = 0.05
    min_earnings_growth_yoy: float = 0.0
    margin_declining_quarters: int = 2
    min_surprise_pct: float = -0.05
    min_return_6m: float = -0.10
    max_peg_ratio: float = 3.0

    # Value-specific
    max_pe_percentile: float = 60.0
    max_pb_percentile: float = 60.0
    min_fcf_yield: float = 0.0
    min_dividend_yield: float = 0.005
    min_roe: float = 0.08
    max_52w_range_percentile: float = 90.0


class PortfolioConfig(BaseModel):
    """Portfolio construction parameters."""

    target_holdings: int = 20
    min_holdings: int = 10
    max_holdings: int = 30
    max_position_weight: float = 0.50
    min_rebalance_threshold: float = 0.02
    min_composite_score: float = 4.0


class AnalystLLMConfig(BaseModel):
    """Per-analyst LLM configuration. Allows each analyst to use a different model."""

    model: str = "claude-sonnet-4-6"
    temperature: float = 0.3


class AgentsConfig(BaseModel):
    """Agent-level configuration."""

    # Per-analyst LLM settings (each analyst can use a different model)
    news_analyst: AnalystLLMConfig = AnalystLLMConfig()
    fundamentals_analyst: AnalystLLMConfig = AnalystLLMConfig()
    technical_analyst: AnalystLLMConfig = AnalystLLMConfig()
    portfolio_manager: AnalystLLMConfig = AnalystLLMConfig(temperature=0.2)

    max_concurrent_analyses: int = 10

    # Composite score weights (must sum to 1.0).
    # 2026-06-10: reweighted toward fundamentals based on live rank-IC
    # (fund +0.04, news -0.20, tech -0.19 over 5 production weeks) — see
    # docs/superpowers/specs/2026-06-10-attribution-weights-ranking-design.md
    weight_fundamentals: float = 0.60
    weight_news: float = 0.20
    weight_technical: float = 0.20

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "AgentsConfig":
        total = self.weight_fundamentals + self.weight_news + self.weight_technical
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Composite weights must sum to 1.0, got {total}")
        return self


class EquitiesConfig(BaseModel):
    """Top-level equities branch configuration."""

    growth_etf: str = "VOOG"
    value_etf: str = "VOOV"
    screening: ScreeningConfig = ScreeningConfig()
    portfolio: PortfolioConfig = PortfolioConfig()
    agents: AgentsConfig = AgentsConfig()
