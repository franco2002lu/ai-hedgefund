"""Cross-sectional ranking — stage 2 of analyst scoring (Phase B).

Stage 1 produces per-stock signals; rankers re-map bullish_score to a forced
decile based on rank across the screened set. DeterministicRanker (sort by
stage-1 score) serves backtests/quant mode; CrossSectionalRanker (LLM call)
is added in a later task for production.
"""

from __future__ import annotations

import logging

from app.modules.equities.models import StockSignal

logger = logging.getLogger(__name__)

DEFAULT_MIN_RANK_UNIVERSE = 5


def decile_score(i: int, n: int) -> int:
    """Map 0-indexed rank position to a 1-10 score, best=10, worst=1."""
    if n <= 1:
        return 10
    return 1 + ((n - 1 - i) * 9) // (n - 1)


def apply_ranking(signals: list[StockSignal], ordered_symbols: list[str]) -> list[StockSignal]:
    """Return new signals with bullish_score = decile of position in ordered_symbols.

    Preserves the input order of `signals`; symbols absent from
    ordered_symbols keep their stage-1 score.
    """
    position = {sym: i for i, sym in enumerate(ordered_symbols)}
    n = len(ordered_symbols)
    out = []
    for sig in signals:
        i = position.get(sig.symbol)
        if i is None:
            out.append(sig)
        else:
            out.append(sig.model_copy(update={"bullish_score": decile_score(i, n)}))
    return out


class DeterministicRanker:
    """Rank-normalize by stage-1 score; ties share the rounded mean decile
    of their positions; alphabetical order is used only for stable iteration.

    Gives quant-mode backtests the same decile score semantics as the LLM
    ranker without any LLM call.
    """

    def __init__(self, min_rank_universe: int = DEFAULT_MIN_RANK_UNIVERSE) -> None:
        self.min_rank_universe = min_rank_universe

    async def rank(self, signals: list[StockSignal]) -> list[StockSignal]:
        if len(signals) < self.min_rank_universe:
            return signals
        ordered = sorted(signals, key=lambda s: (-s.bullish_score, s.symbol))
        n = len(ordered)

        # Group consecutive entries with equal stage-1 score; each group
        # spanning positions i..j shares the rounded mean of its deciles.
        score_by_symbol: dict[str, int] = {}
        i = 0
        while i < n:
            j = i
            while j + 1 < n and ordered[j + 1].bullish_score == ordered[i].bullish_score:
                j += 1
            deciles = [decile_score(k, n) for k in range(i, j + 1)]
            group_score = int(sum(deciles) / len(deciles) + 0.5)
            group_score = max(1, min(10, group_score))
            for k in range(i, j + 1):
                score_by_symbol[ordered[k].symbol] = group_score
            i = j + 1

        return [sig.model_copy(update={"bullish_score": score_by_symbol[sig.symbol]}) for sig in signals]
