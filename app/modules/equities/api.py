from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.db.models import AgentSignalModel, BranchModel, PortfolioDecisionModel, ScreeningRunModel
from app.dependencies import (
    get_equities_service,
    get_portfolio_service,
    get_trade_execution_service,
)
from app.modules.equities.config import EquitiesConfig
from app.modules.equities.service import EquitiesBranchService
from app.modules.event_log.repository import PostgresEventLogRepository
from app.modules.portfolio.service import PortfolioService
from app.modules.trade_execution.service import TradeExecutionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/equities", tags=["equities"])


# Static routes must be defined before /{branch_name} to avoid path conflicts


@router.get("/config")
async def get_config(
    service: EquitiesBranchService = Depends(get_equities_service),
):
    return service.config.model_dump()


@router.put("/config")
async def update_config(
    config: EquitiesConfig,
    service: EquitiesBranchService = Depends(get_equities_service),
):
    service.config = config
    return service.config.model_dump()


# Dynamic /{branch_name} routes


@router.post("/{branch_name}/run")
async def trigger_run(
    branch_name: str,
    service: EquitiesBranchService = Depends(get_equities_service),
    trade_execution_service: TradeExecutionService = Depends(get_trade_execution_service),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
    session: AsyncSession = Depends(get_session),
):
    # Look up the actual branch by name to satisfy FK constraints
    stmt = select(BranchModel).where(BranchModel.name.ilike(f"%{branch_name}%"))
    result = await session.execute(stmt)
    branch = result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail=f"No branch found matching '{branch_name}'")
    branch_id = str(branch.id)
    event_log_repo = PostgresEventLogRepository(session)
    try:
        result = await service.run_pipeline(
            branch_name=branch_name,
            branch_id=branch_id,
            trade_execution_service=trade_execution_service,
            portfolio_service=portfolio_service,
            event_log_repo=event_log_repo,
            session=session,
        )
        return result.model_dump()
    except Exception as e:
        logger.exception("Pipeline run failed for %s", branch_name)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{branch_name}/signals")
async def get_signals(
    branch_name: str,
    limit: int = Query(default=100, le=500),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(AgentSignalModel)
        .join(ScreeningRunModel)
        .where(ScreeningRunModel.branch_name == branch_name)
        .order_by(AgentSignalModel.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "screening_run_id": str(r.screening_run_id),
            "symbol": r.symbol,
            "analyst_type": r.analyst_type,
            "bullish_score": r.bullish_score,
            "confidence": r.confidence,
            "summary": r.summary,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/{branch_name}/screening-runs")
async def get_screening_runs(
    branch_name: str,
    limit: int = Query(default=20, le=100),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(ScreeningRunModel)
        .where(ScreeningRunModel.branch_name == branch_name)
        .order_by(ScreeningRunModel.run_date.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "branch_id": str(r.branch_id),
            "branch_name": r.branch_name,
            "run_date": r.run_date.isoformat() if r.run_date else None,
            "universe_count": r.universe_count,
            "passed_count": r.passed_count,
            "config_snapshot": r.config_snapshot,
            "passed_symbols": r.passed_symbols,
        }
        for r in rows
    ]


@router.get("/{branch_name}/decisions")
async def get_decisions(
    branch_name: str,
    limit: int = Query(default=20, le=100),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(PortfolioDecisionModel)
        .where(PortfolioDecisionModel.branch_name == branch_name)
        .order_by(PortfolioDecisionModel.decided_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "screening_run_id": str(r.screening_run_id),
            "branch_id": str(r.branch_id),
            "branch_name": r.branch_name,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "target_holdings": r.target_holdings,
            "current_holdings": r.current_holdings,
            "orders_generated": r.orders_generated,
            "composite_scores": r.composite_scores,
        }
        for r in rows
    ]
