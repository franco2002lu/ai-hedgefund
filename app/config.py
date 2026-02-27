from pydantic_settings import BaseSettings


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

    model_config = {"env_prefix": "HEDGE_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
