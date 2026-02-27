from abc import ABC, abstractmethod
from datetime import datetime

from app.common.enums import OrderStatus
from app.common.events.base import BaseEvent
from app.common.models.order import Order
from app.common.models.portfolio import PortfolioSnapshot, PortfolioSummary
from app.common.models.position import Position
from app.common.models.trade import Trade


class PortfolioRepository(ABC):
    @abstractmethod
    async def get_by_branch(self, branch_id: str) -> PortfolioSummary | None: ...

    @abstractmethod
    async def create(
        self, branch_id: str, branch_type: str, initial_cash: float, margin_requirement: float
    ) -> PortfolioSummary: ...

    @abstractmethod
    async def update_cash(self, branch_id: str, amount: float, reason: str) -> PortfolioSummary: ...

    @abstractmethod
    async def get_fund_summary(self, fund_id: str) -> dict: ...


class PositionRepository(ABC):
    @abstractmethod
    async def get_by_portfolio(self, portfolio_id: str) -> list[Position]: ...

    @abstractmethod
    async def get_by_symbol(self, portfolio_id: str, symbol: str) -> Position | None: ...

    @abstractmethod
    async def upsert(self, position: Position) -> Position: ...

    @abstractmethod
    async def delete_if_flat(self, portfolio_id: str, instrument_id: str) -> bool: ...


class SnapshotRepository(ABC):
    @abstractmethod
    async def create(self, portfolio_id: str, branch_id: str) -> PortfolioSnapshot: ...

    @abstractmethod
    async def list_by_branch(
        self, branch_id: str, limit: int = 30, offset: int = 0
    ) -> tuple[list[PortfolioSnapshot], int]: ...


class OrderRepository(ABC):
    @abstractmethod
    async def create(self, order: Order) -> Order: ...

    @abstractmethod
    async def get_by_id(self, order_id: str) -> Order | None: ...

    @abstractmethod
    async def update_status(self, order_id: str, status: OrderStatus, **kwargs) -> Order: ...

    @abstractmethod
    async def list_orders(
        self,
        branch_id: str | None = None,
        status: OrderStatus | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Order], int]: ...


class TradeRepository(ABC):
    @abstractmethod
    async def create(self, trade: Trade) -> Trade: ...

    @abstractmethod
    async def get_by_id(self, trade_id: str) -> Trade | None: ...

    @abstractmethod
    async def list_trades(
        self,
        branch_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Trade], int]: ...


class EventLogRepository(ABC):
    @abstractmethod
    async def append(self, event: BaseEvent) -> None: ...

    @abstractmethod
    async def query(
        self,
        event_type: str | None = None,
        branch_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]: ...
