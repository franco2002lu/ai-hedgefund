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


class MarkToMarketResult(BaseModel):
    """Result of repricing a portfolio's open positions at market."""

    nav: float
    cash: float
    unrealized_pnl: float
    realized_pnl: float
    priced: int
    unpriced: int
    # Per position, sorted by market_value desc:
    # {symbol, quantity, price (None if unpriced), market_value, cost_basis,
    #  unrealized_pnl, weight}
    positions_detail: list[dict] = []


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

    # Per-position detail captured at snapshot time (symbol, quantity, price,
    # market_value, cost_basis, unrealized_pnl, weight). None for legacy rows.
    positions_detail: list[dict] | None = None

    snapshot_at: datetime
