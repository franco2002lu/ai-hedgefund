from datetime import datetime

from pydantic import BaseModel

from app.common.enums import OrderSide, OrderStatus, OrderType, TimeInForce


class OrderRequest(BaseModel):
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


class Order(BaseModel):
    id: str
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce

    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    average_fill_price: float = 0.0
    commission: float = 0.0

    confidence: float | None = None
    reasoning: str | None = None
    agent_signals: dict | None = None

    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
