"""Unit tests for equities branch Pydantic models."""

import pytest

from app.common.enums import OrderSide
from app.modules.equities.models import (
    CompositeScore,
    RebalanceOrder,
    RunResult,
    UniverseStock,
)
from tests.conftest import _make_composite_score, _make_stock_signal, _make_universe_stock


class TestUniverseStock:
    def test_basic_construction(self):
        stock = _make_universe_stock()
        assert stock.symbol == "AAPL"
        assert stock.company_name == "Apple Inc."
        assert stock.weight == pytest.approx(0.05)
        assert stock.sector == "Technology"
        assert stock.industry == "Consumer Electronics"

    def test_sector_defaults_to_none(self):
        stock = UniverseStock(symbol="AAPL", company_name="Apple Inc.")
        assert stock.sector is None

    def test_industry_defaults_to_none(self):
        stock = UniverseStock(symbol="AAPL", company_name="Apple Inc.")
        assert stock.industry is None

    def test_weight_defaults_to_zero(self):
        stock = UniverseStock(symbol="AAPL", company_name="Apple Inc.")
        assert stock.weight == pytest.approx(0.0)

    def test_custom_values(self):
        stock = _make_universe_stock(symbol="MSFT", company_name="Microsoft", weight=0.08)
        assert stock.symbol == "MSFT"
        assert stock.company_name == "Microsoft"
        assert stock.weight == pytest.approx(0.08)


class TestStockSignal:
    def test_valid_signal(self):
        signal = _make_stock_signal()
        assert signal.symbol == "AAPL"
        assert signal.analyst_type == "news"
        assert signal.bullish_score == 7
        assert signal.confidence == 8
        assert "product launch" in signal.summary

    def test_bullish_score_boundary_min(self):
        signal = _make_stock_signal(bullish_score=1)
        assert signal.bullish_score == 1

    def test_bullish_score_boundary_max(self):
        signal = _make_stock_signal(bullish_score=10)
        assert signal.bullish_score == 10

    def test_bullish_score_zero_raises_value_error(self):
        with pytest.raises(ValueError):
            _make_stock_signal(bullish_score=0)

    def test_bullish_score_eleven_raises_value_error(self):
        with pytest.raises(ValueError):
            _make_stock_signal(bullish_score=11)

    def test_confidence_zero_raises_value_error(self):
        with pytest.raises(ValueError):
            _make_stock_signal(confidence=0)

    def test_confidence_eleven_raises_value_error(self):
        with pytest.raises(ValueError):
            _make_stock_signal(confidence=11)

    def test_invalid_analyst_type_raises_value_error(self):
        with pytest.raises(ValueError):
            _make_stock_signal(analyst_type="invalid_type")

    def test_valid_analyst_types(self):
        for at in ["news", "fundamentals", "technical"]:
            signal = _make_stock_signal(analyst_type=at)
            assert signal.analyst_type == at


class TestRebalanceOrder:
    def test_buy_order(self):
        order = RebalanceOrder(symbol="AAPL", side=OrderSide.BUY, quantity=100, reason="new_position")
        assert order.symbol == "AAPL"
        assert order.side == OrderSide.BUY
        assert order.quantity == 100
        assert order.reason == "new_position"

    def test_sell_order(self):
        order = RebalanceOrder(symbol="MSFT", side=OrderSide.SELL, quantity=50, reason="removed_position")
        assert order.symbol == "MSFT"
        assert order.side == OrderSide.SELL
        assert order.quantity == 50
        assert order.reason == "removed_position"

    def test_string_side_coerced(self):
        order = RebalanceOrder(symbol="AAPL", side="buy", quantity=10, reason="weight_adjustment")
        assert order.side == OrderSide.BUY


class TestCompositeScore:
    def test_construction_with_all_fields(self):
        score = _make_composite_score()
        assert score.symbol == "AAPL"
        assert score.composite_score == pytest.approx(7.0)
        assert score.composite_confidence == pytest.approx(7.5)
        assert score.conviction == pytest.approx(52.5)
        assert score.target_weight == pytest.approx(0.10)

    def test_default_target_weight(self):
        score = CompositeScore(
            symbol="AAPL",
            composite_score=5.0,
            composite_confidence=6.0,
            conviction=30.0,
        )
        assert score.target_weight == pytest.approx(0.0)


class TestRunResult:
    def test_construction_with_all_fields(self):
        signal = _make_stock_signal()
        score = _make_composite_score()
        order = RebalanceOrder(symbol="AAPL", side=OrderSide.BUY, quantity=100, reason="new_position")
        result = RunResult(
            branch_name="equities",
            universe_count=100,
            screened_count=20,
            signals=[signal],
            composite_scores=[score],
            orders=[order],
            trades_executed=1,
        )
        assert result.branch_name == "equities"
        assert result.universe_count == 100
        assert result.screened_count == 20
        assert len(result.signals) == 1
        assert len(result.composite_scores) == 1
        assert len(result.orders) == 1
        assert result.trades_executed == 1

    def test_empty_lists(self):
        result = RunResult(
            branch_name="equities",
            universe_count=0,
            screened_count=0,
            signals=[],
            composite_scores=[],
            orders=[],
            trades_executed=0,
        )
        assert result.signals == []
        assert result.composite_scores == []
        assert result.orders == []
