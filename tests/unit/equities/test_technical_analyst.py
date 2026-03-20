"""Unit tests for the Technical Analyst agent (mocked LLM + data service)."""

from unittest.mock import AsyncMock

import pytest

from app.modules.equities.agents.technical_analyst import TechnicalAnalyst
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


def _make_llm_response(bullish_score=6, confidence=7, summary="Uptrend with strong momentum"):
    return {"bullish_score": bullish_score, "confidence": confidence, "summary": summary}


def _make_price_data(num_bars=252):
    """Generate synthetic daily price bars."""
    bars = []
    for i in range(num_bars):
        close = 150.0 + i * 0.1
        bars.append({
            "timestamp": f"2025-01-{(i % 28) + 1:02d}",
            "open": close - 1.0,
            "high": close + 1.5,
            "low": close - 1.5,
            "close": close,
            "volume": 1_000_000 + i * 100,
        })
    return {"bars": bars}


def _make_analyst(
    llm_response=None,
    price_data=None,
    llm_side_effect=None,
    data_side_effect=None,
):
    config = AnalystLLMConfig()
    data_service = AsyncMock()
    llm_client = AsyncMock()

    if data_side_effect:
        data_service.get_prices.side_effect = data_side_effect
    else:
        data_service.get_prices.return_value = price_data or _make_price_data()

    if llm_side_effect:
        llm_client.invoke.side_effect = llm_side_effect
    else:
        llm_client.invoke.return_value = llm_response or _make_llm_response()

    analyst = TechnicalAnalyst(config=config, data_service=data_service, llm_client=llm_client)
    return analyst, data_service, llm_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTechnicalAnalyst:
    async def test_returns_valid_stock_signal(self):
        analyst, _, _ = _make_analyst()
        stock = _make_stock()
        signal = await analyst.analyze(stock)

        assert isinstance(signal, StockSignal)
        assert signal.symbol == "AAPL"
        assert signal.analyst_type == "technical"
        assert signal.bullish_score == 6
        assert signal.confidence == 7
        assert signal.summary == "Uptrend with strong momentum"

    async def test_analyst_type_is_technical(self):
        analyst, _, _ = _make_analyst()
        stock = _make_stock(symbol="NVDA", company_name="NVIDIA Corp.")
        signal = await analyst.analyze(stock)

        assert signal.analyst_type == "technical"

    async def test_calls_data_service_for_prices(self):
        analyst, data_service, _ = _make_analyst()
        stock = _make_stock(symbol="META")
        await analyst.analyze(stock)

        data_service.get_prices.assert_awaited_once()
        call_args = data_service.get_prices.call_args
        assert call_args[0][0] == "META"  # first positional arg is the symbol

    async def test_handles_malformed_llm_response(self):
        """LLM returns a dict missing required fields -> defaults are used."""
        malformed = {"summary": "Some analysis"}  # missing scores
        analyst, _, _ = _make_analyst(llm_response=malformed)
        signal = await analyst.analyze(_make_stock())

        assert isinstance(signal, StockSignal)
        assert signal.bullish_score == 5  # default
        assert signal.confidence == 5  # default
        assert signal.summary == "Some analysis"

    async def test_handles_llm_timeout(self):
        analyst, _, _ = _make_analyst(llm_side_effect=TimeoutError("LLM timed out"))
        stock = _make_stock()

        with pytest.raises(TimeoutError):
            await analyst.analyze(stock)

    async def test_handles_insufficient_price_history(self):
        """data_service returns few/no bars; analyst still produces a signal."""
        analyst, _, _ = _make_analyst(price_data={"bars": []})
        stock = _make_stock()
        signal = await analyst.analyze(stock)

        assert isinstance(signal, StockSignal)
        assert signal.symbol == "AAPL"
        assert signal.analyst_type == "technical"
        assert 1 <= signal.bullish_score <= 10
        assert 1 <= signal.confidence <= 10
