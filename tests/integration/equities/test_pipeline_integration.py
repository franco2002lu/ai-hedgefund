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
    CompositeScore,
    RebalanceOrder,
    StockSignal,
    UniverseStock,
)

pytestmark = pytest.mark.integration


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

    @pytest.mark.asyncio
    async def test_real_signals_produce_valid_composite_scores(
        self,
        real_data_service,
        real_llm_client,
        real_sec_edgar,
    ):
        """Run 3 analysts on AAPL, feed signals into PM, verify scores."""
        llm_config = AnalystLLMConfig()
        stock = PIPELINE_STOCKS[0]  # AAPL

        news = NewsAnalyst(config=llm_config, data_service=real_data_service, llm_client=real_llm_client)
        funds = FundamentalsAnalyst(
            config=llm_config,
            data_service=real_data_service,
            sec_edgar=real_sec_edgar,
            llm_client=real_llm_client,
        )
        tech = TechnicalAnalyst(config=llm_config, data_service=real_data_service, llm_client=real_llm_client)

        signals = []
        for analyst in [news, funds, tech]:
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
        # Composite score is weighted average of bullish_scores (each 1-10)
        assert 1.0 <= score.composite_score <= 10.0
        assert 1.0 <= score.composite_confidence <= 10.0
        assert score.conviction > 0  # score * confidence

    def test_position_sizing_sums_to_one(self):
        """Verify conviction-weighted position sizing normalizes to 1.0."""
        pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
        selected = [
            CompositeScore(
                symbol=f"S{i}",
                composite_score=7.0 + i * 0.1,
                composite_confidence=7.0,
                conviction=49.0 + i,
            )
            for i in range(10)
        ]
        sized = pm.size_positions(selected)
        total = sum(s.target_weight for s in sized)
        assert total == pytest.approx(1.0), f"Weights sum to {total}, expected 1.0"

    def test_position_sizing_respects_max_weight_cap(self):
        """If one stock has overwhelming conviction, it should be capped at 50%."""
        pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
        selected = [
            CompositeScore(symbol="BIG", composite_score=10.0, composite_confidence=10.0, conviction=100.0),
            CompositeScore(symbol="SMALL", composite_score=2.0, composite_confidence=2.0, conviction=4.0),
        ]
        sized = pm.size_positions(selected)
        for s in sized:
            assert s.target_weight <= 0.50 + 1e-9, f"{s.symbol} weight {s.target_weight} exceeds 50% cap"
        total = sum(s.target_weight for s in sized)
        assert total == pytest.approx(1.0)

    def test_order_generation_produces_valid_orders(self):
        """Empty current positions should generate BUY orders for all selected stocks."""
        pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
        target = [
            CompositeScore(
                symbol="NEW1",
                composite_score=8.0,
                composite_confidence=8.0,
                conviction=64.0,
                target_weight=0.30,
            ),
            CompositeScore(
                symbol="NEW2",
                composite_score=7.0,
                composite_confidence=7.0,
                conviction=49.0,
                target_weight=0.20,
            ),
        ]
        current_positions: dict[str, float] = {}
        nav = 1_000_000.0
        prices = {"NEW1": 150.0, "NEW2": 200.0}

        orders = pm.generate_orders(target, current_positions, nav, prices)
        assert len(orders) == 2
        assert all(o.side == "buy" for o in orders)
        assert all(o.reason == "new_position" for o in orders)
        assert all(o.quantity > 0 for o in orders)

    def test_order_generation_sells_removed_positions(self):
        """Positions not in target should generate SELL orders."""
        pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
        target = [
            CompositeScore(
                symbol="KEEP",
                composite_score=8.0,
                composite_confidence=8.0,
                conviction=64.0,
                target_weight=0.50,
            ),
        ]
        current_positions = {"KEEP": 0.50, "OLD": 0.30}
        nav = 1_000_000.0
        prices = {"KEEP": 150.0, "OLD": 100.0}

        orders = pm.generate_orders(target, current_positions, nav, prices)
        sell_orders = [o for o in orders if o.side == "sell"]
        assert len(sell_orders) >= 1
        assert any(o.symbol == "OLD" for o in sell_orders)

    def test_composite_scoring_ranking(self):
        """AAPL (strong) should rank above WEAK (bearish) in conviction."""
        pm = PortfolioManager(agents_config=AgentsConfig(), portfolio_config=PortfolioConfig())
        signals = [
            # AAPL — strong across the board
            StockSignal(symbol="AAPL", analyst_type="news", bullish_score=8, confidence=8, summary="s"),
            StockSignal(symbol="AAPL", analyst_type="fundamentals", bullish_score=9, confidence=9, summary="s"),
            StockSignal(symbol="AAPL", analyst_type="technical", bullish_score=7, confidence=7, summary="s"),
            # MSFT — moderate
            StockSignal(symbol="MSFT", analyst_type="news", bullish_score=6, confidence=6, summary="s"),
            StockSignal(symbol="MSFT", analyst_type="fundamentals", bullish_score=7, confidence=7, summary="s"),
            StockSignal(symbol="MSFT", analyst_type="technical", bullish_score=5, confidence=5, summary="s"),
            # WEAK — bearish
            StockSignal(symbol="WEAK", analyst_type="news", bullish_score=3, confidence=4, summary="s"),
            StockSignal(symbol="WEAK", analyst_type="fundamentals", bullish_score=3, confidence=4, summary="s"),
            StockSignal(symbol="WEAK", analyst_type="technical", bullish_score=2, confidence=3, summary="s"),
        ]

        scores = pm.compute_composite_scores(signals)
        assert len(scores) == 3

        scores_sorted = sorted(scores, key=lambda s: s.conviction, reverse=True)
        assert scores_sorted[0].symbol == "AAPL"
        assert scores_sorted[-1].symbol == "WEAK"


# ---------------------------------------------------------------------------
# Full pipeline: analysts -> PM -> orders
# ---------------------------------------------------------------------------


class TestFullPipelineIntegration:
    """Run the full analysis pipeline on a small stock set with real LLM."""

    @pytest.mark.asyncio
    async def test_signals_to_orders(self, real_data_service, real_llm_client, real_sec_edgar):
        """Run 3 analysts on 2 stocks, score, select, size, and generate orders."""
        config = AgentsConfig()
        llm_config = config.news_analyst

        news = NewsAnalyst(
            config=llm_config,
            data_service=real_data_service,
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
        all_signals: list[StockSignal] = []
        for analyst in [news, funds, tech]:
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

    @pytest.mark.asyncio
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
            data_service=real_data_service,
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

        all_signals: list[StockSignal] = []
        for analyst in [news, funds, tech]:
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

    @pytest.mark.asyncio
    async def test_db_persistence(self):
        """Verify screening runs, signals, and decisions are persisted.

        This test will be fleshed out once DB models for equities-specific
        tables are implemented (Phase C9). Placeholder for now.
        """
        pass
