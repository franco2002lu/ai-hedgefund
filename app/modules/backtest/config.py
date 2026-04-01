from datetime import date
from enum import StrEnum

from pydantic import BaseModel, model_validator

from app.modules.equities.config import EquitiesConfig


class RebalanceFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


class BacktestStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LLMBacktestConfig(BaseModel):
    cache_signals: bool = True
    max_llm_calls_per_rebalance: int = 60


class BacktestConfig(BaseModel):
    start_date: date
    end_date: date
    initial_capital: float = 1_000_000.0
    rebalance_frequency: RebalanceFrequency = RebalanceFrequency.WEEKLY
    branch_name: str = "growth"
    use_llm_agents: bool = False
    llm_config: LLMBacktestConfig = LLMBacktestConfig()
    slippage_bps: float = 10.0
    commission_per_trade: float = 0.0
    max_participation_rate: float = 0.10  # reject orders > 10% of daily volume
    benchmark_symbols: list[str] = ["SPY"]
    equities_config_override: EquitiesConfig | None = None
    top_n: int | None = None  # None = all holdings; positive int = top N by weight
    cash_yield_rate: float = 0.04  # annualized rate earned on uninvested cash

    @model_validator(mode="after")
    def _validate(self) -> "BacktestConfig":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative")
        if self.top_n is not None and self.top_n < 1:
            raise ValueError("top_n must be a positive integer or None")
        if self.cash_yield_rate < 0:
            raise ValueError("cash_yield_rate must be non-negative")
        return self
