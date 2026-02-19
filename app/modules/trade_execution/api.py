"""Trade Execution module API routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from app.common.models.order import OrderRequest
from app.common.schemas.pagination import PaginatedResponse
from app.db.connection import get_session
from app.modules.data_platform.api import get_data_service
from app.modules.event_log.repository import PostgresEventLogRepository
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

router = APIRouter(prefix="/api/v1", tags=["trade-execution"])


# --- Request schemas ---


class SubmitOrderRequest(BaseModel):
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    quantity: float
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    confidence: float | None = None
    reasoning: str | None = None
    agent_signals: dict | None = None


# --- Dependency ---


def _get_service(session: AsyncSession) -> TradeExecutionService:
    event_log = PostgresEventLogRepository(session)
    portfolio_service = PortfolioService(
        portfolio_repo=PostgresPortfolioRepository(session),
        position_repo=PostgresPositionRepository(session),
        snapshot_repo=PostgresSnapshotRepository(session),
        event_log=event_log,
    )
    data_service = get_data_service()
    broker = PaperTradingAdapter(data_platform_service=data_service)

    return TradeExecutionService(
        order_repo=PostgresOrderRepository(session),
        trade_repo=PostgresTradeRepository(session),
        broker=broker,
        event_log=event_log,
        portfolio_service=portfolio_service,
    )


# --- Routes ---


@router.post("/orders")
async def submit_order(
    req: SubmitOrderRequest,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    order_req = OrderRequest(**req.model_dump())
    result = await service.submit_order(order_req)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    order = await service.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return order.model_dump()


@router.get("/orders")
async def list_orders(
    branch_id: str | None = None,
    status: OrderStatus | None = None,
    since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    orders, total = await service.list_orders(branch_id, status, since, limit, offset)
    return PaginatedResponse(
        items=[o.model_dump() for o in orders],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    ).model_dump()


@router.get("/trades")
async def list_trades(
    branch_id: str | None = None,
    since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    trades, total = await service.list_trades(branch_id, since, limit, offset)
    return PaginatedResponse(
        items=[t.model_dump() for t in trades],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    ).model_dump()


@router.get("/trades/{trade_id}")
async def get_trade(
    trade_id: str,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    trade_repo = PostgresTradeRepository(session)
    trade = await trade_repo.get_by_id(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    return trade.model_dump()
