from datetime import datetime

from pydantic import BaseModel

from app.common.enums import ExecutionMode, OrderSide


class Trade(BaseModel):
    id: str
    order_id: str
    branch_id: str
    instrument_id: str
    symbol: str

    side: OrderSide
    quantity: float
    price: float
    commission: float = 0.0
    slippage: float = 0.0

    execution_mode: ExecutionMode
    executed_at: datetime
