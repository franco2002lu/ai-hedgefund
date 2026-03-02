"""Unit tests for EventLogService — delegation and query forwarding."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from app.common.events.base import BaseEvent
from app.modules.event_log.service import EventLogService

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEventLogService:
    async def test_append_delegates_to_repository(self):
        repo = AsyncMock()
        svc = EventLogService(repository=repo)
        event = MagicMock(spec=BaseEvent)

        await svc.append(event)

        repo.append.assert_awaited_once_with(event)

    async def test_query_passes_all_filters(self):
        repo = AsyncMock()
        repo.query.return_value = []
        svc = EventLogService(repository=repo)
        since = datetime(2025, 1, 1)

        await svc.query(
            event_type="trade.executed",
            branch_id="branch-123",
            since=since,
            limit=50,
            offset=10,
        )

        repo.query.assert_awaited_once_with(
            event_type="trade.executed",
            branch_id="branch-123",
            since=since,
            limit=50,
            offset=10,
        )

    async def test_query_returns_repository_result(self):
        expected = [{"event_type": "trade.executed", "data": {}}]
        repo = AsyncMock()
        repo.query.return_value = expected
        svc = EventLogService(repository=repo)

        result = await svc.query()

        assert result == expected
