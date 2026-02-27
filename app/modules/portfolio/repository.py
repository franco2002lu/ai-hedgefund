"""PostgreSQL repositories for Portfolio module."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.interfaces.repositories import (
    PortfolioRepository,
    PositionRepository,
    SnapshotRepository,
)
from app.common.models.portfolio import PortfolioSnapshot, PortfolioSummary
from app.common.models.position import Position
from app.db.models import (
    BranchModel,
    FundModel,
    PortfolioModel,
    PortfolioSnapshotModel,
    PositionModel,
)


class PostgresPortfolioRepository(PortfolioRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_branch(self, branch_id: str) -> PortfolioSummary | None:
        stmt = (
            select(PortfolioModel, BranchModel)
            .join(BranchModel, PortfolioModel.branch_id == BranchModel.id)
            .where(PortfolioModel.branch_id == branch_id)
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None

        portfolio, branch = row
        return self._to_summary(portfolio, branch)

    async def create(
        self,
        branch_id: str,
        branch_type: str,
        initial_cash: float,
        margin_requirement: float,
    ) -> PortfolioSummary:
        portfolio = PortfolioModel(
            branch_id=uuid.UUID(branch_id),
            cash=initial_cash,
            nav=initial_cash,
            margin_requirement=margin_requirement,
        )
        self.session.add(portfolio)
        await self.session.flush()

        branch = await self.session.get(BranchModel, uuid.UUID(branch_id))
        return self._to_summary(portfolio, branch)

    async def update_cash(self, branch_id: str, amount: float, reason: str) -> PortfolioSummary:
        stmt = select(PortfolioModel).where(PortfolioModel.branch_id == branch_id)
        result = await self.session.execute(stmt)
        portfolio = result.scalar_one_or_none()
        if portfolio is None:
            raise ValueError(f"No portfolio for branch {branch_id}")

        new_cash = float(portfolio.cash) + amount
        if new_cash < 0:
            raise ValueError(f"Insufficient cash: have {float(portfolio.cash)}, tried to withdraw {abs(amount)}")

        portfolio.cash = new_cash
        portfolio.nav = (
            new_cash
            + float(portfolio.total_long_exposure)
            - float(portfolio.total_short_exposure)
            + float(portfolio.unrealized_pnl)
        )
        portfolio.updated_at = datetime.now(UTC)
        await self.session.flush()

        branch = await self.session.get(BranchModel, uuid.UUID(branch_id))
        return self._to_summary(portfolio, branch)

    async def update_portfolio_fields(self, branch_id: str, **fields) -> None:
        """Update arbitrary portfolio fields (used by service after trade execution)."""
        stmt = select(PortfolioModel).where(PortfolioModel.branch_id == branch_id)
        result = await self.session.execute(stmt)
        portfolio = result.scalar_one_or_none()
        if portfolio is None:
            raise ValueError(f"No portfolio for branch {branch_id}")

        for key, value in fields.items():
            setattr(portfolio, key, value)
        portfolio.updated_at = datetime.now(UTC)
        await self.session.flush()

    async def get_fund_summary(self, fund_id: str) -> dict:
        fund = await self.session.get(FundModel, uuid.UUID(fund_id))
        if fund is None:
            raise ValueError(f"Fund {fund_id} not found")

        stmt = (
            select(PortfolioModel, BranchModel)
            .join(BranchModel, PortfolioModel.branch_id == BranchModel.id)
            .where(BranchModel.fund_id == fund_id)
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        branches = []
        total_nav = 0.0
        total_cash = 0.0
        total_long = 0.0
        total_short = 0.0

        for portfolio, branch in rows:
            nav = float(portfolio.nav)
            cash = float(portfolio.cash)
            total_nav += nav
            total_cash += cash
            total_long += float(portfolio.total_long_exposure)
            total_short += float(portfolio.total_short_exposure)

            # Count positions
            pos_stmt = select(func.count()).where(PositionModel.portfolio_id == portfolio.id)
            pos_result = await self.session.execute(pos_stmt)
            pos_count = pos_result.scalar()

            branches.append(
                {
                    "branch_id": str(branch.id),
                    "branch_type": branch.branch_type,
                    "status": branch.status,
                    "allocated_capital": float(branch.allocated_capital),
                    "nav": nav,
                    "cash": cash,
                    "unrealized_pnl": float(portfolio.unrealized_pnl),
                    "realized_pnl": float(portfolio.realized_pnl),
                    "position_count": pos_count,
                }
            )

        return {
            "fund_id": str(fund.id),
            "total_aum": float(fund.total_aum),
            "total_nav": total_nav,
            "total_cash": total_cash,
            "total_long_exposure": total_long,
            "total_short_exposure": total_short,
            "execution_mode": fund.execution_mode,
            "branches": branches,
        }

    def _to_summary(self, portfolio: PortfolioModel, branch: BranchModel | None) -> PortfolioSummary:
        long_exp = float(portfolio.total_long_exposure)
        short_exp = float(portfolio.total_short_exposure)
        return PortfolioSummary(
            id=str(portfolio.id),
            branch_id=str(portfolio.branch_id),
            branch_type=branch.branch_type if branch else "unknown",
            cash=float(portfolio.cash),
            allocated_capital=float(branch.allocated_capital) if branch else 0.0,
            margin_requirement=float(portfolio.margin_requirement),
            margin_used=float(portfolio.margin_used),
            nav=float(portfolio.nav),
            total_long_exposure=long_exp,
            total_short_exposure=short_exp,
            gross_exposure=long_exp + short_exp,
            net_exposure=long_exp - short_exp,
            unrealized_pnl=float(portfolio.unrealized_pnl),
            realized_pnl=float(portfolio.realized_pnl),
            updated_at=portfolio.updated_at or portfolio.created_at,
        )


class PostgresPositionRepository(PositionRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_portfolio(self, portfolio_id: str) -> list[Position]:
        stmt = select(PositionModel).where(PositionModel.portfolio_id == portfolio_id)
        result = await self.session.execute(stmt)
        return [self._to_domain(row) for row in result.scalars().all()]

    async def get_by_symbol(self, portfolio_id: str, symbol: str) -> Position | None:
        stmt = select(PositionModel).where(
            PositionModel.portfolio_id == portfolio_id,
            PositionModel.symbol == symbol,
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def upsert(self, position: Position) -> Position:
        stmt = select(PositionModel).where(
            PositionModel.portfolio_id == position.portfolio_id,
            PositionModel.instrument_id == position.instrument_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.long_quantity = position.long_quantity
            existing.long_cost_basis = position.long_cost_basis
            existing.short_quantity = position.short_quantity
            existing.short_cost_basis = position.short_cost_basis
            existing.short_margin_used = position.short_margin_used
            existing.realized_pnl_long = position.realized_pnl_long
            existing.realized_pnl_short = position.realized_pnl_short
            existing.updated_at = datetime.now(UTC)
            await self.session.flush()
            return self._to_domain(existing)
        else:
            new_pos = PositionModel(
                portfolio_id=uuid.UUID(position.portfolio_id),
                instrument_id=uuid.UUID(position.instrument_id),
                symbol=position.symbol,
                long_quantity=position.long_quantity,
                long_cost_basis=position.long_cost_basis,
                short_quantity=position.short_quantity,
                short_cost_basis=position.short_cost_basis,
                short_margin_used=position.short_margin_used,
                realized_pnl_long=position.realized_pnl_long,
                realized_pnl_short=position.realized_pnl_short,
            )
            self.session.add(new_pos)
            await self.session.flush()
            return self._to_domain(new_pos)

    async def delete_if_flat(self, portfolio_id: str, instrument_id: str) -> bool:
        stmt = select(PositionModel).where(
            PositionModel.portfolio_id == portfolio_id,
            PositionModel.instrument_id == instrument_id,
        )
        result = await self.session.execute(stmt)
        pos = result.scalar_one_or_none()
        if pos is None:
            return False

        if float(pos.long_quantity) == 0 and float(pos.short_quantity) == 0:
            await self.session.delete(pos)
            await self.session.flush()
            return True
        return False

    def _to_domain(self, model: PositionModel) -> Position:
        return Position(
            id=str(model.id),
            portfolio_id=str(model.portfolio_id),
            instrument_id=str(model.instrument_id),
            symbol=model.symbol,
            long_quantity=float(model.long_quantity),
            long_cost_basis=float(model.long_cost_basis),
            short_quantity=float(model.short_quantity),
            short_cost_basis=float(model.short_cost_basis),
            short_margin_used=float(model.short_margin_used),
            realized_pnl_long=float(model.realized_pnl_long),
            realized_pnl_short=float(model.realized_pnl_short),
            updated_at=model.updated_at,
        )


class PostgresSnapshotRepository(SnapshotRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, portfolio_id: str, branch_id: str) -> PortfolioSnapshot:
        # Get current portfolio state
        portfolio = await self.session.get(PortfolioModel, uuid.UUID(portfolio_id))
        if portfolio is None:
            raise ValueError(f"Portfolio {portfolio_id} not found")

        pos_stmt = select(func.count()).where(PositionModel.portfolio_id == portfolio_id)
        pos_count = (await self.session.execute(pos_stmt)).scalar()

        long_exp = float(portfolio.total_long_exposure)
        short_exp = float(portfolio.total_short_exposure)

        snapshot = PortfolioSnapshotModel(
            portfolio_id=uuid.UUID(portfolio_id),
            branch_id=uuid.UUID(branch_id),
            cash=float(portfolio.cash),
            nav=float(portfolio.nav),
            total_long_exposure=long_exp,
            total_short_exposure=short_exp,
            gross_exposure=long_exp + short_exp,
            net_exposure=long_exp - short_exp,
            unrealized_pnl=float(portfolio.unrealized_pnl),
            realized_pnl=float(portfolio.realized_pnl),
            margin_used=float(portfolio.margin_used),
            position_count=pos_count,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return self._to_domain(snapshot)

    async def list_by_branch(
        self, branch_id: str, limit: int = 30, offset: int = 0
    ) -> tuple[list[PortfolioSnapshot], int]:
        count_stmt = select(func.count()).where(PortfolioSnapshotModel.branch_id == branch_id)
        total = (await self.session.execute(count_stmt)).scalar()

        stmt = (
            select(PortfolioSnapshotModel)
            .where(PortfolioSnapshotModel.branch_id == branch_id)
            .order_by(PortfolioSnapshotModel.snapshot_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        snapshots = [self._to_domain(row) for row in result.scalars().all()]
        return snapshots, total

    def _to_domain(self, model: PortfolioSnapshotModel) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            id=str(model.id),
            portfolio_id=str(model.portfolio_id),
            branch_id=str(model.branch_id),
            cash=float(model.cash),
            nav=float(model.nav),
            total_long_exposure=float(model.total_long_exposure),
            total_short_exposure=float(model.total_short_exposure),
            gross_exposure=float(model.gross_exposure),
            net_exposure=float(model.net_exposure),
            unrealized_pnl=float(model.unrealized_pnl),
            realized_pnl=float(model.realized_pnl),
            margin_used=float(model.margin_used),
            position_count=model.position_count,
            snapshot_at=model.snapshot_at,
        )
