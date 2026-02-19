"""PostgreSQL implementation of the EventLogRepository."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.events.base import BaseEvent
from app.common.interfaces.repositories import EventLogRepository
from app.db.models import EventModel


class PostgresEventLogRepository(EventLogRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def append(self, event: BaseEvent) -> None:
        row = EventModel(
            event_type=event.event_type,
            branch_id=getattr(event, "branch_id", None),
            source=event.source,
            correlation_id=event.correlation_id,
            payload=event.model_dump(mode="json"),
        )
        self.session.add(row)

    async def query(
        self,
        event_type: str | None = None,
        branch_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        stmt = select(EventModel).order_by(EventModel.created_at.desc())

        if event_type is not None:
            stmt = stmt.where(EventModel.event_type == event_type)
        if branch_id is not None:
            stmt = stmt.where(EventModel.branch_id == branch_id)
        if since is not None:
            stmt = stmt.where(EventModel.created_at >= since)

        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        rows = result.scalars().all()

        return [
            {
                "id": str(row.id),
                "event_type": row.event_type,
                "branch_id": row.branch_id,
                "source": row.source,
                "correlation_id": str(row.correlation_id) if row.correlation_id else None,
                "payload": row.payload,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
