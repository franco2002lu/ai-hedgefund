"""Unit tests for EquitiesBranchService._persist_run_artifacts."""

import uuid
from unittest.mock import AsyncMock, MagicMock

from app.db.models import PortfolioDecisionModel
from app.modules.equities.config import EquitiesConfig
from app.modules.equities.models import CompositeScore
from app.modules.equities.service import EquitiesBranchService


def _make_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _added_decision(session: MagicMock) -> PortfolioDecisionModel:
    for call in session.add.call_args_list:
        if isinstance(call.args[0], PortfolioDecisionModel):
            return call.args[0]
    raise AssertionError("No PortfolioDecisionModel was added to the session")


class TestPersistRunArtifacts:
    async def test_target_holdings_come_from_sized_targets(self):
        """target_holdings must store the sized weights, not the unsized score list."""
        service = EquitiesBranchService(config=EquitiesConfig())
        session = _make_session()

        scores = [
            CompositeScore(symbol="AAPL", composite_score=7.0, composite_confidence=6.0, conviction=42.0),
            CompositeScore(symbol="MSFT", composite_score=3.5, composite_confidence=5.0, conviction=17.5),
        ]
        targets = [
            CompositeScore(
                symbol="AAPL", composite_score=7.0, composite_confidence=6.0, conviction=42.0, target_weight=0.65
            ),
        ]

        await service._persist_run_artifacts(
            session,
            "growth",
            str(uuid.uuid4()),
            universe=[],
            screened=[],
            signals=[],
            scores=scores,
            orders=[],
            current_positions={},
            targets=targets,
        )

        decision = _added_decision(session)
        assert decision.target_holdings == {"AAPL": 0.65}
        # composite_scores still cover every scored stock
        assert set(decision.composite_scores) == {"AAPL", "MSFT"}

    async def test_analyst_weights_report_persisted_on_decision(self):
        from app.modules.equities.adaptive_weights import AnalystWeightsReport

        service = EquitiesBranchService(config=EquitiesConfig())
        session = _make_session()
        report = AnalystWeightsReport(
            weights={"fundamentals": 0.65, "news": 0.19, "technical": 0.16},
            mode="adaptive",
            reason="ok",
            ewics={"fundamentals": 0.09, "news": 0.0, "technical": -0.08},
            valid_weeks={"fundamentals": 9, "news": 9, "technical": 9},
        )
        await service._persist_run_artifacts(
            session,
            "growth",
            str(uuid.uuid4()),
            universe=[],
            screened=[],
            signals=[],
            scores=[],
            orders=[],
            current_positions={},
            targets=[],
            analyst_weights_report=report,
        )
        decision = _added_decision(session)
        assert decision.analyst_weights["weights"]["fundamentals"] == 0.65
        assert decision.analyst_weights["mode"] == "adaptive"

    async def test_absent_weights_report_persists_null(self):
        service = EquitiesBranchService(config=EquitiesConfig())
        session = _make_session()
        await service._persist_run_artifacts(
            session,
            "growth",
            str(uuid.uuid4()),
            universe=[],
            screened=[],
            signals=[],
            scores=[],
            orders=[],
            current_positions={},
            targets=[],
        )
        assert _added_decision(session).analyst_weights is None
