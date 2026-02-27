"""
Single FastAPI application — all modules register their routers here.
"""

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings

# Load .env into os.environ so non-HEDGE_ vars (e.g. ANTHROPIC_API_KEY) are available
load_dotenv()
from app.db.connection import engine
from app.dependencies import init_services
from app.modules.data_platform.adapters.yahoo_finance import YahooFinanceAdapter
from app.modules.data_platform.api import router as data_platform_router
from app.modules.data_platform.cache import DataCache
from app.modules.data_platform.rate_limiter import RateLimiter
from app.modules.data_platform.service import DataPlatformService
from app.modules.equities.api import router as equities_router
from app.modules.portfolio.api import router as portfolio_router
from app.modules.trade_execution.api import router as trade_execution_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify DB connection
    async with engine.connect() as conn:
        await conn.execute(__import__("sqlalchemy").text("SELECT 1"))

    # Initialize Data Platform service with adapter registry
    yahoo = YahooFinanceAdapter()
    adapter_registry = {
        "prices": {
            "equity": [yahoo],
            "crypto": [yahoo],
            "all": [yahoo],
        },
        "fundamentals": {
            "equity": [yahoo],
        },
        "news": {
            "all": [yahoo],
        },
    }
    data_service = DataPlatformService(
        adapter_registry=adapter_registry,
        cache=DataCache(),
        rate_limiter=RateLimiter(),
    )
    init_services(data_service)

    yield
    # Shutdown: dispose engine
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)


app.include_router(portfolio_router)
app.include_router(data_platform_router)
app.include_router(trade_execution_router)
app.include_router(equities_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": settings.app_name, "version": "0.1.0"}


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"error": "bad_request", "message": str(exc), "details": None},
    )
