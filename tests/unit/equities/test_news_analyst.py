"""Unit tests for the News Analyst agent (mocked LLM + data service)."""

from unittest.mock import AsyncMock

import pytest

from app.modules.equities.agents.news_analyst import NewsAnalyst
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import StockSignal, UniverseStock

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_stock(**overrides) -> UniverseStock:
    defaults = dict(
        symbol="AAPL",
        company_name="Apple Inc.",
        weight=0.05,
        sector="Technology",
    )
    defaults.update(overrides)
    return UniverseStock(**defaults)


def _make_llm_response(bullish_score=7, confidence=8, summary="Positive news sentiment"):
    return {"bullish_score": bullish_score, "confidence": confidence, "summary": summary}


def _make_news_data(articles=None):
    if articles is None:
        articles = [
            {"title": "Apple beats earnings expectations"},
            {"title": "iPhone sales surge in Q4"},
        ]
    return {"articles": articles}


def _make_analyst(
    llm_response=None,
    news_data=None,
    llm_side_effect=None,
    data_side_effect=None,
):
    config = AnalystLLMConfig()
    data_service = AsyncMock()
    llm_client = AsyncMock()

    if data_side_effect:
        data_service.get_news.side_effect = data_side_effect
    else:
        data_service.get_news.return_value = news_data or _make_news_data()

    if llm_side_effect:
        llm_client.invoke.side_effect = llm_side_effect
    else:
        llm_client.invoke.return_value = llm_response or _make_llm_response()

    analyst = NewsAnalyst(config=config, data_service=data_service, llm_client=llm_client)
    return analyst, data_service, llm_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNewsAnalyst:
    async def test_returns_valid_stock_signal(self):
        analyst, _, _ = _make_analyst()
        stock = _make_stock()
        signal = await analyst.analyze(stock)

        assert isinstance(signal, StockSignal)
        assert signal.symbol == "AAPL"
        assert signal.analyst_type == "news"
        assert signal.bullish_score == 7
        assert signal.confidence == 8
        assert signal.summary == "Positive news sentiment"

    async def test_analyst_type_is_news(self):
        analyst, _, _ = _make_analyst()
        stock = _make_stock(symbol="MSFT", company_name="Microsoft Corp.")
        signal = await analyst.analyze(stock)

        assert signal.analyst_type == "news"

    async def test_bullish_score_within_range(self):
        for score in [1, 5, 10]:
            analyst, _, _ = _make_analyst(
                llm_response=_make_llm_response(bullish_score=score),
            )
            signal = await analyst.analyze(_make_stock())
            assert 1 <= signal.bullish_score <= 10

    async def test_confidence_within_range(self):
        for conf in [1, 5, 10]:
            analyst, _, _ = _make_analyst(
                llm_response=_make_llm_response(confidence=conf),
            )
            signal = await analyst.analyze(_make_stock())
            assert 1 <= signal.confidence <= 10

    async def test_calls_data_service_for_news(self):
        analyst, data_service, _ = _make_analyst()
        stock = _make_stock(symbol="GOOG")
        await analyst.analyze(stock)

        data_service.get_news.assert_awaited_once_with(symbols=["GOOG"])

    async def test_handles_malformed_llm_response(self):
        """LLM returns a dict missing required fields -> defaults are used."""
        malformed = {"bullish_score": 3}  # missing confidence and summary
        analyst, _, _ = _make_analyst(llm_response=malformed)
        signal = await analyst.analyze(_make_stock())

        assert isinstance(signal, StockSignal)
        assert signal.bullish_score == 3
        # defaults kick in for missing fields
        assert signal.confidence == 5
        assert signal.summary == "No analysis available."

    async def test_handles_llm_timeout(self):
        analyst, _, _ = _make_analyst(llm_side_effect=TimeoutError("LLM timed out"))
        stock = _make_stock()

        with pytest.raises(TimeoutError):
            await analyst.analyze(stock)

    async def test_handles_llm_api_failure(self):
        analyst, _, _ = _make_analyst(llm_side_effect=Exception("API error"))
        stock = _make_stock()

        with pytest.raises(Exception, match="API error"):
            await analyst.analyze(stock)
