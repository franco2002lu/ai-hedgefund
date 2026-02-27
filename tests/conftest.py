"""Shared test fixtures for the equities module."""

from app.modules.equities.models import CompositeScore, StockSignal, UniverseStock


def _make_universe_stock(**overrides) -> UniverseStock:
    defaults = dict(
        symbol="AAPL",
        company_name="Apple Inc.",
        weight=0.05,
        sector="Technology",
        industry="Consumer Electronics",
    )
    defaults.update(overrides)
    return UniverseStock(**defaults)


def _make_stock_signal(**overrides) -> StockSignal:
    defaults = dict(
        symbol="AAPL",
        analyst_type="news",
        bullish_score=7,
        confidence=8,
        summary="Strong positive sentiment from recent product launch.",
    )
    defaults.update(overrides)
    return StockSignal(**defaults)


def _make_composite_score(**overrides) -> CompositeScore:
    defaults = dict(
        symbol="AAPL",
        composite_score=7.0,
        composite_confidence=7.5,
        conviction=52.5,
        target_weight=0.10,
    )
    defaults.update(overrides)
    return CompositeScore(**defaults)
