import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """All events inherit from this."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str
    correlation_id: str | None = None
