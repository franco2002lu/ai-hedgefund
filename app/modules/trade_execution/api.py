"""Trade Execution module API routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.common.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from app.common.models.order import OrderRequest
from app.common.schemas.pagination import PaginatedResponse
from app.dependencies import get_trade_execution_service
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


# --- Routes ---


@router.post("/orders")
async def submit_order(
    req: SubmitOrderRequest,
    service: TradeExecutionService = Depends(get_trade_execution_service),
):
    order_req = OrderRequest(**req.model_dump())
    result = await service.submit_order(order_req)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    service: TradeExecutionService = Depends(get_trade_execution_service),
):
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
    service: TradeExecutionService = Depends(get_trade_execution_service),
):
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
    service: TradeExecutionService = Depends(get_trade_execution_service),
):
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
    service: TradeExecutionService = Depends(get_trade_execution_service),
):
    trade = await service.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    return trade.model_dump()
