from pydantic import BaseModel

from app.common.events.base import BaseEvent


class BranchAllocation(BaseModel):
    branch_id: str
    branch_type: str
    target_capital: float
    current_capital: float
    delta: float
    action: str  # "increase", "decrease", "hold"


class AllocationDirectiveEvent(BaseEvent):
    event_type: str = "allocation.directive"

    fund_id: str
    total_aum: float
    regime: str
    allocations: list[BranchAllocation]
    reasoning: str
