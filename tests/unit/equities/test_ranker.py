"""Unit tests for the cross-sectional ranking stage."""

import json
from unittest.mock import AsyncMock, MagicMock

from app.modules.equities.agents.ranker import (
    CrossSectionalRanker,
    DeterministicRanker,
    apply_ranking,
    decile_score,
)
from app.modules.equities.models import StockSignal


def _sig(symbol, score, conf=5):
    return StockSignal(
        symbol=symbol,
        analyst_type="fundamentals",
        bullish_score=score,
        confidence=conf,
        summary="t",
    )


class TestDecileScore:
    def test_spread_n20(self):
        assert decile_score(0, 20) == 10
        assert decile_score(19, 20) == 1
        assert decile_score(10, 20) == 5

    def test_spread_n7(self):
        assert decile_score(0, 7) == 10
        assert decile_score(6, 7) == 1
        assert decile_score(3, 7) == 5

    def test_n1_is_10(self):
        assert decile_score(0, 1) == 10


class TestApplyRanking:
    def test_absent_symbol_keeps_stage1_score(self):
        signals = [_sig("A", 7), _sig("Z", 4)]
        ranked = apply_ranking(signals, ["A"])
        by = {s.symbol: s.bullish_score for s in ranked}
        # A is position 0 of n=1 -> 10; Z is absent -> keeps stage-1 score
        assert by == {"A": 10, "Z": 4}


class TestDeterministicRanker:
    async def test_rank_normalizes_scores_preserving_order(self):
        ranker = DeterministicRanker(min_rank_universe=2)
        signals = [_sig("A", 7), _sig("B", 5), _sig("C", 6)]
        ranked = await ranker.rank(signals)
        by_symbol = {s.symbol: s.bullish_score for s in ranked}
        assert by_symbol == {"A": 10, "C": 5, "B": 1}
        assert all(s.confidence == 5 and s.summary == "t" for s in ranked)

    async def test_tied_scores_share_mean_decile(self):
        ranker = DeterministicRanker(min_rank_universe=2)
        ranked = await ranker.rank([_sig("B", 5), _sig("A", 5)])
        by = {s.symbol: s.bullish_score for s in ranked}
        # tied stocks share the rounded mean of their positional deciles
        # positions 0,1 of n=2 -> deciles 10,1 -> mean 5.5 -> 6
        assert by == {"A": 6, "B": 6}

    async def test_tie_group_in_middle_shares_decile(self):
        ranker = DeterministicRanker(min_rank_universe=2)
        signals = [_sig("A", 7), _sig("B", 5), _sig("C", 5), _sig("D", 5), _sig("E", 3)]
        ranked = await ranker.rank(signals)
        by = {s.symbol: s.bullish_score for s in ranked}
        # n=5 deciles by position: 10, 7, 5, 3, 1; B/C/D occupy positions 1-3
        # -> mean(7,5,3)=5
        assert by == {"A": 10, "B": 5, "C": 5, "D": 5, "E": 1}

    async def test_below_min_universe_passthrough(self):
        ranker = DeterministicRanker(min_rank_universe=5)
        signals = [_sig("A", 7), _sig("B", 3)]
        ranked = await ranker.rank(signals)
        assert {s.bullish_score for s in ranked} == {7, 3}


def _llm(returning: str):
    client = MagicMock()
    client.invoke_raw = AsyncMock(return_value=returning)
    return client


class TestCrossSectionalRanker:
    async def test_reorders_scores_by_llm_ranking(self):
        signals = [_sig("A", 5), _sig("B", 5), _sig("C", 5), _sig("D", 5), _sig("E", 5)]
        llm = _llm(json.dumps({"ranking": ["C", "A", "E", "B", "D"]}))
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        by = {s.symbol: s.bullish_score for s in ranked}
        assert by["C"] == 10 and by["D"] == 1
        assert by["A"] > by["E"] > by["B"]

    async def test_symbols_missing_from_ranking_keep_stage1_score(self):
        signals = [_sig("A", 7), _sig("B", 6), _sig("C", 5), _sig("D", 4), _sig("E", 3)]
        llm = _llm(json.dumps({"ranking": ["B", "A", "D", "C"]}))  # E omitted
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        by = {s.symbol: s.bullish_score for s in ranked}
        assert by["E"] == 3  # untouched
        assert by["B"] == 10

    async def test_hallucinated_symbols_ignored(self):
        signals = [_sig(s, 5) for s in "ABCDE"]
        llm = _llm(json.dumps({"ranking": ["A", "ZZZ", "B", "C", "D", "E"]}))
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        by = {s.symbol: s.bullish_score for s in ranked}
        assert by["A"] == 10 and by["E"] == 1 and "ZZZ" not in by

    async def test_llm_error_falls_back_to_stage1(self):
        signals = [_sig(s, 6) for s in "ABCDE"]
        llm = MagicMock()
        llm.invoke_raw = AsyncMock(side_effect=RuntimeError("api down"))
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        assert all(s.bullish_score == 6 for s in ranked)

    async def test_unparseable_response_falls_back(self):
        signals = [_sig(s, 6) for s in "ABCDE"]
        ranker = CrossSectionalRanker(_llm("sorry, I cannot"), analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        assert all(s.bullish_score == 6 for s in ranked)

    async def test_below_min_universe_skips_llm(self):
        signals = [_sig("A", 7), _sig("B", 3)]
        llm = _llm("{}")
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        llm.invoke_raw.assert_not_called()
        assert {s.bullish_score for s in ranked} == {7, 3}

    async def test_duplicate_symbols_in_ranking_use_first_occurrence(self):
        signals = [_sig(s, 5) for s in "ABCDE"]
        llm = _llm(json.dumps({"ranking": ["A", "B", "A", "C", "D", "E"]}))
        ranker = CrossSectionalRanker(llm, analyst_type="news", branch_name="growth")
        ranked = await ranker.rank(signals)
        by = {s.symbol: s.bullish_score for s in ranked}
        assert by["A"] == 10  # first occurrence wins
