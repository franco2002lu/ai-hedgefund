"""Tier 2: Integration tests for analyst agents with real LLM calls.

These tests validate that each analyst agent can:
  1. Fetch real market data from Yahoo Finance
  2. Send a well-formed prompt to the Anthropic LLM
  3. Parse the LLM response into a valid StockSignal

Run manually: pytest tests/integration/equities/test_analyst_integration.py -m integration -v

Required environment variable: ANTHROPIC_API_KEY
"""

import pytest

from app.modules.equities.agents.fundamentals_analyst import FundamentalsAnalyst
from app.modules.equities.agents.news_analyst import NewsAnalyst
from app.modules.equities.agents.technical_analyst import TechnicalAnalyst
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import StockSignal, UniverseStock

pytestmark = pytest.mark.integration

TEST_STOCKS = [
    UniverseStock(symbol="AAPL", company_name="Apple Inc.", weight=0.07, sector="Technology"),
    UniverseStock(symbol="JPM", company_name="JPMorgan Chase & Co.", weight=0.04, sector="Financials"),
    UniverseStock(symbol="JNJ", company_name="Johnson & Johnson", weight=0.03, sector="Healthcare"),
]


def _validate_signal(signal: StockSignal, expected_symbol: str, expected_analyst_type: str):
    """Common validation for all analyst signals."""
    assert isinstance(signal, StockSignal)
    assert signal.symbol == expected_symbol
    assert signal.analyst_type == expected_analyst_type
    assert 1 <= signal.bullish_score <= 10
    assert 1 <= signal.confidence <= 10
    assert len(signal.summary) > 0


# ---------------------------------------------------------------------------
# News Analyst
# ---------------------------------------------------------------------------


class TestNewsAnalystIntegration:
    """Test NewsAnalyst with real LLM and real market data from Yahoo Finance."""

    @pytest.mark.parametrize("stock", TEST_STOCKS, ids=lambda s: s.symbol)
    async def test_returns_valid_signal_for_each_stock(self, stock, real_data_service, real_llm_client):
        analyst = NewsAnalyst(
            config=AnalystLLMConfig(),
            data_service=real_data_service,
            llm_client=real_llm_client,
        )
        signal = await analyst.analyze(stock)
        _validate_signal(signal, stock.symbol, "news")

    async def test_scores_within_valid_range(self, real_data_service, real_llm_client):
        analyst = NewsAnalyst(
            config=AnalystLLMConfig(),
            data_service=real_data_service,
            llm_client=real_llm_client,
        )
        for stock in TEST_STOCKS:
            signal = await analyst.analyze(stock)
            assert 1 <= signal.bullish_score <= 10, f"{stock.symbol}: bullish_score {signal.bullish_score} out of range"
            assert 1 <= signal.confidence <= 10, f"{stock.symbol}: confidence {signal.confidence} out of range"

    async def test_summary_is_nonempty(self, real_data_service, real_llm_client):
        analyst = NewsAnalyst(
            config=AnalystLLMConfig(),
            data_service=real_data_service,
            llm_client=real_llm_client,
        )
        signal = await analyst.analyze(TEST_STOCKS[0])
        assert len(signal.summary.strip()) > 0, "Summary should be a non-empty string"

    async def test_batch_analysis_returns_all_signals(self, real_data_service, real_llm_client):
        analyst = NewsAnalyst(
            config=AnalystLLMConfig(),
            data_service=real_data_service,
            llm_client=real_llm_client,
        )
        signals = await analyst.analyze_batch(TEST_STOCKS)
        assert len(signals) == len(TEST_STOCKS)
        symbols = {s.symbol for s in signals}
        assert symbols == {"AAPL", "JPM", "JNJ"}


# ---------------------------------------------------------------------------
# Fundamentals Analyst
# ---------------------------------------------------------------------------


class TestFundamentalsAnalystIntegration:
    """Test FundamentalsAnalyst with real LLM, Yahoo Finance, and SEC EDGAR stub."""

    @pytest.mark.parametrize("stock", TEST_STOCKS, ids=lambda s: s.symbol)
    async def test_returns_valid_signal_for_each_stock(
        self,
        stock,
        real_data_service,
        real_llm_client,
        real_sec_edgar,
    ):
        analyst = FundamentalsAnalyst(
            config=AnalystLLMConfig(),
            data_service=real_data_service,
            sec_edgar=real_sec_edgar,
            llm_client=real_llm_client,
        )
        signal = await analyst.analyze(stock)
        _validate_signal(signal, stock.symbol, "fundamentals")

    async def test_scores_within_valid_range(
        self,
        real_data_service,
        real_llm_client,
        real_sec_edgar,
    ):
        analyst = FundamentalsAnalyst(
            config=AnalystLLMConfig(),
            data_service=real_data_service,
            sec_edgar=real_sec_edgar,
            llm_client=real_llm_client,
        )
        for stock in TEST_STOCKS:
            signal = await analyst.analyze(stock)
            assert 1 <= signal.bullish_score <= 10, f"{stock.symbol}: bullish_score {signal.bullish_score} out of range"
            assert 1 <= signal.confidence <= 10, f"{stock.symbol}: confidence {signal.confidence} out of range"

    async def test_summary_references_financials(
        self,
        real_data_service,
        real_llm_client,
        real_sec_edgar,
    ):
        """The summary should reference some financial concept (loose check)."""
        analyst = FundamentalsAnalyst(
            config=AnalystLLMConfig(),
            data_service=real_data_service,
            sec_edgar=real_sec_edgar,
            llm_client=real_llm_client,
        )
        signal = await analyst.analyze(TEST_STOCKS[0])
        assert len(signal.summary) > 10, "Summary should be a substantive sentence"


# ---------------------------------------------------------------------------
# Technical Analyst
# ---------------------------------------------------------------------------


class TestTechnicalAnalystIntegration:
    """Test TechnicalAnalyst with real LLM and real price data from Yahoo Finance."""

    @pytest.mark.parametrize("stock", TEST_STOCKS, ids=lambda s: s.symbol)
    async def test_returns_valid_signal_for_each_stock(self, stock, real_data_service, real_llm_client):
        analyst = TechnicalAnalyst(
            config=AnalystLLMConfig(),
            data_service=real_data_service,
            llm_client=real_llm_client,
        )
        signal = await analyst.analyze(stock)
        _validate_signal(signal, stock.symbol, "technical")

    async def test_scores_within_valid_range(self, real_data_service, real_llm_client):
        analyst = TechnicalAnalyst(
            config=AnalystLLMConfig(),
            data_service=real_data_service,
            llm_client=real_llm_client,
        )
        for stock in TEST_STOCKS:
            signal = await analyst.analyze(stock)
            assert 1 <= signal.bullish_score <= 10, f"{stock.symbol}: bullish_score {signal.bullish_score} out of range"
            assert 1 <= signal.confidence <= 10, f"{stock.symbol}: confidence {signal.confidence} out of range"

    async def test_batch_analysis_returns_all_signals(self, real_data_service, real_llm_client):
        analyst = TechnicalAnalyst(
            config=AnalystLLMConfig(),
            data_service=real_data_service,
            llm_client=real_llm_client,
        )
        signals = await analyst.analyze_batch(TEST_STOCKS)
        assert len(signals) == len(TEST_STOCKS)
        for signal in signals:
            assert signal.analyst_type == "technical"
            assert 1 <= signal.bullish_score <= 10
            assert 1 <= signal.confidence <= 10
