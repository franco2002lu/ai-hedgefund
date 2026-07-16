"""Portfolio service — business logic for portfolio management."""

import logging
from datetime import UTC, datetime

from app.common.enums import OrderSide
from app.common.events.portfolio import PortfolioSnapshotEvent, PortfolioUpdatedEvent
from app.common.interfaces.repositories import (
    EventLogRepository,
    PortfolioRepository,
    PositionRepository,
    SnapshotRepository,
)
from app.common.models.portfolio import MarkToMarketResult, PortfolioSnapshot, PortfolioSummary
from app.common.models.position import Position
from app.common.models.trade import Trade

logger = logging.getLogger(__name__)


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

    async def mark_to_market(self, branch_id: str, prices: dict[str, float | None]) -> MarkToMarketResult:
        """Reprice open long positions and persist marked NAV/exposure/unrealized P&L.

        A position with no usable price keeps its cost basis (a live data outage
        must not crater reported NAV — unlike the backtest's zero-out rule).
        """
        summary = await self.portfolio_repo.get_by_branch(branch_id)
        if summary is None:
            raise ValueError(f"No portfolio for branch {branch_id}")

        positions = await self.position_repo.get_by_portfolio(summary.id)
        open_positions = [p for p in positions if p.long_quantity > 0]

        total_mv = 0.0
        total_cost = 0.0
        unpriced = 0
        detail: list[dict] = []
        for p in open_positions:
            price = prices.get(p.symbol)
            if price is None or price <= 0:
                unpriced += 1
                logger.warning("No price for %s — carrying at cost basis", p.symbol)
                price = None
                mv = p.long_cost_basis
            else:
                mv = price * p.long_quantity
            total_mv += mv
            total_cost += p.long_cost_basis
            detail.append(
                {
                    "symbol": p.symbol,
                    "quantity": p.long_quantity,
                    "price": price,
                    "market_value": mv,
                    "cost_basis": p.long_cost_basis,
                    "unrealized_pnl": mv - p.long_cost_basis,
                }
            )

        nav = float(summary.cash) + total_mv
        unrealized = total_mv - total_cost
        for d in detail:
            d["weight"] = d["market_value"] / nav if nav > 0 else 0.0
        detail.sort(key=lambda d: (-d["market_value"], d["symbol"]))

        await self.portfolio_repo.update_portfolio_fields(
            branch_id,
            nav=nav,
            total_long_exposure=total_mv,
            unrealized_pnl=unrealized,
        )
        await self.event_log.append(
            PortfolioUpdatedEvent(
                source="portfolio_service",
                portfolio_id=summary.id,
                branch_id=branch_id,
                trigger="mark_to_market",
                cash=float(summary.cash),
                nav=nav,
                margin_used=float(summary.margin_used),
                total_long_exposure=total_mv,
                total_short_exposure=float(summary.total_short_exposure),
                unrealized_pnl=unrealized,
                realized_pnl=float(summary.realized_pnl),
            )
        )
        return MarkToMarketResult(
            nav=nav,
            cash=float(summary.cash),
            unrealized_pnl=unrealized,
            realized_pnl=float(summary.realized_pnl),
            priced=len(open_positions) - unpriced,
            unpriced=unpriced,
            positions_detail=detail,
        )

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

    async def take_snapshot(self, branch_id: str, positions_detail: list[dict] | None = None) -> PortfolioSnapshot:
        summary = await self.portfolio_repo.get_by_branch(branch_id)
        if summary is None:
            raise ValueError(f"No portfolio for branch {branch_id}")

        snapshot = await self.snapshot_repo.create(summary.id, branch_id, positions_detail=positions_detail)

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
        """Return fund + per-branch summary with inception returns as fractions (0.01 = 1%), not percents.

        `total_return_pct` / branch `total_return_pct` are None when there is no baseline (initial capital <= 0).
        """
        summary = await self.portfolio_repo.get_fund_summary(fund_id)
        total_initial = 0.0
        for b in summary.get("branches", []):
            initial = float(b.get("allocated_capital") or 0.0)
            nav = float(b.get("nav") or 0.0)
            b["initial_capital"] = initial
            b["total_pnl"] = nav - initial
            b["total_return_pct"] = (nav - initial) / initial if initial > 0 else None
            total_initial += initial
        total_nav = float(summary.get("total_nav") or 0.0)
        summary["total_initial_capital"] = total_initial
        summary["total_pnl"] = total_nav - total_initial
        summary["total_return_pct"] = (total_nav - total_initial) / total_initial if total_initial > 0 else None
        return summary

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
        realized_delta = 0.0  # this trade's contribution to lifetime realized P&L

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
            realized_delta = realized

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
            realized_delta = realized
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

        # Recalculate row-derived aggregates (exposures/margin live on position rows).
        all_positions = await self.position_repo.get_by_portfolio(summary.id)
        total_long_exposure = sum(p.long_cost_basis for p in all_positions)
        total_short_exposure = sum(p.short_cost_basis for p in all_positions)
        total_margin_used = sum(p.short_margin_used for p in all_positions)

        # Realized P&L is ledger-derived, not row-derived: delete_if_flat removes
        # fully-closed positions, so summing surviving rows silently drops their
        # lifetime P&L. Accumulate incrementally, mirroring the cash update.
        new_realized = float(summary.realized_pnl) + realized_delta

        new_cash = float(summary.cash) + cash_delta
        new_nav = new_cash + total_long_exposure

        await self.portfolio_repo.update_portfolio_fields(
            trade.branch_id,
            cash=new_cash,
            nav=new_nav,
            total_long_exposure=total_long_exposure,
            total_short_exposure=total_short_exposure,
            margin_used=total_margin_used,
            unrealized_pnl=0.0,  # exposures revert to cost basis until the next mark
            realized_pnl=new_realized,
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
                realized_pnl=new_realized,
            )
        )
