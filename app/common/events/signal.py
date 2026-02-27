from app.common.enums import SignalDirection
from app.common.events.base import BaseEvent


class SignalGeneratedEvent(BaseEvent):
    event_type: str = "signal.generated"

    branch_id: str
    agent_name: str
    instrument_id: str
    symbol: str

    direction: SignalDirection
    confidence: float  # 0-100
    reasoning: str
    data_sources_used: list[str] = []
