"""Tier 2: Integration tests for the full equities pipeline.

Tests the pipeline from analyst signals through portfolio construction and
order generation. Combines real LLM calls with deterministic PM logic.

Run manually: pytest tests/integration/equities/test_pipeline_integration.py -m integration -v

Required environment variable: ANTHROPIC_API_KEY
"""

import pytest

from app.modules.equities.agents.fundamentals_analyst import FundamentalsAnalyst
from app.modules.equities.agents.news_analyst import NewsAnalyst
from app.modules.equities.agents.portfolio_manager import PortfolioManager
from app.modules.equities.agents.technical_analyst import TechnicalAnalyst
from app.modules.equities.config import AgentsConfig, AnalystLLMConfig, PortfolioConfig
from app.modules.equities.models import (
    RebalanceOrder,
    StockSignal,
    UniverseStock,
)

pytestmark = pytest.mark.integration


async def _fetch_news_articles_for(stock, data_service):
    """Mimic the prefetch_news graph node for integration tests: fetch market + sector articles."""
    market = await data_service.get_market_news(limit=10)
    sector = await data_service.get_sector_news(stock.sector, limit=10) if stock.sector else {"articles": []}
    return list(market.get("articles", [])) + list(sector.get("articles", []))


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

PIPELINE_STOCKS = [
    UniverseStock(symbol="AAPL", company_name="Apple Inc.", weight=0.07, sector="Technology"),
    UniverseStock(symbol="JPM", company_name="JPMorgan Chase & Co.", weight=0.04, sector="Financials"),
]

TEN_STOCKS = [
    UniverseStock(symbol="AAPL", company_name="Apple Inc.", weight=0.07, sector="Technology"),
    UniverseStock(symbol="MSFT", company_name="Microsoft Corp.", weight=0.06, sector="Technology"),
    UniverseStock(symbol="AMZN", company_name="Amazon.com Inc.", weight=0.05, sector="Consumer Cyclical"),
    UniverseStock(symbol="GOOGL", company_name="Alphabet Inc.", weight=0.04, sector="Communication Services"),
    UniverseStock(symbol="META", company_name="Meta Platforms Inc.", weight=0.04, sector="Communication Services"),
    UniverseStock(symbol="JPM", company_name="JPMorgan Chase & Co.", weight=0.03, sector="Financials"),
    UniverseStock(symbol="JNJ", company_name="Johnson & Johnson", weight=0.03, sector="Healthcare"),
    UniverseStock(symbol="V", company_name="Visa Inc.", weight=0.02, sector="Financials"),
    UniverseStock(symbol="PG", company_name="Procter & Gamble Co.", weight=0.02, sector="Consumer Defensive"),
    UniverseStock(symbol="UNH", company_name="UnitedHealth Group Inc.", weight=0.02, sector="Healthcare"),
]


# ---------------------------------------------------------------------------
# Portfolio Manager with real signals (from LLM)
# ---------------------------------------------------------------------------


class TestPortfolioManagerWithRealSignals:
    """Feed real analyst signals into the deterministic PM."""

    async def test_real_signals_produce_valid_composite_scores(
        self,
        real_data_service,
        real_llm_client,
        real_sec_edgar,
    ):
        """Run 3 analysts on AAPL, feed signals into PM, verify scores."""
        llm_config = AnalystLLMConfig()
        stock = PIPELINE_STOCKS[0]  # AAPL

        news = NewsAnalyst(config=llm_config, llm_client=real_llm_client)
        funds = FundamentalsAnalyst(
            config=llm_config,
            data_service=real_data_service,
            sec_edgar=real_sec_edgar,
            llm_client=real_llm_client,
        )
        tech = TechnicalAnalyst(config=llm_config, data_service=real_data_service, llm_client=real_llm_client)

        articles_for_stock = await _fetch_news_articles_for(stock, real_data_service)
        signals = []
        for analyst in [news, funds, tech]:
            if isinstance(analyst, NewsAnalyst):
                signal = await analyst.analyze(stock, articles=articles_for_stock)
            else:
                signal = await analyst.analyze(stock)
            signals.append(signal)

        assert len(signals) == 3
        analyst_types = {s.analyst_type for s in signals}
        assert analyst_types == {"news", "fundamentals", "technical"}

        pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
        scores = pm.compute_composite_scores(signals)
        assert len(scores) == 1

        score = scores[0]
        assert score.symbol == "AAPL"
        assert 1.0 <= score.composite_score <= 10.0
        assert 1.0 <= score.composite_confidence <= 10.0
        assert score.conviction > 0


# ---------------------------------------------------------------------------
# Full pipeline: analysts -> PM -> orders
# ---------------------------------------------------------------------------


class TestFullPipelineIntegration:
    """Run the full analysis pipeline on a small stock set with real LLM."""

    async def test_signals_to_orders(self, real_data_service, real_llm_client, real_sec_edgar):
        """Run 3 analysts on 2 stocks, score, select, size, and generate orders."""
        config = AgentsConfig()
        llm_config = config.news_analyst

        news = NewsAnalyst(
            config=llm_config,
            llm_client=real_llm_client,
        )
        funds = FundamentalsAnalyst(
            config=llm_config,
            data_service=real_data_service,
            sec_edgar=real_sec_edgar,
            llm_client=real_llm_client,
        )
        tech = TechnicalAnalyst(
            config=llm_config,
            data_service=real_data_service,
            llm_client=real_llm_client,
        )

        # Run all three analysts on both stocks
        articles_by_symbol = {}
        for s in PIPELINE_STOCKS:
            articles_by_symbol[s.symbol] = await _fetch_news_articles_for(s, real_data_service)
        all_signals: list[StockSignal] = []
        for analyst in [news, funds, tech]:
            if isinstance(analyst, NewsAnalyst):
                signals = await analyst.analyze_batch(PIPELINE_STOCKS, articles_by_symbol=articles_by_symbol)
            else:
                signals = await analyst.analyze_batch(PIPELINE_STOCKS)
            all_signals.extend(signals)

        # 2 stocks * 3 analysts = 6 signals
        assert len(all_signals) == 6
        symbols_seen = {s.symbol for s in all_signals}
        assert symbols_seen == {"AAPL", "JPM"}

        # Score and select
        pm = PortfolioManager(agents_config=config, portfolio_config=PortfolioConfig())
        scores = pm.compute_composite_scores(all_signals)
        assert len(scores) == 2

        for score in scores:
            assert 1.0 <= score.composite_score <= 10.0
            assert 1.0 <= score.composite_confidence <= 10.0
            assert score.conviction > 0

        # Select and size
        selected = pm.select_stocks(scores)
        assert len(selected) >= 1

        sized = pm.size_positions(selected)
        total_weight = sum(s.target_weight for s in sized)
        assert total_weight == pytest.approx(1.0)

        # Generate orders (empty portfolio -> all BUYs)
        prices = {}
        for s in sized:
            price = await real_data_service.get_current_price(s.symbol)
            if price:
                prices[s.symbol] = price
        assert len(prices) > 0, "Should fetch at least one price"

        orders = pm.generate_orders(sized, {}, 1_000_000.0, prices)
        assert len(orders) > 0
        for order in orders:
            assert isinstance(order, RebalanceOrder)
            assert order.side == "buy"
            assert order.quantity > 0

    async def test_screening_to_analysis_to_orders(
        self,
        real_data_service,
        real_llm_client,
        real_sec_edgar,
    ):
        """Simulate the full pipeline on 10 stocks with a pass-through screener."""
        config = AgentsConfig()
        llm_config = config.news_analyst

        # Use a small subset (3 stocks) for LLM calls to keep costs down
        analysis_stocks = TEN_STOCKS[:3]

        news = NewsAnalyst(
            config=llm_config,
            llm_client=real_llm_client,
        )
        funds = FundamentalsAnalyst(
            config=llm_config,
            data_service=real_data_service,
            sec_edgar=real_sec_edgar,
            llm_client=real_llm_client,
        )
        tech = TechnicalAnalyst(
            config=llm_config,
            data_service=real_data_service,
            llm_client=real_llm_client,
        )

        articles_by_symbol = {}
        for s in analysis_stocks:
            articles_by_symbol[s.symbol] = await _fetch_news_articles_for(s, real_data_service)
        all_signals: list[StockSignal] = []
        for analyst in [news, funds, tech]:
            if isinstance(analyst, NewsAnalyst):
                signals = await analyst.analyze_batch(analysis_stocks, articles_by_symbol=articles_by_symbol)
            else:
                signals = await analyst.analyze_batch(analysis_stocks)
            all_signals.extend(signals)

        # 3 stocks * 3 analysts = 9 signals
        assert len(all_signals) == 9

        pm = PortfolioManager(agents_config=config, portfolio_config=PortfolioConfig())
        scores = pm.compute_composite_scores(all_signals)
        assert len(scores) == 3

        selected = pm.select_stocks(scores)
        assert len(selected) >= 1

        sized = pm.size_positions(selected)
        total_weight = sum(s.target_weight for s in sized)
        assert total_weight == pytest.approx(1.0)

        prices = {}
        for s in sized:
            price = await real_data_service.get_current_price(s.symbol)
            if price:
                prices[s.symbol] = price

        orders = pm.generate_orders(sized, {}, 1_000_000.0, prices)
        assert len(orders) > 0
        for order in orders:
            assert isinstance(order, RebalanceOrder)
            assert order.quantity > 0
