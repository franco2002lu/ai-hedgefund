"""Portfolio module API routes — thin layer over PortfolioService."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.common.schemas.pagination import PaginatedResponse, PaginationParams
from app.dependencies import get_event_log_service, get_portfolio_service
from app.modules.event_log.service import EventLogService
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


# --- Routes ---


@router.post("/portfolios")
async def create_portfolio(
    req: CreatePortfolioRequest,
    service: PortfolioService = Depends(get_portfolio_service),
):
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
    service: PortfolioService = Depends(get_portfolio_service),
):
    summary = await service.get_portfolio(branch_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"No portfolio for branch {branch_id}")
    return summary.model_dump()


@router.get("/portfolios/{branch_id}/positions")
async def list_positions(
    branch_id: str,
    service: PortfolioService = Depends(get_portfolio_service),
):
    try:
        positions = await service.get_positions(branch_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return [p.model_dump() for p in positions]


@router.get("/portfolios/{branch_id}/positions/{symbol}")
async def get_position(
    branch_id: str,
    symbol: str,
    service: PortfolioService = Depends(get_portfolio_service),
):
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
    service: PortfolioService = Depends(get_portfolio_service),
):
    try:
        summary = await service.adjust_cash(branch_id, req.amount, req.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return summary.model_dump()


@router.post("/portfolios/{branch_id}/snapshots")
async def take_snapshot(
    branch_id: str,
    service: PortfolioService = Depends(get_portfolio_service),
):
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
    service: PortfolioService = Depends(get_portfolio_service),
):
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
    service: PortfolioService = Depends(get_portfolio_service),
):
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
    event_service: EventLogService = Depends(get_event_log_service),
):
    """Query the event log (shared endpoint, lives here for convenience)."""
    return await event_service.query(
        event_type=event_type,
        branch_id=branch_id,
        since=since,
        limit=limit,
        offset=offset,
    )
