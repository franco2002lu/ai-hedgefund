from app.common.events.base import BaseEvent


class PortfolioUpdatedEvent(BaseEvent):
    event_type: str = "portfolio.updated"

    portfolio_id: str
    branch_id: str
    trigger: str  # "trade_executed", "allocation_adjusted", "manual"

    cash: float
    nav: float
    margin_used: float
    total_long_exposure: float
    total_short_exposure: float
    unrealized_pnl: float
    realized_pnl: float

    trade_id: str | None = None


class PortfolioSnapshotEvent(BaseEvent):
    event_type: str = "portfolio.snapshot"

    snapshot_id: str
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
