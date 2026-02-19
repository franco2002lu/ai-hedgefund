"""Event log service — thin wrapper around the repository."""

from datetime import datetime

from app.common.events.base import BaseEvent
from app.common.interfaces.repositories import EventLogRepository


class EventLogService:
    def __init__(self, repository: EventLogRepository):
        self.repository = repository

    async def append(self, event: BaseEvent) -> None:
        await self.repository.append(event)

    async def query(
        self,
        event_type: str | None = None,
        branch_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        return await self.repository.query(
            event_type=event_type,
            branch_id=branch_id,
            since=since,
            limit=limit,
            offset=offset,
        )
