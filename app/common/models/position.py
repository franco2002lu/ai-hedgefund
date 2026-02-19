from datetime import datetime

from pydantic import BaseModel


class Position(BaseModel):
    id: str
    portfolio_id: str
    instrument_id: str
    symbol: str

    long_quantity: float = 0.0
    long_cost_basis: float = 0.0

    short_quantity: float = 0.0
    short_cost_basis: float = 0.0
    short_margin_used: float = 0.0

    realized_pnl_long: float = 0.0
    realized_pnl_short: float = 0.0

    updated_at: datetime
