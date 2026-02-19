from datetime import datetime

from pydantic import BaseModel

from app.common.models.position import Position


class PortfolioSummary(BaseModel):
    id: str
    branch_id: str
    branch_type: str

    cash: float
    allocated_capital: float
    margin_requirement: float
    margin_used: float

    nav: float
    total_long_exposure: float
    total_short_exposure: float
    gross_exposure: float
    net_exposure: float
    unrealized_pnl: float
    realized_pnl: float

    positions: list[Position] = []
    updated_at: datetime


class PortfolioSnapshot(BaseModel):
    id: str
    portfolio_id: str
    branch_id: str

    cash: float
    nav: float
    total_long_exposure: float
    total_short_exposure: float
    gross_exposure: float
    net_exposure: float
    unrealized_pnl: float
    realized_pnl: float
    margin_used: float

    position_count: int
    top_holdings: list[dict] = []

    snapshot_at: datetime
