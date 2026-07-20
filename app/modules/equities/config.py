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
    # Entries trade down to 0.5% targets; the 2% band above applies only to
    # adjustments of names already held (2026-07-19 Theme A3 decision).
    min_entry_weight: float = 0.005
    min_composite_score: float = 4.0
    # A held stock exits when its composite score drops below
    # max(exit_score_threshold, min_composite_score) or it falls outside
    # max_holdings by conviction rank.
    exit_score_threshold: float = 4.0
    # Fraction of NAV deliberately left uninvested so buy fills (slippage,
    # decision-to-fill drift) cannot overdraw cash. Target weights sum to
    # 1 - cash_buffer_pct.
    cash_buffer_pct: float = 0.01


class AnalystLLMConfig(BaseModel):
    """Per-analyst LLM configuration. Allows each analyst to use a different model."""

    model: str = "claude-sonnet-4-6"
    temperature: float = 0.3


class AdaptiveWeightsConfig(BaseModel):
    """IC-feedback policy for composite weights (see
    docs/superpowers/specs/2026-07-16-adaptive-analyst-weights-design.md).

    Weights tilt off the static prior by trailing rank-IC evidence:
    target ∝ static · exp(EWIC / ic_tilt_scale), then are clamped to a floor
    and a max weekly move and renormalized.
    """

    enabled: bool = True
    lookback_weeks: int = 12
    half_life_weeks: float = 4.0
    min_history_weeks: int = 6
    weight_floor: float = 0.10
    max_weekly_shift: float = 0.05
    ic_tilt_scale: float = 0.40
    alert_streak_weeks: int = 4

    @model_validator(mode="after")
    def _feasible(self) -> "AdaptiveWeightsConfig":
        if self.half_life_weeks <= 0:
            raise ValueError("half_life_weeks must be > 0")
        if self.ic_tilt_scale <= 0:
            raise ValueError("ic_tilt_scale must be > 0")
        if not (0 < self.max_weekly_shift <= 1):
            raise ValueError("max_weekly_shift must be in (0, 1]")
        if not (self.weight_floor > 0 and 3 * self.weight_floor <= 1):
            raise ValueError("weight_floor must be > 0 and 3*floor <= 1")
        if not (1 <= self.min_history_weeks <= self.lookback_weeks):
            raise ValueError("need 1 <= min_history_weeks <= lookback_weeks")
        if self.alert_streak_weeks < 1:
            raise ValueError("alert_streak_weeks must be >= 1")
        return self


class AgentsConfig(BaseModel):
    """Agent-level configuration."""

    # Per-analyst LLM settings (each analyst can use a different model)
    news_analyst: AnalystLLMConfig = AnalystLLMConfig()
    fundamentals_analyst: AnalystLLMConfig = AnalystLLMConfig()
    technical_analyst: AnalystLLMConfig = AnalystLLMConfig()
    portfolio_manager: AnalystLLMConfig = AnalystLLMConfig(temperature=0.2)

    max_concurrent_analyses: int = 10

    # Per-ticker company news (2026-07-17 per-ticker news spec):
    # fetch_limit = raw articles requested per symbol from Yahoo;
    # prompt_cap = filtered company articles rendered per stock prompt.
    company_news_fetch_limit: int = 10
    company_news_prompt_cap: int = 6

    # Composite score weights (must sum to 1.0).
    # 2026-06-10: reweighted toward fundamentals based on live rank-IC
    # (fund +0.04, news -0.20, tech -0.19 over 5 production weeks) — see
    # docs/superpowers/specs/2026-06-10-attribution-weights-ranking-design.md
    weight_fundamentals: float = 0.60
    weight_news: float = 0.20
    weight_technical: float = 0.20

    # IC-feedback loop (2026-07-16 spec); the static weight_* above remain the
    # prior and the fallback whenever adaptation cannot run.
    adaptive: AdaptiveWeightsConfig = AdaptiveWeightsConfig()

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
