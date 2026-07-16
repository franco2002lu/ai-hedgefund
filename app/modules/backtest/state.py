"""In-memory repository implementations for backtesting."""

import uuid
from datetime import UTC, datetime

from app.common.enums import OrderStatus
from app.common.events.base import BaseEvent
from app.common.interfaces.repositories import (
    EventLogRepository,
    OrderRepository,
    PortfolioRepository,
    PositionRepository,
    SnapshotRepository,
    TradeRepository,
)
from app.common.models.order import Order
from app.common.models.portfolio import PortfolioSnapshot, PortfolioSummary
from app.common.models.position import Position
from app.common.models.trade import Trade


class InMemoryPortfolioRepository(PortfolioRepository):
    def __init__(self) -> None:
        self._store: dict[str, PortfolioSummary] = {}

    async def create(
        self,
        branch_id: str,
        branch_type: str,
        initial_cash: float,
        margin_requirement: float,
    ) -> PortfolioSummary:
        portfolio = PortfolioSummary(
            id=str(uuid.uuid4()),
            branch_id=branch_id,
            branch_type=branch_type,
            cash=initial_cash,
            allocated_capital=initial_cash,
            margin_requirement=margin_requirement,
            margin_used=0.0,
            nav=initial_cash,
            total_long_exposure=0.0,
            total_short_exposure=0.0,
            gross_exposure=0.0,
            net_exposure=0.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            updated_at=datetime.now(UTC),
        )
        self._store[branch_id] = portfolio
        return portfolio

    async def get_by_branch(self, branch_id: str) -> PortfolioSummary | None:
        return self._store.get(branch_id)

    async def update_cash(self, branch_id: str, amount: float, reason: str) -> PortfolioSummary:
        portfolio = self._store[branch_id]
        updated = portfolio.model_copy(update={"cash": portfolio.cash + amount})
        self._store[branch_id] = updated
        return updated

    async def update_portfolio_fields(self, branch_id: str, **fields) -> PortfolioSummary:
        portfolio = self._store[branch_id]
        updated = portfolio.model_copy(update=fields)
        self._store[branch_id] = updated
        return updated

    async def get_fund_summary(self, fund_id: str) -> dict:
        return {}


class InMemoryPositionRepository(PositionRepository):
    def __init__(self) -> None:
        self._store: dict[str, Position] = {}

    async def upsert(self, position: Position) -> Position:
        if not position.id:
            position.id = str(uuid.uuid4())
        self._store[position.id] = position
        return position

    async def get_by_portfolio(self, portfolio_id: str) -> list[Position]:
        return [p for p in self._store.values() if p.portfolio_id == portfolio_id]

    async def get_by_symbol(self, portfolio_id: str, symbol: str) -> Position | None:
        for p in self._store.values():
            if p.portfolio_id == portfolio_id and p.symbol == symbol:
                return p
        return None

    async def delete_if_flat(self, portfolio_id: str, instrument_id: str) -> bool:
        to_delete = [
            key
            for key, p in self._store.items()
            if p.portfolio_id == portfolio_id
            and p.instrument_id == instrument_id
            and p.long_quantity == 0.0
            and p.short_quantity == 0.0
        ]
        for key in to_delete:
            del self._store[key]
        return len(to_delete) > 0


class InMemorySnapshotRepository(SnapshotRepository):
    def __init__(self) -> None:
        self._store: list[PortfolioSnapshot] = []

    async def create(
        self, portfolio_id: str, branch_id: str, positions_detail: list[dict] | None = None
    ) -> PortfolioSnapshot:
        snapshot = PortfolioSnapshot(
            id=str(uuid.uuid4()),
            portfolio_id=portfolio_id,
            branch_id=branch_id,
            cash=0.0,
            nav=0.0,
            total_long_exposure=0.0,
            total_short_exposure=0.0,
            gross_exposure=0.0,
            net_exposure=0.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            margin_used=0.0,
            position_count=0,
            positions_detail=positions_detail,
            snapshot_at=datetime.now(UTC),
        )
        self._store.append(snapshot)
        return snapshot

    async def list_by_branch(
        self, branch_id: str, limit: int = 30, offset: int = 0
    ) -> tuple[list[PortfolioSnapshot], int]:
        filtered = [s for s in self._store if s.branch_id == branch_id]
        total = len(filtered)
        return filtered[offset : offset + limit], total

    async def latest_by_branch(self, branch_id: str, before: datetime | None = None) -> PortfolioSnapshot | None:
        rows = [s for s in self._store if s.branch_id == branch_id]
        if before is not None:
            rows = [s for s in rows if s.snapshot_at < before]
        return max(rows, key=lambda s: s.snapshot_at, default=None)


class InMemoryOrderRepository(OrderRepository):
    def __init__(self) -> None:
        self._store: dict[str, Order] = {}

    async def create(self, order: Order) -> Order:
        self._store[order.id] = order
        return order

    async def get_by_id(self, order_id: str) -> Order | None:
        return self._store.get(order_id)

    async def update_status(self, order_id: str, status: OrderStatus, **kwargs) -> Order:
        order = self._store[order_id]
        updated = order.model_copy(update={"status": status, **kwargs})
        self._store[order_id] = updated
        return updated

    async def list_orders(
        self,
        branch_id: str | None = None,
        status: OrderStatus | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Order], int]:
        items = list(self._store.values())
        if branch_id is not None:
            items = [o for o in items if o.branch_id == branch_id]
        if status is not None:
            items = [o for o in items if o.status == status]
        if since is not None:
            items = [o for o in items if o.created_at >= since]
        total = len(items)
        return items[offset : offset + limit], total


class InMemoryTradeRepository(TradeRepository):
    def __init__(self) -> None:
        self._store: list[Trade] = []

    @property
    def all_trades(self) -> list[Trade]:
        return list(self._store)

    async def create(self, trade: Trade) -> Trade:
        self._store.append(trade)
        return trade

    async def get_by_id(self, trade_id: str) -> Trade | None:
        for t in self._store:
            if t.id == trade_id:
                return t
        return None

    async def list_trades(
        self,
        branch_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Trade], int]:
        items = list(self._store)
        if branch_id is not None:
            items = [t for t in items if t.branch_id == branch_id]
        if since is not None:
            items = [t for t in items if t.executed_at >= since]
        total = len(items)
        return items[offset : offset + limit], total


class InMemoryEventLogRepository(EventLogRepository):
    def __init__(self) -> None:
        self._store: list[dict] = []

    async def append(self, event: BaseEvent) -> None:
        self._store.append(event.model_dump())

    async def query(
        self,
        event_type: str | None = None,
        branch_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        items = list(self._store)
        if event_type is not None:
            items = [e for e in items if e.get("event_type") == event_type]
        if branch_id is not None:
            items = [e for e in items if e.get("branch_id") == branch_id]
        if since is not None:
            items = [e for e in items if e.get("timestamp", datetime.min) >= since]
        return items[offset : offset + limit]
