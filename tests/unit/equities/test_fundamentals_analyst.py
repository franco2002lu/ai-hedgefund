"""Unit tests for the Fundamentals Analyst agent (mocked LLM, data service, EDGAR)."""

from unittest.mock import AsyncMock

import pytest

from app.modules.equities.agents.fundamentals_analyst import FundamentalsAnalyst
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


def _make_llm_response(bullish_score=8, confidence=7, summary="Strong fundamentals"):
    return {"bullish_score": bullish_score, "confidence": confidence, "summary": summary}


def _make_metrics_data(metrics=None):
    if metrics is None:
        metrics = [{"pe_ratio": 25.0, "pb_ratio": 10.0, "roe": 0.15}]
    return {"metrics": metrics}


class _FakeEarning:
    def __init__(self, fiscal_quarter="Q4 2025", eps=1.50, revenue=100_000_000):
        self.fiscal_quarter = fiscal_quarter
        self.eps = eps
        self.revenue = revenue


def _make_earnings(count=4):
    quarters = ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"]
    return [_FakeEarning(fiscal_quarter=quarters[i]) for i in range(min(count, 4))]


def _make_analyst(
    llm_response=None,
    metrics_data=None,
    earnings=None,
    llm_side_effect=None,
    sec_edgar_side_effect=None,
):
    config = AnalystLLMConfig()
    data_service = AsyncMock()
    sec_edgar = AsyncMock()
    llm_client = AsyncMock()

    data_service.get_metrics.return_value = metrics_data or _make_metrics_data()

    if sec_edgar_side_effect:
        sec_edgar.get_earnings_data.side_effect = sec_edgar_side_effect
    else:
        sec_edgar.get_earnings_data.return_value = earnings if earnings is not None else _make_earnings()

    if llm_side_effect:
        llm_client.invoke.side_effect = llm_side_effect
    else:
        llm_client.invoke.return_value = llm_response or _make_llm_response()

    analyst = FundamentalsAnalyst(
        config=config,
        data_service=data_service,
        sec_edgar=sec_edgar,
        llm_client=llm_client,
    )
    return analyst, data_service, sec_edgar, llm_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFundamentalsAnalyst:
    async def test_returns_valid_stock_signal(self):
        analyst, _, _, _ = _make_analyst()
        stock = _make_stock()
        signal = await analyst.analyze(stock)

        assert isinstance(signal, StockSignal)
        assert signal.symbol == "AAPL"
        assert signal.analyst_type == "fundamentals"
        assert signal.bullish_score == 8
        assert signal.confidence == 7
        assert signal.summary == "Strong fundamentals"

    async def test_analyst_type_is_fundamentals(self):
        analyst, _, _, _ = _make_analyst()
        stock = _make_stock(symbol="TSLA", company_name="Tesla Inc.")
        signal = await analyst.analyze(stock)

        assert signal.analyst_type == "fundamentals"

    async def test_calls_data_service_for_metrics(self):
        analyst, data_service, _, _ = _make_analyst()
        stock = _make_stock(symbol="GOOG")
        await analyst.analyze(stock)

        data_service.get_metrics.assert_awaited_once_with("GOOG")

    async def test_calls_sec_edgar_for_earnings(self):
        analyst, _, sec_edgar, _ = _make_analyst()
        stock = _make_stock(symbol="AMZN")
        await analyst.analyze(stock)

        sec_edgar.get_earnings_data.assert_awaited_once_with("AMZN")

    async def test_handles_malformed_llm_response(self):
        """LLM returns a dict missing required fields -> defaults are used."""
        malformed = {"confidence": 6}  # missing bullish_score and summary
        analyst, _, _, _ = _make_analyst(llm_response=malformed)
        signal = await analyst.analyze(_make_stock())

        assert isinstance(signal, StockSignal)
        assert signal.bullish_score == 5  # default
        assert signal.confidence == 6
        assert signal.summary == "No analysis available."  # default

    async def test_handles_llm_timeout(self):
        analyst, _, _, _ = _make_analyst(llm_side_effect=TimeoutError("LLM timed out"))
        stock = _make_stock()

        with pytest.raises(TimeoutError):
            await analyst.analyze(stock)

    async def test_handles_missing_edgar_data(self):
        """sec_edgar returns empty list; analyst still produces signal from Yahoo data."""
        analyst, _, _, _ = _make_analyst(earnings=[])
        stock = _make_stock()
        signal = await analyst.analyze(stock)

        assert isinstance(signal, StockSignal)
        assert signal.symbol == "AAPL"
        assert signal.analyst_type == "fundamentals"
        assert 1 <= signal.bullish_score <= 10
        assert 1 <= signal.confidence <= 10

    async def test_passes_system_prompt_to_llm_client(self):
        """When branch_name is set, invoke() receives a system_prompt kwarg."""
        analyst, _, _, llm_client = _make_analyst()
        analyst.branch_name = "growth"
        stock = _make_stock(sector="Technology")
        await analyst.analyze(stock)

        call_kwargs = llm_client.invoke.call_args.kwargs
        assert "system_prompt" in call_kwargs
        assert "Fundamentals Analyst" in call_kwargs["system_prompt"]
        assert "Growth Branch" in call_kwargs["system_prompt"]

    async def test_no_branch_still_passes_system_prompt(self):
        """Even with default empty branch_name, system_prompt is passed (base only)."""
        analyst, _, _, llm_client = _make_analyst()
        stock = _make_stock()
        await analyst.analyze(stock)

        call_kwargs = llm_client.invoke.call_args.kwargs
        assert "system_prompt" in call_kwargs
        assert "Fundamentals Analyst" in call_kwargs["system_prompt"]

    async def test_simplified_user_prompt(self):
        """User prompt should be context + simple instruction, no score format."""
        analyst, _, _, llm_client = _make_analyst()
        stock = _make_stock()
        await analyst.analyze(stock)

        user_prompt = llm_client.invoke.call_args.args[0]
        # Should contain the context (from format_fundamentals_context)
        assert "AAPL" in user_prompt
        # Should NOT contain the old format instruction
        assert "bullish_score (1-10)" not in user_prompt


# ---------------------------------------------------------------------------
# skills_dir parameter tests
# ---------------------------------------------------------------------------


class TestFundamentalsAnalystSkillsDir:
    def test_stores_skills_dir_on_init(self, tmp_path):
        from app.modules.equities.agents.fundamentals_analyst import FundamentalsAnalyst
        from app.modules.equities.config import AnalystLLMConfig

        analyst = FundamentalsAnalyst(
            config=AnalystLLMConfig(),
            skills_dir=tmp_path,
        )
        assert analyst.skills_dir == tmp_path

    def test_default_skills_dir_is_none(self):
        from app.modules.equities.agents.fundamentals_analyst import FundamentalsAnalyst
        from app.modules.equities.config import AnalystLLMConfig

        analyst = FundamentalsAnalyst(config=AnalystLLMConfig())
        assert analyst.skills_dir is None
