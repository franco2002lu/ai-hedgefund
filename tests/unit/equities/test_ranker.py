"""Unit tests for the cross-sectional ranking stage."""

from app.modules.equities.agents.ranker import DeterministicRanker, decile_score
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


class TestDeterministicRanker:
    async def test_rank_normalizes_scores_preserving_order(self):
        ranker = DeterministicRanker(min_rank_universe=2)
        signals = [_sig("A", 7), _sig("B", 5), _sig("C", 6)]
        ranked = await ranker.rank(signals)
        by_symbol = {s.symbol: s.bullish_score for s in ranked}
        assert by_symbol == {"A": 10, "C": 5, "B": 1}
        assert all(s.confidence == 5 and s.summary == "t" for s in ranked)

    async def test_ties_broken_by_symbol_for_determinism(self):
        ranker = DeterministicRanker(min_rank_universe=2)
        ranked = await ranker.rank([_sig("B", 5), _sig("A", 5)])
        by = {s.symbol: s.bullish_score for s in ranked}
        assert by == {"A": 10, "B": 1}

    async def test_below_min_universe_passthrough(self):
        ranker = DeterministicRanker(min_rank_universe=5)
        signals = [_sig("A", 7), _sig("B", 3)]
        ranked = await ranker.rank(signals)
        assert {s.bullish_score for s in ranked} == {7, 3}
