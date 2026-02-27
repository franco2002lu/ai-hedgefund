"""PostgreSQL repositories for Trade Execution module."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import OrderStatus
from app.common.interfaces.repositories import OrderRepository, TradeRepository
from app.common.models.order import Order
from app.common.models.trade import Trade
from app.db.models import OrderModel, TradeModel


class PostgresOrderRepository(OrderRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, order: Order) -> Order:
        model = OrderModel(
            branch_id=uuid.UUID(order.branch_id),
            instrument_id=uuid.UUID(order.instrument_id),
            symbol=order.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
            quantity=order.quantity,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            time_in_force=order.time_in_force.value,
            status=order.status.value,
            confidence=order.confidence,
            reasoning=order.reasoning,
            agent_signals=order.agent_signals,
        )
        self.session.add(model)
        await self.session.flush()
        return self._to_domain(model)

    async def get_by_id(self, order_id: str) -> Order | None:
        model = await self.session.get(OrderModel, uuid.UUID(order_id))
        return self._to_domain(model) if model else None

    async def update_status(self, order_id: str, status: OrderStatus, **kwargs) -> Order:
        model = await self.session.get(OrderModel, uuid.UUID(order_id))
        if model is None:
            raise ValueError(f"Order {order_id} not found")

        model.status = status.value
        model.updated_at = datetime.now(UTC)

        for key, value in kwargs.items():
            if hasattr(model, key):
                setattr(model, key, value)

        if status == OrderStatus.FILLED:
            model.filled_at = datetime.now(UTC)
        if status == OrderStatus.SUBMITTED:
            model.submitted_at = datetime.now(UTC)

        await self.session.flush()
        return self._to_domain(model)

    async def list_orders(
        self,
        branch_id: str | None = None,
        status: OrderStatus | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Order], int]:
        base = select(OrderModel)
        count_base = select(func.count()).select_from(OrderModel)

        if branch_id is not None:
            base = base.where(OrderModel.branch_id == branch_id)
            count_base = count_base.where(OrderModel.branch_id == branch_id)
        if status is not None:
            base = base.where(OrderModel.status == status.value)
            count_base = count_base.where(OrderModel.status == status.value)
        if since is not None:
            base = base.where(OrderModel.created_at >= since)
            count_base = count_base.where(OrderModel.created_at >= since)

        total = (await self.session.execute(count_base)).scalar()
        stmt = base.order_by(OrderModel.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        orders = [self._to_domain(m) for m in result.scalars().all()]
        return orders, total

    def _to_domain(self, model: OrderModel) -> Order:
        return Order(
            id=str(model.id),
            branch_id=str(model.branch_id),
            instrument_id=str(model.instrument_id),
            symbol=model.symbol,
            side=model.side,
            order_type=model.order_type,
            quantity=float(model.quantity),
            limit_price=float(model.limit_price) if model.limit_price else None,
            stop_price=float(model.stop_price) if model.stop_price else None,
            time_in_force=model.time_in_force,
            status=model.status,
            filled_quantity=float(model.filled_quantity),
            average_fill_price=float(model.average_fill_price),
            commission=float(model.commission),
            confidence=float(model.confidence) if model.confidence else None,
            reasoning=model.reasoning,
            agent_signals=model.agent_signals,
            created_at=model.created_at,
            updated_at=model.updated_at or model.created_at,
            submitted_at=model.submitted_at,
            filled_at=model.filled_at,
        )


class PostgresTradeRepository(TradeRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, trade: Trade) -> Trade:
        model = TradeModel(
            order_id=uuid.UUID(trade.order_id),
            branch_id=uuid.UUID(trade.branch_id),
            instrument_id=uuid.UUID(trade.instrument_id),
            symbol=trade.symbol,
            side=trade.side.value,
            quantity=trade.quantity,
            price=trade.price,
            commission=trade.commission,
            slippage=trade.slippage,
            execution_mode=trade.execution_mode.value,
        )
        self.session.add(model)
        await self.session.flush()
        return self._to_domain(model)

    async def get_by_id(self, trade_id: str) -> Trade | None:
        model = await self.session.get(TradeModel, uuid.UUID(trade_id))
        return self._to_domain(model) if model else None

    async def list_trades(
        self,
        branch_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Trade], int]:
        base = select(TradeModel)
        count_base = select(func.count()).select_from(TradeModel)

        if branch_id is not None:
            base = base.where(TradeModel.branch_id == branch_id)
            count_base = count_base.where(TradeModel.branch_id == branch_id)
        if since is not None:
            base = base.where(TradeModel.executed_at >= since)
            count_base = count_base.where(TradeModel.executed_at >= since)

        total = (await self.session.execute(count_base)).scalar()
        stmt = base.order_by(TradeModel.executed_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        trades = [self._to_domain(m) for m in result.scalars().all()]
        return trades, total

    def _to_domain(self, model: TradeModel) -> Trade:
        return Trade(
            id=str(model.id),
            order_id=str(model.order_id),
            branch_id=str(model.branch_id),
            instrument_id=str(model.instrument_id),
            symbol=model.symbol,
            side=model.side,
            quantity=float(model.quantity),
            price=float(model.price),
            commission=float(model.commission),
            slippage=float(model.slippage),
            execution_mode=model.execution_mode,
            executed_at=model.executed_at,
        )
