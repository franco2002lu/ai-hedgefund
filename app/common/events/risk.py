from app.common.events.base import BaseEvent
from app.common.enums import RiskAlertLevel


class RiskAlertEvent(BaseEvent):
    event_type: str = "risk.alert"

    level: RiskAlertLevel
    source: str  # "global" or branch_id
    metric: str
    current_value: float
    threshold: float
    message: str
    action_required: str | None = None
    affected_branches: list[str] = []
