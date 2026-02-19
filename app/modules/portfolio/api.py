"""Portfolio module API routes — thin layer over PortfolioService."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas.pagination import PaginatedResponse, PaginationParams
from app.db.connection import get_session
from app.modules.event_log.repository import PostgresEventLogRepository
from app.modules.portfolio.repository import (
    PostgresPortfolioRepository,
    PostgresPositionRepository,
    PostgresSnapshotRepository,
)
from app.modules.portfolio.service import PortfolioService

router = APIRouter(prefix="/api/v1", tags=["portfolio"])


# --- Request/Response schemas ---


class CreatePortfolioRequest(BaseModel):
    branch_id: str
    branch_type: str
    initial_cash: float
    margin_requirement: float = 0.0


class AdjustCashRequest(BaseModel):
    amount: float
    reason: str


# --- Dependency ---


def _get_service(session: AsyncSession) -> PortfolioService:
    return PortfolioService(
        portfolio_repo=PostgresPortfolioRepository(session),
        position_repo=PostgresPositionRepository(session),
        snapshot_repo=PostgresSnapshotRepository(session),
        event_log=PostgresEventLogRepository(session),
    )


# --- Routes ---


@router.post("/portfolios")
async def create_portfolio(
    req: CreatePortfolioRequest,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    summary = await service.create_portfolio(
        branch_id=req.branch_id,
        branch_type=req.branch_type,
        initial_cash=req.initial_cash,
        margin_requirement=req.margin_requirement,
    )
    return {
        "portfolio_id": summary.id,
        "branch_id": summary.branch_id,
        "cash": summary.cash,
        "margin_requirement": summary.margin_requirement,
        "created_at": summary.updated_at.isoformat(),
    }


@router.get("/portfolios/{branch_id}")
async def get_portfolio(
    branch_id: str,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    summary = await service.get_portfolio(branch_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"No portfolio for branch {branch_id}")
    return summary.model_dump()


@router.get("/portfolios/{branch_id}/positions")
async def list_positions(
    branch_id: str,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    try:
        positions = await service.get_positions(branch_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return [p.model_dump() for p in positions]


@router.get("/portfolios/{branch_id}/positions/{symbol}")
async def get_position(
    branch_id: str,
    symbol: str,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    try:
        position = await service.get_position_by_symbol(branch_id, symbol)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if position is None:
        raise HTTPException(status_code=404, detail=f"No position for {symbol}")
    return position.model_dump()


@router.put("/portfolios/{branch_id}/cash")
async def adjust_cash(
    branch_id: str,
    req: AdjustCashRequest,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    try:
        summary = await service.adjust_cash(branch_id, req.amount, req.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return summary.model_dump()


@router.post("/portfolios/{branch_id}/snapshots")
async def take_snapshot(
    branch_id: str,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    try:
        snapshot = await service.take_snapshot(branch_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return snapshot.model_dump()


@router.get("/portfolios/{branch_id}/snapshots")
async def list_snapshots(
    branch_id: str,
    limit: int = 30,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    snapshots, total = await service.list_snapshots(branch_id, limit, offset)
    return PaginatedResponse(
        items=[s.model_dump() for s in snapshots],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    ).model_dump()


@router.get("/fund/{fund_id}/summary")
async def get_fund_summary(
    fund_id: str,
    session: AsyncSession = Depends(get_session),
):
    service = _get_service(session)
    try:
        return await service.get_fund_summary(fund_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/events")
async def list_events(
    event_type: str | None = None,
    branch_id: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """Query the event log (shared endpoint, lives here for convenience)."""
    from app.modules.event_log.service import EventLogService

    event_service = EventLogService(PostgresEventLogRepository(session))
    return await event_service.query(
        event_type=event_type,
        branch_id=branch_id,
        since=since,
        limit=limit,
        offset=offset,
    )
