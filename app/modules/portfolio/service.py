"""Portfolio service — business logic for portfolio management."""

from datetime import UTC, datetime

from app.common.enums import OrderSide
from app.common.events.portfolio import PortfolioSnapshotEvent, PortfolioUpdatedEvent
from app.common.interfaces.repositories import (
    EventLogRepository,
    PortfolioRepository,
    PositionRepository,
    SnapshotRepository,
)
from app.common.models.portfolio import PortfolioSnapshot, PortfolioSummary
from app.common.models.position import Position
from app.common.models.trade import Trade


class PortfolioService:
    def __init__(
        self,
        portfolio_repo: PortfolioRepository,
        position_repo: PositionRepository,
        snapshot_repo: SnapshotRepository,
        event_log: EventLogRepository,
    ):
        self.portfolio_repo = portfolio_repo
        self.position_repo = position_repo
        self.snapshot_repo = snapshot_repo
        self.event_log = event_log

    async def get_portfolio(self, branch_id: str) -> PortfolioSummary | None:
        summary = await self.portfolio_repo.get_by_branch(branch_id)
        if summary:
            positions = await self.position_repo.get_by_portfolio(summary.id)
            summary.positions = positions
        return summary

    async def create_portfolio(
        self,
        branch_id: str,
        branch_type: str,
        initial_cash: float,
        margin_requirement: float = 0.0,
    ) -> PortfolioSummary:
        return await self.portfolio_repo.create(
            branch_id=branch_id,
            branch_type=branch_type,
            initial_cash=initial_cash,
            margin_requirement=margin_requirement,
        )

    async def adjust_cash(self, branch_id: str, amount: float, reason: str) -> PortfolioSummary:
        summary = await self.portfolio_repo.update_cash(branch_id, amount, reason)

        await self.event_log.append(
            PortfolioUpdatedEvent(
                source="portfolio_service",
                portfolio_id=summary.id,
                branch_id=branch_id,
                trigger=reason,
                cash=summary.cash,
                nav=summary.nav,
                margin_used=summary.margin_used,
                total_long_exposure=summary.total_long_exposure,
                total_short_exposure=summary.total_short_exposure,
                unrealized_pnl=summary.unrealized_pnl,
                realized_pnl=summary.realized_pnl,
            )
        )
        return summary

    async def get_positions(self, branch_id: str) -> list[Position]:
        summary = await self.portfolio_repo.get_by_branch(branch_id)
        if summary is None:
            raise ValueError(f"No portfolio for branch {branch_id}")
        return await self.position_repo.get_by_portfolio(summary.id)

    async def get_position_by_symbol(self, branch_id: str, symbol: str) -> Position | None:
        summary = await self.portfolio_repo.get_by_branch(branch_id)
        if summary is None:
            raise ValueError(f"No portfolio for branch {branch_id}")
        return await self.position_repo.get_by_symbol(summary.id, symbol)

    async def take_snapshot(self, branch_id: str) -> PortfolioSnapshot:
        summary = await self.portfolio_repo.get_by_branch(branch_id)
        if summary is None:
            raise ValueError(f"No portfolio for branch {branch_id}")

        snapshot = await self.snapshot_repo.create(summary.id, branch_id)

        await self.event_log.append(
            PortfolioSnapshotEvent(
                source="portfolio_service",
                snapshot_id=snapshot.id,
                portfolio_id=snapshot.portfolio_id,
                branch_id=branch_id,
                cash=snapshot.cash,
                nav=snapshot.nav,
                total_long_exposure=snapshot.total_long_exposure,
                total_short_exposure=snapshot.total_short_exposure,
                gross_exposure=snapshot.gross_exposure,
                net_exposure=snapshot.net_exposure,
                unrealized_pnl=snapshot.unrealized_pnl,
                realized_pnl=snapshot.realized_pnl,
                margin_used=snapshot.margin_used,
                position_count=snapshot.position_count,
            )
        )
        return snapshot

    async def list_snapshots(
        self, branch_id: str, limit: int = 30, offset: int = 0
    ) -> tuple[list[PortfolioSnapshot], int]:
        return await self.snapshot_repo.list_by_branch(branch_id, limit, offset)

    async def get_fund_summary(self, fund_id: str) -> dict:
        return await self.portfolio_repo.get_fund_summary(fund_id)

    async def handle_trade_executed(self, trade: Trade) -> None:
        """
        Called by Trade Execution module after a fill.
        Updates position, recalculates cash/margin/PnL, logs event.
        """
        summary = await self.portfolio_repo.get_by_branch(trade.branch_id)
        if summary is None:
            raise ValueError(f"No portfolio for branch {trade.branch_id}")

        # Get or create position
        position = await self.position_repo.get_by_symbol(summary.id, trade.symbol)
        if position is None:
            position = Position(
                id="",  # Will be set by DB
                portfolio_id=summary.id,
                instrument_id=trade.instrument_id,
                symbol=trade.symbol,
                updated_at=datetime.now(UTC),
            )

        trade_cost = trade.price * trade.quantity + trade.commission
        trade_proceeds = trade.price * trade.quantity - trade.commission

        if trade.side == OrderSide.BUY:
            position.long_quantity += trade.quantity
            position.long_cost_basis += trade_cost
            cash_delta = -trade_cost

        elif trade.side == OrderSide.SELL:
            if position.long_quantity < trade.quantity:
                raise ValueError(f"Cannot sell {trade.quantity} of {trade.symbol}: only hold {position.long_quantity}")
            # Calculate realized P&L (average cost basis)
            avg_cost = position.long_cost_basis / position.long_quantity if position.long_quantity > 0 else 0
            realized = (trade.price - avg_cost) * trade.quantity - trade.commission
            position.realized_pnl_long += realized
            position.long_cost_basis -= avg_cost * trade.quantity
            position.long_quantity -= trade.quantity
            cash_delta = trade_proceeds

        elif trade.side == OrderSide.SHORT:
            position.short_quantity += trade.quantity
            position.short_cost_basis += trade_proceeds
            margin_required = trade.price * trade.quantity * float(summary.margin_requirement)
            position.short_margin_used += margin_required
            cash_delta = trade_proceeds

        elif trade.side == OrderSide.COVER:
            if position.short_quantity < trade.quantity:
                raise ValueError(
                    f"Cannot cover {trade.quantity} of {trade.symbol}: only short {position.short_quantity}"
                )
            avg_short_price = position.short_cost_basis / position.short_quantity if position.short_quantity > 0 else 0
            realized = (avg_short_price - trade.price) * trade.quantity - trade.commission
            position.realized_pnl_short += realized
            position.short_cost_basis -= avg_short_price * trade.quantity
            margin_release = (
                position.short_margin_used * (trade.quantity / position.short_quantity)
                if position.short_quantity > 0
                else 0
            )
            position.short_margin_used -= margin_release
            position.short_quantity -= trade.quantity
            cash_delta = -trade_cost
        else:
            raise ValueError(f"Unknown trade side: {trade.side}")

        # Upsert position
        position.updated_at = datetime.now(UTC)
        await self.position_repo.upsert(position)

        # Clean up flat positions
        if position.long_quantity == 0 and position.short_quantity == 0:
            await self.position_repo.delete_if_flat(summary.id, trade.instrument_id)

        # Recalculate portfolio aggregates
        all_positions = await self.position_repo.get_by_portfolio(summary.id)
        total_long_exposure = sum(p.long_cost_basis for p in all_positions)
        total_short_exposure = sum(p.short_cost_basis for p in all_positions)
        total_margin_used = sum(p.short_margin_used for p in all_positions)
        total_realized = sum(p.realized_pnl_long + p.realized_pnl_short for p in all_positions)

        new_cash = float(summary.cash) + cash_delta
        new_nav = new_cash + total_long_exposure

        await self.portfolio_repo.update_portfolio_fields(
            trade.branch_id,
            cash=new_cash,
            nav=new_nav,
            total_long_exposure=total_long_exposure,
            total_short_exposure=total_short_exposure,
            margin_used=total_margin_used,
            realized_pnl=total_realized,
        )

        # Log event
        await self.event_log.append(
            PortfolioUpdatedEvent(
                source="portfolio_service",
                portfolio_id=summary.id,
                branch_id=trade.branch_id,
                trigger="trade_executed",
                trade_id=trade.id,
                cash=new_cash,
                nav=new_nav,
                margin_used=total_margin_used,
                total_long_exposure=total_long_exposure,
                total_short_exposure=total_short_exposure,
                unrealized_pnl=0.0,  # Requires market prices to compute
                realized_pnl=total_realized,
            )
        )
