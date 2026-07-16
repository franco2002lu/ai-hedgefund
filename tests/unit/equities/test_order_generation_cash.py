"""Sells-first ordering and cash-buffer sizing."""

import pytest

from app.common.enums import OrderSide
from app.modules.equities.agents.portfolio_manager import PortfolioManager
from app.modules.equities.config import AgentsConfig, PortfolioConfig
from app.modules.equities.models import CompositeScore


def _pm(**cfg):
    return PortfolioManager(
        agents_config=AgentsConfig(),
        portfolio_config=PortfolioConfig(**cfg),
    )


def _score(symbol, conviction=50.0, target_weight=0.0):
    return CompositeScore(
        symbol=symbol,
        composite_score=7.0,
        composite_confidence=7.0,
        conviction=conviction,
        target_weight=target_weight,
    )


def test_generate_orders_sells_before_buys():
    pm = _pm()
    target = [_score("AAA", target_weight=0.5), _score("ZZZ", target_weight=0.4)]
    current = {"MMM": 0.5, "BBB": 0.4}  # both fully exited
    orders = pm.generate_orders(
        target, current, nav=1_000_000.0, prices={"AAA": 10.0, "ZZZ": 10.0, "MMM": 10.0, "BBB": 10.0}
    )
    sides = [o.side for o in orders]
    first_buy = sides.index(OrderSide.BUY)
    assert all(s == OrderSide.SELL for s in sides[:first_buy])
    assert all(s == OrderSide.BUY for s in sides[first_buy:])
    sells = [o.symbol for o in orders if o.side == OrderSide.SELL]
    assert sells == sorted(sells)


def test_size_positions_targets_sum_to_one_minus_buffer():
    pm = _pm(cash_buffer_pct=0.01)
    sized = pm.size_positions([_score(f"S{i}", conviction=10.0 + i) for i in range(10)])
    assert sum(s.target_weight for s in sized) == pytest.approx(0.99, abs=1e-9)


def test_size_positions_buffer_respects_cap():
    pm = _pm(cash_buffer_pct=0.01, max_position_weight=0.50)
    sized = pm.size_positions([_score("ONLY", conviction=42.0)])
    assert sized[0].target_weight == pytest.approx(0.50 * 0.99)


def test_size_positions_zero_conviction_equal_weight_buffered():
    pm = _pm(cash_buffer_pct=0.01)
    sized = pm.size_positions([_score("A", conviction=0.0), _score("B", conviction=0.0)])
    assert sum(s.target_weight for s in sized) == pytest.approx(0.99, abs=1e-9)


def test_default_cash_buffer_pct_is_one_percent():
    """Pin the default so a silent config change fails a test, not just prod."""
    assert PortfolioConfig().cash_buffer_pct == 0.01
