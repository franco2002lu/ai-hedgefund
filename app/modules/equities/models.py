from pydantic import BaseModel, field_validator

from app.common.enums import OrderSide
from app.modules.equities.adaptive_weights import AnalystWeightsReport


class UniverseStock(BaseModel):
    """A single stock in the investment universe."""

    symbol: str
    company_name: str
    weight: float = 0.0
    sector: str | None = None
    industry: str | None = None


class StockSignal(BaseModel):
    """Output from a single analyst agent for a single stock."""

    symbol: str
    analyst_type: str  # "news" | "fundamentals" | "technical"
    bullish_score: int  # 1-10
    confidence: int  # 1-10
    summary: str

    @field_validator("bullish_score", "confidence")
    @classmethod
    def _score_in_range(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError(f"Score must be between 1 and 10, got {v}")
        return v

    @field_validator("analyst_type")
    @classmethod
    def _valid_analyst_type(cls, v: str) -> str:
        allowed = {"news", "fundamentals", "technical"}
        if v not in allowed:
            raise ValueError(f"analyst_type must be one of {allowed}, got {v}")
        return v


class CompositeScore(BaseModel):
    """Synthesized score from all analysts for a single stock."""

    symbol: str
    composite_score: float
    composite_confidence: float
    conviction: float  # score * confidence
    target_weight: float = 0.0


class RebalanceOrder(BaseModel):
    """A single rebalancing action."""

    symbol: str
    side: OrderSide  # BUY or SELL
    quantity: float
    reason: str  # "new_position" | "removed_position" | "weight_adjustment"


class RunResult(BaseModel):
    """Result of a full pipeline run."""

    branch_name: str
    universe_count: int
    screened_count: int
    signals: list[StockSignal]
    composite_scores: list[CompositeScore]
    orders: list[RebalanceOrder]
    trades_executed: int
    # Weights the composite actually used (2026-07-16 adaptive weights spec)
    analyst_weights_report: AnalystWeightsReport | None = None
