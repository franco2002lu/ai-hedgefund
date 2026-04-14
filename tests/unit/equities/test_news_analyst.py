"""Unit tests for the News Analyst agent (mocked LLM)."""

from unittest.mock import AsyncMock

import pytest

from app.modules.equities.agents.news_analyst import NewsAnalyst
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import StockSignal, UniverseStock


def _make_stock(**overrides) -> UniverseStock:
    defaults = dict(symbol="AAPL", company_name="Apple Inc.", weight=0.05, sector="Technology")
    defaults.update(overrides)
    return UniverseStock(**defaults)


def _make_llm_response(bullish_score=7, confidence=8, summary="Positive"):
    return {"bullish_score": bullish_score, "confidence": confidence, "summary": summary}


def _make_articles():
    return [
        {"title": "Apple beats earnings", "source": "Reuters", "published_at": "2026-04-14T10:00:00Z"},
        {"title": "iPhone sales surge", "source": "Bloomberg", "published_at": "2026-04-14T11:00:00Z"},
    ]


def _make_analyst(llm_response=None, llm_side_effect=None):
    llm_client = AsyncMock()
    if llm_side_effect:
        llm_client.invoke.side_effect = llm_side_effect
    else:
        llm_client.invoke.return_value = llm_response or _make_llm_response()
    analyst = NewsAnalyst(config=AnalystLLMConfig(), llm_client=llm_client)
    return analyst, llm_client


class TestNewsAnalyst:
    async def test_returns_valid_stock_signal(self):
        analyst, _ = _make_analyst()
        signal = await analyst.analyze(_make_stock(), articles=_make_articles())

        assert isinstance(signal, StockSignal)
        assert signal.symbol == "AAPL"
        assert signal.analyst_type == "news"
        assert signal.bullish_score == 7
        assert signal.confidence == 8

    async def test_empty_articles_are_allowed(self):
        analyst, _ = _make_analyst()
        signal = await analyst.analyze(_make_stock(), articles=[])
        assert isinstance(signal, StockSignal)

    async def test_passes_articles_into_prompt(self):
        analyst, llm_client = _make_analyst()
        await analyst.analyze(_make_stock(), articles=_make_articles())

        user_prompt = llm_client.invoke.call_args.args[0]
        assert "Apple beats earnings" in user_prompt
        assert "iPhone sales surge" in user_prompt

    async def test_does_not_call_data_service(self):
        analyst, _ = _make_analyst()
        # The analyst no longer has a data_service attribute at all, OR if kept, it's None.
        assert not hasattr(analyst, "data_service") or analyst.data_service is None
        await analyst.analyze(_make_stock(), articles=_make_articles())

    async def test_handles_malformed_llm_response(self):
        analyst, _ = _make_analyst(llm_response={"bullish_score": 3})
        signal = await analyst.analyze(_make_stock(), articles=_make_articles())
        assert signal.bullish_score == 3
        assert signal.confidence == 5  # default
        assert signal.summary == "No analysis available."

    async def test_propagates_llm_errors(self):
        analyst, _ = _make_analyst(llm_side_effect=TimeoutError("timeout"))
        with pytest.raises(TimeoutError):
            await analyst.analyze(_make_stock(), articles=_make_articles())

    async def test_branch_name_selects_overlay(self):
        analyst, llm_client = _make_analyst()
        analyst.branch_name = "growth"
        await analyst.analyze(_make_stock(sector="Technology"), articles=_make_articles())

        system_prompt = llm_client.invoke.call_args.kwargs["system_prompt"]
        assert "News Analyst" in system_prompt
        assert "Growth Branch" in system_prompt


class TestAnalyzeBatch:
    async def test_distributes_articles_per_symbol(self):
        analyst, _ = _make_analyst()
        stocks = [_make_stock(symbol="AAPL"), _make_stock(symbol="MSFT")]
        articles_by_symbol = {
            "AAPL": _make_articles(),
            "MSFT": [{"title": "MSFT news", "source": "WSJ", "published_at": "2026-04-14T10:00:00Z"}],
        }

        signals = await analyst.analyze_batch(stocks, articles_by_symbol=articles_by_symbol, max_concurrent=2)

        assert len(signals) == 2
        assert {s.symbol for s in signals} == {"AAPL", "MSFT"}

    async def test_missing_symbol_in_mapping_uses_empty_list(self):
        analyst, _ = _make_analyst()
        stock = _make_stock(symbol="ZZZ")

        signals = await analyst.analyze_batch([stock], articles_by_symbol={}, max_concurrent=2)

        assert len(signals) == 1
        assert signals[0].symbol == "ZZZ"

    async def test_failure_yields_neutral_fallback(self):
        analyst, _ = _make_analyst(llm_side_effect=Exception("boom"))
        stock = _make_stock()

        signals = await analyst.analyze_batch(
            [stock],
            articles_by_symbol={"AAPL": _make_articles()},
            max_concurrent=2,
        )

        assert len(signals) == 1
        assert signals[0].bullish_score == 5
        assert signals[0].confidence == 1

    async def test_analyze_batch_tolerates_extra_kwargs(self):
        """CLAUDE.md requires analyze_batch to accept **kwargs for forward compat."""
        analyst, _ = _make_analyst()
        signals = await analyst.analyze_batch(
            [_make_stock()],
            articles_by_symbol={"AAPL": _make_articles()},
            max_concurrent=2,
            unknown_future_kwarg="hello",  # would crash without **kwargs
        )
        assert len(signals) == 1


class TestNewsAnalystSkillsDir:
    def test_stores_skills_dir_on_init(self, tmp_path):
        analyst = NewsAnalyst(
            config=AnalystLLMConfig(),
            skills_dir=tmp_path,
        )
        assert analyst.skills_dir == tmp_path

    def test_default_skills_dir_is_none(self):
        analyst = NewsAnalyst(config=AnalystLLMConfig())
        assert analyst.skills_dir is None
