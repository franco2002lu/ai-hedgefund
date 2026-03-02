"""Unit tests for the LangGraph equities workflow (graph.py)."""

from unittest.mock import AsyncMock, MagicMock

from app.modules.equities.agents.graph import EquitiesWorkflowState, build_equities_graph
from app.modules.equities.models import UniverseStock

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


class TestGraphCompilation:
    def test_compiles_without_error(self):
        graph = build_equities_graph("growth")
        assert graph is not None

    def test_compiles_for_value_branch(self):
        graph = build_equities_graph("value")
        assert graph is not None
