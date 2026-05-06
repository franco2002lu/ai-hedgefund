from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode

# Known equities branch names. Extend here when adding new branches.
VALID_EQUITIES_BRANCHES: frozenset[str] = frozenset({"growth", "value"})


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://hedgefund:localdev@localhost:5433/hedgefund"

    # Execution
    execution_mode: str = "paper"

    # Paper trading
    paper_slippage_bps: float = 5.0
    paper_commission_per_trade: float = 0.0

    # Cache TTLs (seconds)
    cache_prices_ttl: int = 60
    cache_fundamentals_ttl: int = 3600
    cache_news_ttl: int = 300

    # App
    app_name: str = "AI Hedge Fund"
    debug: bool = False

    # Equities weekly pipeline (overridable via HEDGE_EQUITIES_*)
    equities_top_n: int = 50
    # NoDecode: tells pydantic-settings NOT to JSON-decode the env var,
    # so our @field_validator receives the raw comma-separated string.
    equities_enabled_branches: Annotated[list[str], NoDecode] = ["growth", "value"]

    @field_validator("equities_enabled_branches", mode="before")
    @classmethod
    def _split_branches(cls, v):
        if isinstance(v, str):
            return [b.strip() for b in v.split(",") if b.strip()]
        return v

    @field_validator("equities_enabled_branches", mode="after")
    @classmethod
    def _validate_branch_names(cls, v: list[str]) -> list[str]:
        invalid = sorted(set(v) - VALID_EQUITIES_BRANCHES)
        if invalid:
            valid = sorted(VALID_EQUITIES_BRANCHES)
            raise ValueError(
                f"Unknown branches in HEDGE_EQUITIES_ENABLED_BRANCHES: {invalid}. "
                f"Valid: {valid}"
            )
        return v

    model_config = {"env_prefix": "HEDGE_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
