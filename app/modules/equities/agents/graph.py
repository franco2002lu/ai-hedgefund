from __future__ import annotations

import asyncio
import logging
import operator
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph

from app.modules.data_platform.manual_news import ManualNewsLoader

logger = logging.getLogger(__name__)


class EquitiesWorkflowState(TypedDict, total=False):
    """State passed through the LangGraph workflow."""

    branch_name: str
    branch_id: str
    universe: list
    screened: list
    signals: Annotated[list, operator.add]
    scores: list
    targets: list
    orders: list
    trades: list
    deps: dict
    news_context: dict


def _window_start(as_of: date | None, window_days: int) -> date | None:
    if as_of is None:
        return None
    return as_of - timedelta(days=window_days)


async def _run_prefetch_news(state: EquitiesWorkflowState) -> dict:
    """Fetch market + per-sector news once per rebalance and load manual articles."""
    deps = state["deps"]
    data_service = deps["data_service"]
    as_of_date = deps.get("as_of_date")
    window_days = deps.get("news_window_days", 7)
    manual_root: Path | None = deps.get("manual_news_root")

    since = _window_start(as_of_date, window_days)

    market_articles: list[dict] = []
    try:
        market_result = await data_service.get_market_news(since=since, limit=20)
        market_articles = list(market_result.get("articles", []))
    except Exception:
        logger.warning("Market news fetch failed; continuing with empty market news", exc_info=True)

    unique_sectors = sorted({s.sector for s in state.get("screened", []) if s.sector})

    async def _fetch_sector(sector: str) -> tuple[str, list[dict]]:
        try:
            sector_result = await data_service.get_sector_news(sector, since=since, limit=20)
            return sector, list(sector_result.get("articles", []))
        except Exception:
            logger.warning("Sector news fetch failed for %s", sector, exc_info=True)
            return sector, []

    if unique_sectors:
        results = await asyncio.gather(*(_fetch_sector(s) for s in unique_sectors))
        sector_articles: dict[str, list[dict]] = dict(results)
    else:
        sector_articles = {}

    manual_articles: list[dict] = []
    if manual_root is not None and as_of_date is not None:
        try:
            loader = ManualNewsLoader(root=manual_root)
            manual_articles = loader.load(reference_date=as_of_date, window_days=window_days)
        except Exception:
            logger.warning("Manual news load failed", exc_info=True)

    logger.info(
        "News prefetch: %d market + %d sector articles across %d sectors + %d manual",
        len(market_articles),
        sum(len(v) for v in sector_articles.values()),
        len(unique_sectors),
        len(manual_articles),
    )

    return {
        "news_context": {
            "market": market_articles,
            "sectors": sector_articles,
            "manual": manual_articles,
        }
    }


def _articles_for_stock(stock, news_context: dict) -> list[dict]:
    """Merge market + stock-sector + manual articles scoped to market or the stock's sector."""
    market = list(news_context.get("market", []))
    sector_map = news_context.get("sectors", {})
    sector_articles = list(sector_map.get(stock.sector, [])) if stock.sector else []
    manual = news_context.get("manual", [])
    manual_matching = [
        a for a in manual if a.get("scope") == "market" or (stock.sector and a.get("scope") == stock.sector)
    ]
    return market + sector_articles + manual_matching


async def _run_news_analysis(state: EquitiesWorkflowState) -> dict:
    deps = state["deps"]
    analyst = deps["news_analyst"]
    max_concurrent = deps.get("max_concurrent_analyses", 10)
    screened = state["screened"]
    news_context = state.get("news_context", {"market": [], "sectors": {}, "manual": []})

    articles_by_symbol = {s.symbol: _articles_for_stock(s, news_context) for s in screened}
    signals = await analyst.analyze_batch(
        screened,
        articles_by_symbol=articles_by_symbol,
        max_concurrent=max_concurrent,
    )
    logger.info("News analyst produced %d signals", len(signals))
    return {"signals": list(signals)}


def build_equities_graph(branch_name: str):
    """Builds the LangGraph workflow for one equities branch."""

    async def fetch_universe(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        provider = deps["universe_provider"]
        as_of_date = deps.get("as_of_date")
        b_name = state.get("branch_name", "")
        holdings = await provider.get_holdings(b_name, as_of_date=as_of_date)
        logger.info("Fetched %d holdings for '%s' branch", len(holdings), b_name)
        return {"universe": holdings}

    async def screen_stocks(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        screener = deps["screener"]
        data_service = deps["data_service"]
        as_of_date = deps.get("as_of_date")
        screened = await screener.screen(state["universe"], data_service, as_of_date=as_of_date)
        logger.info("Screened %d -> %d stocks", len(state["universe"]), len(screened))
        return {"screened": screened}

    async def prefetch_news(state: EquitiesWorkflowState) -> dict:
        return await _run_prefetch_news(state)

    async def news_analysis(state: EquitiesWorkflowState) -> dict:
        return await _run_news_analysis(state)

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
        selected = pm.select_stocks(scores, current_holdings=set(current_positions))
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
        return {"scores": scores, "targets": sized, "orders": orders}

    async def execute_trades(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        trade_fn = deps.get("execute_trade_fn")
        trades = []
        if trade_fn:
            for order in state.get("orders", []):
                trade = await trade_fn(order)
                # submit_order returns a dict for both fills and rejections;
                # only count actual fills as executed trades
                if isinstance(trade, dict) and trade.get("success"):
                    trades.append(trade)
        logger.info("Executed %d trades", len(trades))
        return {"trades": trades}

    graph = StateGraph(EquitiesWorkflowState)
    graph.add_node("fetch_universe", fetch_universe)
    graph.add_node("screen_stocks", screen_stocks)
    graph.add_node("prefetch_news", prefetch_news)
    graph.add_node("news_analysis", news_analysis)
    graph.add_node("fundamentals_analysis", fundamentals_analysis)
    graph.add_node("technical_analysis", technical_analysis)
    graph.add_node("portfolio_decision", portfolio_decision)
    graph.add_node("execute_trades", execute_trades)

    graph.set_entry_point("fetch_universe")
    graph.add_edge("fetch_universe", "screen_stocks")
    graph.add_edge("screen_stocks", "prefetch_news")
    graph.add_edge("prefetch_news", "news_analysis")
    graph.add_edge("screen_stocks", "fundamentals_analysis")
    graph.add_edge("screen_stocks", "technical_analysis")
    # List form = AND-join: portfolio_decision fires once, after ALL three
    # analysts complete. Separate edges would fire it per incoming branch,
    # generating and submitting orders twice per run.
    graph.add_edge(
        ["news_analysis", "fundamentals_analysis", "technical_analysis"],
        "portfolio_decision",
    )
    graph.add_edge("portfolio_decision", "execute_trades")
    graph.add_edge("execute_trades", END)

    return graph.compile()
