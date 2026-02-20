"""
Single composition root for the entire application.

All service and repository wiring lives here. API routes use FastAPI's
Depends() to obtain services; modules never instantiate their own
dependencies.

Lifecycle rules:
  - Singletons (stateless): created once at startup via init_services()
  - Per-request (session-scoped): created per request via Depends() functions
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.modules.data_platform.service import DataPlatformService
from app.modules.event_log.repository import PostgresEventLogRepository
from app.modules.event_log.service import EventLogService
from app.modules.portfolio.repository import (
    PostgresPortfolioRepository,
    PostgresPositionRepository,
    PostgresSnapshotRepository,
)
from app.modules.portfolio.service import PortfolioService
from app.modules.trade_execution.adapters.paper import PaperTradingAdapter
from app.modules.trade_execution.repository import (
    PostgresOrderRepository,
    PostgresTradeRepository,
)
from app.modules.trade_execution.service import TradeExecutionService


# ---------------------------------------------------------------------------
# Application-scoped singletons (initialized once at startup)
# ---------------------------------------------------------------------------

_data_platform_service: DataPlatformService | None = None
_paper_broker: PaperTradingAdapter | None = None


def init_services(data_platform_service: DataPlatformService) -> None:
    """Called once from app lifespan (main.py) after adapter registry is configured."""
    global _data_platform_service, _paper_broker
    _data_platform_service = data_platform_service
    _paper_broker = PaperTradingAdapter(data_platform_service=data_platform_service)


# ---------------------------------------------------------------------------
# Singleton accessors (used as FastAPI dependencies)
# ---------------------------------------------------------------------------


def get_data_platform_service() -> DataPlatformService:
    if _data_platform_service is None:
        raise RuntimeError("DataPlatformService not initialized — call init_services() first")
    return _data_platform_service


def get_paper_broker() -> PaperTradingAdapter:
    if _paper_broker is None:
        raise RuntimeError("PaperTradingAdapter not initialized — call init_services() first")
    return _paper_broker


# ---------------------------------------------------------------------------
# Per-request service factories (one session shared across all repos)
# ---------------------------------------------------------------------------


async def get_portfolio_service(
    session: AsyncSession = Depends(get_session),
) -> PortfolioService:
    return PortfolioService(
        portfolio_repo=PostgresPortfolioRepository(session),
        position_repo=PostgresPositionRepository(session),
        snapshot_repo=PostgresSnapshotRepository(session),
        event_log=PostgresEventLogRepository(session),
    )


async def get_trade_execution_service(
    session: AsyncSession = Depends(get_session),
) -> TradeExecutionService:
    event_log = PostgresEventLogRepository(session)
    portfolio_service = PortfolioService(
        portfolio_repo=PostgresPortfolioRepository(session),
        position_repo=PostgresPositionRepository(session),
        snapshot_repo=PostgresSnapshotRepository(session),
        event_log=event_log,
    )
    return TradeExecutionService(
        order_repo=PostgresOrderRepository(session),
        trade_repo=PostgresTradeRepository(session),
        broker=get_paper_broker(),
        event_log=event_log,
        portfolio_service=portfolio_service,
    )


async def get_event_log_service(
    session: AsyncSession = Depends(get_session),
) -> EventLogService:
    return EventLogService(PostgresEventLogRepository(session))
