"""PostgresRiskAlertRepository persists RiskAlert domain models as rows."""

import uuid

from app.common.enums import RiskAlertLevel
from app.common.models.risk import RiskAlert
from app.modules.portfolio.repository import PostgresRiskAlertRepository


class StubSession:
    """Captures added rows; flush assigns an id like the DB default would."""

    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()


async def test_create_persists_row_and_returns_alert_with_id():
    session = StubSession()
    repo = PostgresRiskAlertRepository(session)
    alert = RiskAlert(
        level=RiskAlertLevel.CRITICAL,
        source="33333333-3333-3333-3333-333333333333",
        metric="cash",
        current_value=-55473.33,
        threshold=0.0,
        message="growth: cash is negative",
        affected_branches=["growth"],
    )

    saved = await repo.create(alert)

    assert saved.id is not None
    assert len(session.added) == 1
    row = session.added[0]
    assert row.level == "critical"
    assert row.metric == "cash"
    assert row.current_value == -55473.33
    assert row.threshold == 0.0
    assert row.message == "growth: cash is negative"
    assert row.affected_branches == ["growth"]
    assert row.resolved is False


async def test_create_preserves_action_required_none():
    session = StubSession()
    repo = PostgresRiskAlertRepository(session)
    alert = RiskAlert(
        level=RiskAlertLevel.WARNING,
        source="b-1",
        metric="cash_pct",
        current_value=0.08,
        threshold=0.05,
        message="underinvested",
    )
    await repo.create(alert)
    assert session.added[0].action_required is None
