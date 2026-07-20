"""Domain model for risk alerts (mirrors RiskAlertModel in app/db/models.py)."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.common.enums import RiskAlertLevel


class RiskAlert(BaseModel):
    id: str | None = None
    level: RiskAlertLevel
    source: str  # branch_id, or "global" for fund-level alerts
    metric: str
    current_value: float
    threshold: float
    message: str
    action_required: str | None = None
    affected_branches: list[str] = Field(default_factory=list)
    resolved: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
