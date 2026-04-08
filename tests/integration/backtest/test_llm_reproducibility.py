"""End-to-end reproducibility test for LLM-mode backtests.

Cost-gated: requires ANTHROPIC_API_KEY. Expected cost: ~$0.50 per run.

Verifies that running the same LLM-mode backtest twice with the same arguments
produces bit-identical outputs because the persistent response cache covers
every LLM call on the second run.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from app.modules.backtest.config import BacktestConfig, LLMBacktestConfig
from app.modules.backtest.engine import BacktestEngine

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Requires ANTHROPIC_API_KEY for live LLM calls",
)
@pytest.mark.asyncio
async def test_llm_backtest_is_reproducible_via_cache(tmp_path: Path) -> None:
    """Two identical --llm runs must produce bit-identical metrics on the second run."""
    cache_path = tmp_path / "llm_response_cache.db"

    config = BacktestConfig(
        start_date=date(2025, 5, 31),
        end_date=date(2025, 6, 30),  # 1-month window. Start covers voog_2025-05-31 snapshot
        # so UniverseProvider.get_snapshot_symbols() returns the top-N from the snapshot
        # rather than falling back to growth_universe.csv (which doesn't apply top_n and
        # contains BRK/B — Yahoo's quoteSummary endpoint 500s on the slash in the ticker).
        initial_capital=10_000.0,
        branch_name="growth",
        use_llm_agents=True,
        llm_config=LLMBacktestConfig(cache_signals=True, max_llm_calls_per_rebalance=60),
        top_n=8,  # top-8 growth (NVDA, MSFT, META, AAPL, AVGO, AMZN, GOOGL, TSLA).
        # top-3 was insufficient: NVDA/MSFT/META all get rejected by the growth
        # screener's valuation filters (P/E and PEG ceilings), so the analysts
        # never run and the LLM cache stays empty. With top-8, ~2-3 stocks pass
        # screening per rebalance → ~45 analyst calls × ~$0.012 each ≈ $0.50 total.
        use_llm_response_cache=True,
        llm_response_cache_path=cache_path,
    )

    engine = BacktestEngine()

    # First run — populates the cache
    result_1 = await engine.run(config)
    assert result_1.metrics is not None, f"First run failed: {result_1.error_message}"
    assert result_1.llm_cache_misses > 0, "First run should miss the cache at least once"

    # Second run — should hit 100%
    result_2 = await engine.run(config)
    assert result_2.metrics is not None, f"Second run failed: {result_2.error_message}"
    assert result_2.llm_cache_misses == 0, (
        f"Second run had {result_2.llm_cache_misses} misses; cache should cover every call"
    )
    assert result_2.llm_cache_hits > 0

    # Core metrics must be bit-identical
    m1, m2 = result_1.metrics, result_2.metrics
    assert m1.total_return == m2.total_return
    assert m1.sharpe_ratio == m2.sharpe_ratio
    assert m1.max_drawdown == m2.max_drawdown
    assert len(result_1.trades) == len(result_2.trades)
    assert len(result_1.signals) == len(result_2.signals)
