from app.common.enums import ExecutionMode, OrderSide, OrderStatus, OrderType, TimeInForce
from app.common.events.base import BaseEvent


class TradeRequestedEvent(BaseEvent):
    event_type: str = "trade.requested"

    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY

    confidence: float | None = None
    reasoning: str | None = None
    agent_signals: dict | None = None


class TradeExecutedEvent(BaseEvent):
    event_type: str = "trade.executed"

    trade_id: str
    order_id: str
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    quantity: float
    fill_price: float
    commission: float = 0.0
    slippage: float = 0.0
    execution_mode: ExecutionMode


class TradeRejectedEvent(BaseEvent):
    event_type: str = "trade.rejected"

    order_id: str
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    quantity: float
    rejection_reason: str


class OrderStatusChangedEvent(BaseEvent):
    event_type: str = "order.status_changed"

    order_id: str
    branch_id: str
    previous_status: OrderStatus
    new_status: OrderStatus
    filled_quantity: float = 0.0
    average_fill_price: float = 0.0
