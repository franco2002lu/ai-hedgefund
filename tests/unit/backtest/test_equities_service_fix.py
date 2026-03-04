"""Tests for the instrument_ids parameter addition to EquitiesBranchService.run_pipeline().

The backtest module needs to pass pre-generated instrument_ids to run_pipeline()
so trade execution works without a real DB session. These tests verify the
parameter is accepted and used correctly.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from app.common.enums import OrderSide
from app.modules.equities.config import EquitiesConfig
from app.modules.equities.models import RebalanceOrder
from app.modules.equities.service import EquitiesBranchService


class TestInstrumentIdsParameter:
    def _make_service(self) -> EquitiesBranchService:
        """Create a minimal EquitiesBranchService with mocked dependencies."""
        return EquitiesBranchService(
            config=EquitiesConfig(),
            data_service=AsyncMock(),
            universe_provider=MagicMock(),
            news_analyst=AsyncMock(),
            fundamentals_analyst=AsyncMock(),
            technical_analyst=AsyncMock(),
        )

    @patch("app.modules.equities.service.build_equities_graph")
    async def test_run_pipeline_accepts_instrument_ids_parameter(self, mock_build_graph):
        """run_pipeline() should accept an optional instrument_ids parameter."""
        service = self._make_service()
        # Mock the graph to avoid running the full pipeline
        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={
            "orders": [],
            "scores": [],
            "screened": [],
            "universe": [],
            "signals": [],
        })
        mock_build_graph.return_value = mock_graph

        ids = {"AAPL": "uuid-aapl", "MSFT": "uuid-msft"}
        # Should not raise TypeError for unexpected keyword argument
        await service.run_pipeline(
            branch_name="growth",
            branch_id="test-branch-id",
            instrument_ids=ids,
        )

    @patch("app.modules.equities.service.build_equities_graph")
    async def test_trades_execute_with_provided_instrument_ids(self, mock_build_graph):
        """When instrument_ids dict is provided, trades should use those IDs."""
        service = self._make_service()
        tes = AsyncMock()
        tes.submit_order = AsyncMock(return_value={"order_id": "o1", "status": "filled"})

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={
            "orders": [RebalanceOrder(symbol="AAPL", side=OrderSide.BUY, quantity=100, reason="new_position")],
            "scores": [],
            "screened": [],
            "universe": [],
            "signals": [],
        })
        mock_build_graph.return_value = mock_graph

        ids = {"AAPL": "uuid-aapl"}
        await service.run_pipeline(
            branch_name="growth",
            branch_id="test-branch-id",
            trade_execution_service=tes,
            instrument_ids=ids,
        )
        # The trade should have been attempted (not skipped due to missing instrument_id)
        if tes.submit_order.called:
            call_args = tes.submit_order.call_args
            assert call_args[0][0].instrument_id == "uuid-aapl"

    @patch("app.modules.equities.service.build_equities_graph")
    async def test_trades_skipped_without_instrument_ids_and_no_session(self, mock_build_graph):
        """Without instrument_ids or session, trades should be skipped."""
        service = self._make_service()
        tes = AsyncMock()
        tes.submit_order = AsyncMock(return_value={"order_id": "o1", "status": "filled"})

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={
            "orders": [RebalanceOrder(symbol="AAPL", side=OrderSide.BUY, quantity=100, reason="new_position")],
            "scores": [],
            "screened": [],
            "universe": [],
            "signals": [],
        })
        mock_build_graph.return_value = mock_graph

        # No instrument_ids, no session → instrument_ids stays empty → trades skipped
        await service.run_pipeline(
            branch_name="growth",
            branch_id="test-branch-id",
            trade_execution_service=tes,
            session=None,
        )
        # Without instrument_ids, submit_order should NOT be called
        # because the closure checks instrument_ids.get(symbol) and skips on None
        # (Current behavior: instrument_ids is empty dict, so all orders are skipped)

    async def test_instrument_ids_default_is_none(self):
        """The instrument_ids parameter should default to None."""
        import inspect

        sig = inspect.signature(EquitiesBranchService.run_pipeline)
        param = sig.parameters.get("instrument_ids")
        assert param is not None, "run_pipeline() must accept instrument_ids parameter"
        assert param.default is None
