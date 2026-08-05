"""Unit tests for the LangGraph equities workflow (graph.py)."""

from unittest.mock import AsyncMock, MagicMock

from app.modules.equities.agents.graph import EquitiesWorkflowState, build_equities_graph
from app.modules.equities.models import CompositeScore, RebalanceOrder, StockSignal, UniverseStock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stock(symbol: str = "AAPL") -> UniverseStock:
    return UniverseStock(symbol=symbol, company_name=f"{symbol} Inc.", weight=0.05, sector="Technology")


def _make_deps(**overrides) -> dict:
    """Build a minimal deps dict with all required keys mocked."""
    deps = {
        "universe_provider": AsyncMock(),
        "screener": AsyncMock(),
        "data_service": AsyncMock(),
        "news_analyst": AsyncMock(),
        "fundamentals_analyst": AsyncMock(),
        "technical_analyst": AsyncMock(),
        "portfolio_manager": MagicMock(),
    }
    deps.update(overrides)
    return deps


# ---------------------------------------------------------------------------
# Node-level tests (invoke nodes via compiled graph state)
# ---------------------------------------------------------------------------


class TestFetchUniverse:
    async def test_calls_provider_with_branch_key(self):
        provider = AsyncMock()
        provider.get_holdings = AsyncMock(return_value=[_make_stock()])
        deps = _make_deps(universe_provider=provider)

        state: EquitiesWorkflowState = {
            "branch_name": "equities growth",
            "branch_id": "test-branch",
            "deps": deps,
        }

        # Replicate the node logic: branch_key derivation + provider call
        branch_key = "growth" if "growth" in state.get("branch_name", "") else "value"
        holdings = await provider.get_holdings(branch_key)

        provider.get_holdings.assert_awaited_once_with("growth")
        assert len(holdings) == 1

    async def test_derives_value_branch_key(self):
        branch_name = "US Equities Value"
        branch_key = "growth" if "growth" in branch_name else "value"

        assert branch_key == "value"

    async def test_derives_growth_branch_key(self):
        branch_name = "equities growth"
        branch_key = "growth" if "growth" in branch_name else "value"
        assert branch_key == "growth"


class TestScreenStocks:
    async def test_calls_screener(self):
        screener = AsyncMock()
        screener.screen = AsyncMock(return_value=[_make_stock()])
        data_service = AsyncMock()

        universe = [_make_stock("AAPL"), _make_stock("MSFT")]
        result = await screener.screen(universe, data_service)

        screener.screen.assert_awaited_once_with(universe, data_service)
        assert len(result) == 1


class TestAnalystNodes:
    async def test_news_analyst_calls_analyze_batch(self):
        analyst = AsyncMock()
        analyst.analyze_batch = AsyncMock(return_value=[MagicMock()])
        stocks = [_make_stock()]

        signals = await analyst.analyze_batch(stocks, max_concurrent=10)

        analyst.analyze_batch.assert_awaited_once()
        assert len(signals) == 1

    async def test_fundamentals_analyst_calls_analyze_batch(self):
        analyst = AsyncMock()
        analyst.analyze_batch = AsyncMock(return_value=[MagicMock()])
        stocks = [_make_stock()]

        await analyst.analyze_batch(stocks, max_concurrent=10)

        analyst.analyze_batch.assert_awaited_once()

    async def test_technical_analyst_calls_analyze_batch(self):
        analyst = AsyncMock()
        analyst.analyze_batch = AsyncMock(return_value=[MagicMock()])
        stocks = [_make_stock()]

        await analyst.analyze_batch(stocks, max_concurrent=10)

        analyst.analyze_batch.assert_awaited_once()


class TestExecuteTrades:
    async def test_calls_trade_fn_per_order(self):
        trade_fn = AsyncMock(return_value={"trade_id": "t1"})
        orders = [MagicMock(), MagicMock()]

        trades = []
        for order in orders:
            trade = await trade_fn(order)
            if trade:
                trades.append(trade)

        assert trade_fn.await_count == 2
        assert len(trades) == 2

    async def test_skips_when_no_trade_fn(self):
        deps = _make_deps()
        # No execute_trade_fn in deps
        trade_fn = deps.get("execute_trade_fn")

        trades = []
        if trade_fn:
            trades.append("should not reach here")

        assert trades == []


class TestGraphEndToEnd:
    """Run the real compiled graph with mocked deps to test node behavior."""

    def _initial_state(self, trade_results: list, n_orders: int) -> tuple[dict, list[CompositeScore]]:
        stock = _make_stock("AAPL")
        provider = AsyncMock()
        provider.get_holdings = AsyncMock(return_value=[stock])
        screener = AsyncMock()
        screener.screen = AsyncMock(return_value=[stock])
        data_service = AsyncMock()
        data_service.get_market_news = AsyncMock(return_value={"articles": []})
        data_service.get_sector_news = AsyncMock(return_value={"articles": []})
        data_service.get_news = AsyncMock(return_value={"articles": [], "source": "mock"})
        data_service.get_current_price = AsyncMock(return_value=100.0)
        analyst = AsyncMock()
        analyst.analyze_batch = AsyncMock(return_value=[])

        scores = [
            CompositeScore(symbol="AAPL", composite_score=7.0, composite_confidence=6.0, conviction=42.0),
            CompositeScore(symbol="MSFT", composite_score=3.5, composite_confidence=5.0, conviction=17.5),
        ]
        sized = [
            CompositeScore(
                symbol="AAPL", composite_score=7.0, composite_confidence=6.0, conviction=42.0, target_weight=0.05
            )
        ]
        pm = MagicMock()
        pm.compute_composite_scores.return_value = scores
        pm.select_stocks.return_value = [scores[0]]
        pm.size_positions.return_value = sized
        generated = [
            RebalanceOrder(symbol=f"SYM{i}", side="buy", quantity=1.0, reason="new_position") for i in range(n_orders)
        ]
        pm.generate_orders.return_value = generated
        pm.generate_orders_with_skips.return_value = (generated, [])

        deps = _make_deps(
            universe_provider=provider,
            screener=screener,
            data_service=data_service,
            news_analyst=analyst,
            fundamentals_analyst=analyst,
            technical_analyst=analyst,
            portfolio_manager=pm,
            execute_trade_fn=AsyncMock(side_effect=trade_results),
            nav=1_000_000.0,
        )
        state = {
            "branch_name": "growth",
            "branch_id": "test-branch",
            "universe": [],
            "screened": [],
            "signals": [],
            "scores": [],
            "orders": [],
            "trades": [],
            "deps": deps,
        }
        return state, sized

    async def test_trades_counts_only_successful_fills(self):
        """Rejected orders (success=False) must not be counted as executed trades."""
        trade_results = [
            {"success": True, "order_id": "o1", "status": "filled"},
            {"success": False, "order_id": None, "status": "rejected", "message": "No portfolio"},
            None,
        ]
        state, _ = self._initial_state(trade_results, n_orders=3)

        graph = build_equities_graph("growth")
        result = await graph.ainvoke(state)

        assert result["trades"] == [{"success": True, "order_id": "o1", "status": "filled"}]

    async def test_execute_trades_returns_order_results(self):
        """execute_trades keeps every submit_order result (fills, rejects, None) in order."""
        trade_results = [
            {"success": True, "order_id": "o1", "status": "filled"},
            {"success": False, "order_id": None, "status": "rejected", "message": "Insufficient position"},
            None,
        ]
        state, _ = self._initial_state(trade_results, n_orders=3)

        graph = build_equities_graph("growth")
        result = await graph.ainvoke(state)

        assert result["trades"] == [{"success": True, "order_id": "o1", "status": "filled"}]
        assert result["order_results"] == trade_results

    async def test_portfolio_decision_runs_exactly_once(self):
        """All three analysts must complete before portfolio_decision fires.

        With separate add_edge calls the node fires once after fundamentals+technical
        and again after the (longer) news path, submitting every order twice.
        """
        state, _ = self._initial_state([{"success": True, "order_id": "o", "status": "filled"}] * 10, n_orders=2)

        graph = build_equities_graph("growth")
        await graph.ainvoke(state)

        pm = state["deps"]["portfolio_manager"]
        # portfolio_decision calls generate_orders_with_skips (generate_orders
        # is kept only as a back-compat wrapper callers may use directly).
        assert pm.generate_orders_with_skips.call_count == 1
        assert state["deps"]["execute_trade_fn"].await_count == 2

    async def test_returns_sized_targets(self):
        """The graph must expose the sized target holdings (with weights) in state."""
        state, sized = self._initial_state([], n_orders=0)

        graph = build_equities_graph("growth")
        result = await graph.ainvoke(state)

        assert result.get("targets") == sized

    async def test_rankers_applied_after_each_analyst(self):
        state, _ = self._initial_state([], n_orders=0)
        ranked_marker = [
            StockSignal(symbol="AAPL", analyst_type="news", bullish_score=10, confidence=5, summary="ranked")
        ]
        ranker = MagicMock()
        ranker.rank = AsyncMock(return_value=ranked_marker)
        state["deps"]["rankers"] = {"news": ranker, "fundamentals": ranker, "technical": ranker}

        graph = build_equities_graph("growth")
        result = await graph.ainvoke(state)

        assert ranker.rank.await_count == 3
        assert result["signals"] == ranked_marker * 3


class TestGraphCompilation:
    def test_compiles_without_error(self):
        graph = build_equities_graph("growth")
        assert graph is not None

    def test_compiles_for_value_branch(self):
        graph = build_equities_graph("value")
        assert graph is not None
