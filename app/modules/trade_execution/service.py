"""Trade Execution service — order routing, validation, and lifecycle management."""

import uuid
from datetime import UTC, datetime

from app.common.enums import ExecutionMode, OrderSide, OrderStatus
from app.common.events.trade import (
    TradeExecutedEvent,
    TradeRejectedEvent,
    TradeRequestedEvent,
)
from app.common.interfaces.broker import BrokerAdapter
from app.common.interfaces.repositories import (
    EventLogRepository,
    OrderRepository,
    TradeRepository,
)
from app.common.models.order import Order, OrderRequest
from app.modules.portfolio.service import PortfolioService

# Full-exit quantities travel through Decimal->float conversions; treat a
# quantity within this tolerance of the held quantity as "sell everything".
FULL_EXIT_QTY_TOLERANCE = 1e-6


class TradeExecutionService:
    def __init__(
        self,
        order_repo: OrderRepository,
        trade_repo: TradeRepository,
        broker: BrokerAdapter,
        event_log: EventLogRepository,
        portfolio_service: PortfolioService,
    ):
        self.order_repo = order_repo
        self.trade_repo = trade_repo
        self.broker = broker
        self.event_log = event_log
        self.portfolio_service = portfolio_service

    async def submit_order(self, req: OrderRequest) -> dict:
        """
        Full order lifecycle:
        1. Validate cash/position
        2. Persist order
        3. Log trade.requested event
        4. Route to broker
        5. On fill: persist trade, update portfolio, log events
        6. On rejection: update order status, log event
        """
        # Validate. A failed validation is persisted as a REJECTED order row +
        # rejection event — before 2026-07-30 it returned bare, leaving no
        # trace (8 sells ≈$530k silently dropped on 07-20/27, starving buys).
        validation_error = await self._validate_order(req)
        if validation_error:
            order = self._build_order(req)
            order = await self.order_repo.create(order)
            await self.order_repo.update_status(order.id, OrderStatus.REJECTED, rejection_reason=validation_error)
            await self.event_log.append(
                TradeRejectedEvent(
                    source="trade_execution_service",
                    order_id=order.id,
                    branch_id=req.branch_id,
                    instrument_id=req.instrument_id,
                    symbol=req.symbol,
                    side=req.side,
                    quantity=req.quantity,
                    rejection_reason=validation_error,
                )
            )
            return {"success": False, "order_id": order.id, "status": "rejected", "message": validation_error}

        # Clamp near-held SELL quantities to exactly the held quantity so a
        # full exit closes the position to exactly zero (delete_if_flat fires)
        # and can never trip handle_trade_executed's oversell guard. Must
        # happen before the Order row is created so order and trade agree.
        # Clamp-up is safe: order quantities are either 4-dp-grid (delta path)
        # or exact-held (full exits), so nothing legitimate lands a partial
        # within tolerance below the held quantity.
        if req.side == OrderSide.SELL:
            position = await self.portfolio_service.get_position_by_symbol(req.branch_id, req.symbol)
            if position is not None:
                held = position.long_quantity
                if held > 0 and abs(req.quantity - held) <= FULL_EXIT_QTY_TOLERANCE and req.quantity != held:
                    req = req.model_copy(update={"quantity": held})

        # Create order record
        order = self._build_order(req)
        order = await self.order_repo.create(order)

        # Log intent
        await self.event_log.append(
            TradeRequestedEvent(
                source="trade_execution_service",
                branch_id=req.branch_id,
                instrument_id=req.instrument_id,
                symbol=req.symbol,
                side=req.side,
                order_type=req.order_type,
                quantity=req.quantity,
                limit_price=req.limit_price,
                stop_price=req.stop_price,
                time_in_force=req.time_in_force,
                confidence=req.confidence,
                reasoning=req.reasoning,
                agent_signals=req.agent_signals,
            )
        )

        # Submit to broker
        result = await self.broker.submit_order(req)

        if result.success and result.trade:
            # Cash gate at the exact fill price: a BUY that would overdraw the
            # portfolio is rejected, not filled — the paper broker itself has no
            # account state to enforce this.
            if req.side == OrderSide.BUY:
                portfolio = await self.portfolio_service.get_portfolio(req.branch_id)
                cost = result.trade.price * req.quantity + result.trade.commission
                available = float(portfolio.cash) if portfolio else 0.0
                if cost > available + 1e-6:
                    reason = f"Insufficient cash: cost {cost:.2f} > available {available:.2f}"
                    await self.order_repo.update_status(order.id, OrderStatus.REJECTED, rejection_reason=reason)
                    await self.event_log.append(
                        TradeRejectedEvent(
                            source="trade_execution_service",
                            order_id=order.id,
                            branch_id=req.branch_id,
                            instrument_id=req.instrument_id,
                            symbol=req.symbol,
                            side=req.side,
                            quantity=req.quantity,
                            rejection_reason=reason,
                        )
                    )
                    return {
                        "success": False,
                        "order_id": order.id,
                        "status": "rejected",
                        "message": reason,
                    }

            # Update trade with the persisted order ID
            trade = result.trade.model_copy(update={"order_id": order.id})

            # Update order status
            await self.order_repo.update_status(
                order.id,
                OrderStatus.FILLED,
                filled_quantity=req.quantity,
                average_fill_price=trade.price,
                commission=trade.commission,
            )

            # Persist trade
            trade = await self.trade_repo.create(trade)

            # Log execution event
            await self.event_log.append(
                TradeExecutedEvent(
                    source="trade_execution_service",
                    trade_id=trade.id,
                    order_id=order.id,
                    branch_id=req.branch_id,
                    instrument_id=req.instrument_id,
                    symbol=req.symbol,
                    side=req.side,
                    quantity=req.quantity,
                    fill_price=trade.price,
                    commission=trade.commission,
                    slippage=trade.slippage,
                    execution_mode=ExecutionMode.PAPER,
                )
            )

            # Update portfolio
            await self.portfolio_service.handle_trade_executed(trade)

            return {
                "success": True,
                "order_id": order.id,
                "trade_id": trade.id,
                "status": "filled",
                "fill_price": trade.price,
                "quantity": trade.quantity,
                "commission": trade.commission,
                "slippage": trade.slippage,
                "message": f"Filled {req.quantity} {req.symbol} @ {trade.price}",
            }
        else:
            reason = result.rejection_reason or "Unknown rejection"
            await self.order_repo.update_status(
                order.id,
                OrderStatus.REJECTED,
                rejection_reason=reason,
            )
            await self.event_log.append(
                TradeRejectedEvent(
                    source="trade_execution_service",
                    order_id=order.id,
                    branch_id=req.branch_id,
                    instrument_id=req.instrument_id,
                    symbol=req.symbol,
                    side=req.side,
                    quantity=req.quantity,
                    rejection_reason=reason,
                )
            )
            return {
                "success": False,
                "order_id": order.id,
                "status": "rejected",
                "message": reason,
            }

    async def get_order(self, order_id: str) -> Order | None:
        return await self.order_repo.get_by_id(order_id)

    async def list_orders(
        self,
        branch_id: str | None = None,
        status: OrderStatus | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        return await self.order_repo.list_orders(branch_id, status, since, limit, offset)

    async def get_trade(self, trade_id: str):
        return await self.trade_repo.get_by_id(trade_id)

    async def list_trades(
        self,
        branch_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        return await self.trade_repo.list_trades(branch_id, since, limit, offset)

    @staticmethod
    def _build_order(req: OrderRequest) -> Order:
        """One Order shape for every submission path — keep the two callers in lockstep."""
        now = datetime.now(UTC)
        return Order(
            id=str(uuid.uuid4()),
            branch_id=req.branch_id,
            instrument_id=req.instrument_id,
            symbol=req.symbol,
            side=req.side,
            order_type=req.order_type,
            quantity=req.quantity,
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            time_in_force=req.time_in_force,
            status=OrderStatus.PENDING,
            confidence=req.confidence,
            reasoning=req.reasoning,
            agent_signals=req.agent_signals,
            created_at=now,
            updated_at=now,
        )

    async def _validate_order(self, req: OrderRequest) -> str | None:
        """Pre-execution validation. Returns error message or None if valid."""
        portfolio = await self.portfolio_service.get_portfolio(req.branch_id)
        if portfolio is None:
            return f"No portfolio for branch {req.branch_id}"

        if req.side == OrderSide.BUY:
            # No pre-trade cash check here: the exact cost isn't known until
            # the broker returns a fill price. Cash IS enforced at fill time
            # in submit_order, at the exact fill price — see the cash gate
            # there, which rejects (rather than fills) an overdrawing BUY.
            pass

        elif req.side == OrderSide.SELL:
            position = await self.portfolio_service.get_position_by_symbol(req.branch_id, req.symbol)
            held = position.long_quantity if position else 0
            # 1e-6 tolerance: full-exit quantities travel through float
            # conversions; a hair above the held quantity is a full exit,
            # not an oversell.
            if position is None or req.quantity > held + FULL_EXIT_QTY_TOLERANCE:
                return f"Insufficient position: hold {held} {req.symbol}, tried to sell {req.quantity}"

        elif req.side == OrderSide.COVER:
            position = await self.portfolio_service.get_position_by_symbol(req.branch_id, req.symbol)
            if position is None or position.short_quantity < req.quantity:
                held = position.short_quantity if position else 0
                return f"Insufficient short position: hold {held} {req.symbol}, tried to cover {req.quantity}"

        return None
