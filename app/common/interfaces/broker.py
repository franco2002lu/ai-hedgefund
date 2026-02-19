from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.common.models.order import OrderRequest, Order
from app.common.models.trade import Trade


class OrderResult(BaseModel):
    success: bool
    order_id: str | None = None
    trade: Trade | None = None
    rejection_reason: str | None = None


class AccountInfo(BaseModel):
    buying_power: float
    cash: float
    portfolio_value: float
    positions: list[dict]


class BrokerAdapter(ABC):
    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> OrderResult:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Order:
        ...

    @abstractmethod
    async def get_account_info(self) -> AccountInfo:
        ...

    @abstractmethod
    def supports_asset_class(self, asset_class: str) -> bool:
        ...
