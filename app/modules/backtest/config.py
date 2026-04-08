from datetime import date
from enum import StrEnum
from pathlib import Path

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
    """Per-run config for LLM-mode backtests."""

    cache_signals: bool = True
    """Controls the in-memory ctx.llm_cache that CachedAnalystWrapper writes
    to during a run. When True, every (date, symbol, analyst_type) tuple is
    stored as it is generated, and BacktestEngine reads the cache at end-of-run
    to populate BacktestResult.signals.

    When False, the wrapper bypasses the in-memory cache entirely. The LLM
    analysts still run and trades still execute normally, but
    BacktestResult.signals will be EMPTY because there is nothing for the
    engine to harvest at end-of-run. This is intentional for variance probes
    that want to make every API call fresh — see Phase 3 — but it is a
    silent footgun for any other caller. Leave this True unless you have a
    specific reason to disable it. The CLI hardcodes True (see
    scripts/run_backtest.py).

    Note: this is INDEPENDENT of BacktestConfig.use_llm_response_cache, which
    controls the persistent SQLite cache (data/llm_response_cache.db) that
    deduplicates real API calls across runs.
    """

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
    skills_bundle: str | None = None  # bundle name to load from data/skill_bundles/
    use_llm_response_cache: bool = True  # whether to use the persistent LLM response cache
    llm_response_cache_path: Path = Path("data/llm_response_cache.db")  # cache db location

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
