from __future__ import annotations

import logging
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)


class EquitiesWorkflowState(TypedDict, total=False):
    """State passed through the LangGraph workflow."""

    branch_name: str
    branch_id: str
    universe: list
    screened: list
    signals: Annotated[list, operator.add]
    scores: list
    orders: list
    trades: list
    deps: dict


def build_equities_graph(branch_name: str):
    """Builds the LangGraph workflow for one equities branch."""

    async def fetch_universe(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        provider = deps["universe_provider"]
        as_of_date = deps.get("as_of_date")
        branch_name = state.get("branch_name", "")
        holdings = await provider.get_holdings(
            branch_name, as_of_date=as_of_date,
        )
        logger.info("Fetched %d holdings for '%s' branch", len(holdings), branch_name)
        return {"universe": holdings}

    async def screen_stocks(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        screener = deps["screener"]
        data_service = deps["data_service"]
        as_of_date = deps.get("as_of_date")
        screened = await screener.screen(state["universe"], data_service, as_of_date=as_of_date)
        logger.info("Screened %d -> %d stocks", len(state["universe"]), len(screened))
        return {"screened": screened}

    async def news_analysis(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        analyst = deps["news_analyst"]
        max_concurrent = deps.get("max_concurrent_analyses", 10)
        signals = await analyst.analyze_batch(state["screened"], max_concurrent=max_concurrent)
        logger.info("News analyst produced %d signals", len(signals))
        return {"signals": list(signals)}

    async def fundamentals_analysis(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        analyst = deps["fundamentals_analyst"]
        max_concurrent = deps.get("max_concurrent_analyses", 10)
        signals = await analyst.analyze_batch(state["screened"], max_concurrent=max_concurrent)
        logger.info("Fundamentals analyst produced %d signals", len(signals))
        return {"signals": list(signals)}

    async def technical_analysis(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        analyst = deps["technical_analyst"]
        max_concurrent = deps.get("max_concurrent_analyses", 10)
        signals = await analyst.analyze_batch(state["screened"], max_concurrent=max_concurrent)
        logger.info("Technical analyst produced %d signals", len(signals))
        return {"signals": list(signals)}

    async def portfolio_decision(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        pm = deps["portfolio_manager"]
        data_service = deps["data_service"]
        current_positions = deps.get("current_positions", {})
        nav = deps.get("nav", 1_000_000.0)
        scores = pm.compute_composite_scores(state["signals"])
        selected = pm.select_stocks(scores)
        sized = pm.size_positions(selected)
        prices: dict[str, float] = {}
        for s in sized:
            price = await data_service.get_current_price(s.symbol)
            if price:
                prices[s.symbol] = price
        for sym in current_positions:
            if sym not in prices:
                price = await data_service.get_current_price(sym)
                if price:
                    prices[sym] = price
        orders = pm.generate_orders(sized, current_positions, nav, prices)
        logger.info("Portfolio manager generated %d orders", len(orders))
        return {"scores": scores, "orders": orders}

    async def execute_trades(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        trade_fn = deps.get("execute_trade_fn")
        trades = []
        if trade_fn:
            for order in state.get("orders", []):
                trade = await trade_fn(order)
                if trade:
                    trades.append(trade)
        logger.info("Executed %d trades", len(trades))
        return {"trades": trades}

    graph = StateGraph(EquitiesWorkflowState)
    graph.add_node("fetch_universe", fetch_universe)
    graph.add_node("screen_stocks", screen_stocks)
    graph.add_node("news_analysis", news_analysis)
    graph.add_node("fundamentals_analysis", fundamentals_analysis)
    graph.add_node("technical_analysis", technical_analysis)
    graph.add_node("portfolio_decision", portfolio_decision)
    graph.add_node("execute_trades", execute_trades)

    graph.set_entry_point("fetch_universe")
    graph.add_edge("fetch_universe", "screen_stocks")
    graph.add_edge("screen_stocks", "news_analysis")
    graph.add_edge("screen_stocks", "fundamentals_analysis")
    graph.add_edge("screen_stocks", "technical_analysis")
    graph.add_edge("news_analysis", "portfolio_decision")
    graph.add_edge("fundamentals_analysis", "portfolio_decision")
    graph.add_edge("technical_analysis", "portfolio_decision")
    graph.add_edge("portfolio_decision", "execute_trades")
    graph.add_edge("execute_trades", END)

    return graph.compile()
