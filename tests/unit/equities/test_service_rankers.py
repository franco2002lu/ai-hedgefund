"""Ranker selection: LLM ranker for LLM-capable analysts, deterministic otherwise."""

from unittest.mock import MagicMock

from app.modules.equities.agents.ranker import CrossSectionalRanker, DeterministicRanker
from app.modules.equities.service import build_ranker


def _llm_client(with_raw=True):
    return MagicMock(spec=["invoke", "invoke_raw"] if with_raw else ["invoke"])


def test_none_analyst_yields_none():
    assert build_ranker(None, "news", "growth") is None


def test_llm_analyst_gets_cross_sectional_ranker():
    analyst = MagicMock(spec=["llm_client", "skills_dir"])
    analyst.llm_client = _llm_client()
    analyst.skills_dir = None
    r = build_ranker(analyst, "news", "growth")
    assert isinstance(r, CrossSectionalRanker)
    assert r.analyst_type == "news" and r.branch_name == "growth"


def test_wrapped_analyst_unwraps_inner_llm_client():
    inner = MagicMock(spec=["llm_client", "skills_dir"])
    inner.llm_client = _llm_client()
    inner.skills_dir = None
    wrapper = MagicMock(spec=["_analyst"])
    wrapper._analyst = inner
    r = build_ranker(wrapper, "fundamentals", "value")
    assert isinstance(r, CrossSectionalRanker)


def test_client_without_invoke_raw_gets_deterministic():
    analyst = MagicMock(spec=["llm_client"])
    analyst.llm_client = _llm_client(with_raw=False)
    r = build_ranker(analyst, "technical", "growth")
    assert isinstance(r, DeterministicRanker)


def test_quant_analyst_gets_deterministic():
    analyst = MagicMock(spec=["analyze_batch"])
    r = build_ranker(analyst, "news", "growth")
    assert isinstance(r, DeterministicRanker)
