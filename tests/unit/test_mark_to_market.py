"""Tests for PortfolioService.mark_to_market."""

from datetime import UTC, datetime

import pytest

from app.common.models.portfolio import PortfolioSummary
from app.common.models.position import Position
from app.modules.portfolio.service import PortfolioService


def _summary(**over):
    base = dict(
        id="pf-1",
        branch_id="b-1",
        branch_type="equities",
        cash=10_000.0,
        allocated_capital=1_000_000.0,
        margin_requirement=0.0,
        margin_used=0.0,
        nav=110_000.0,
        total_long_exposure=100_000.0,
        total_short_exposure=0.0,
        gross_exposure=100_000.0,
        net_exposure=100_000.0,
        unrealized_pnl=0.0,
        realized_pnl=500.0,
        updated_at=datetime.now(UTC),
    )
    base.update(over)
    return PortfolioSummary(**base)


def _position(symbol, qty, cost):
    return Position(
        id=f"pos-{symbol}",
        portfolio_id="pf-1",
        instrument_id=f"in-{symbol}",
        symbol=symbol,
        long_quantity=qty,
        long_cost_basis=cost,
        short_quantity=0.0,
        short_cost_basis=0.0,
        short_margin_used=0.0,
        realized_pnl_long=0.0,
        realized_pnl_short=0.0,
        updated_at=datetime.now(UTC),
    )


class FakePortfolioRepo:
    def __init__(self, summary):
        self.summary = summary
        self.updated_fields = None

    async def get_by_branch(self, branch_id):
        return self.summary

    async def update_portfolio_fields(self, branch_id, **fields):
        self.updated_fields = fields


class FakePositionRepo:
    def __init__(self, positions):
        self.positions = positions

    async def get_by_portfolio(self, portfolio_id):
        return self.positions


class FakeEventLog:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


def _service(summary, positions):
    return PortfolioService(
        portfolio_repo=FakePortfolioRepo(summary),
        position_repo=FakePositionRepo(positions),
        snapshot_repo=None,
        event_log=FakeEventLog(),
    )


async def test_mark_to_market_values_positions_at_price():
    svc = _service(_summary(), [_position("AAA", 100, 10_000.0), _position("BBB", 50, 5_000.0)])
    result = await svc.mark_to_market("b-1", {"AAA": 120.0, "BBB": 90.0})
    # AAA mv=12000, BBB mv=4500 → total 16500; nav = 10000 + 16500
    assert result.nav == pytest.approx(26_500.0)
    assert result.unrealized_pnl == pytest.approx(16_500.0 - 15_000.0)
    assert result.priced == 2 and result.unpriced == 0
    fields = svc.portfolio_repo.updated_fields
    assert fields["nav"] == pytest.approx(26_500.0)
    assert fields["total_long_exposure"] == pytest.approx(16_500.0)
    assert fields["unrealized_pnl"] == pytest.approx(1_500.0)


async def test_mark_to_market_unpriced_position_keeps_cost_basis():
    svc = _service(_summary(), [_position("AAA", 100, 10_000.0), _position("BAD", 10, 2_000.0)])
    result = await svc.mark_to_market("b-1", {"AAA": 120.0, "BAD": None})
    # BAD carried at cost 2000 → mv total 14000
    assert result.nav == pytest.approx(10_000.0 + 14_000.0)
    assert result.unpriced == 1
    bad = next(d for d in result.positions_detail if d["symbol"] == "BAD")
    assert bad["market_value"] == pytest.approx(2_000.0)
    assert bad["price"] is None


async def test_mark_to_market_detail_sorted_and_weighted():
    svc = _service(_summary(), [_position("AAA", 100, 10_000.0), _position("BBB", 50, 5_000.0)])
    result = await svc.mark_to_market("b-1", {"AAA": 120.0, "BBB": 400.0})
    # BBB mv=20000 > AAA mv=12000; nav = 10000+32000 = 42000
    assert [d["symbol"] for d in result.positions_detail] == ["BBB", "AAA"]
    assert result.positions_detail[0]["weight"] == pytest.approx(20_000.0 / 42_000.0)


async def test_mark_to_market_equal_market_values_sorted_by_symbol():
    # ZZZ inserted first; both mv=10_000 → tie must break alphabetically, not by
    # insertion order (the positions query has no ORDER BY).
    svc = _service(_summary(), [_position("ZZZ", 100, 9_000.0), _position("AAA", 50, 9_500.0)])
    result = await svc.mark_to_market("b-1", {"ZZZ": 100.0, "AAA": 200.0})
    assert [d["symbol"] for d in result.positions_detail] == ["AAA", "ZZZ"]


async def test_mark_to_market_empty_portfolio():
    svc = _service(_summary(), [])
    result = await svc.mark_to_market("b-1", {})
    assert result.nav == pytest.approx(10_000.0)  # cash only
    assert result.positions_detail == []
    assert result.priced == 0 and result.unpriced == 0
    fields = svc.portfolio_repo.updated_fields
    assert fields["total_long_exposure"] == pytest.approx(0.0)
    assert fields["unrealized_pnl"] == pytest.approx(0.0)


@pytest.mark.parametrize("bad_price", [0.0, -5.0])
async def test_mark_to_market_nonpositive_price_treated_as_unpriced(bad_price):
    svc = _service(_summary(), [_position("BAD", 10, 2_000.0)])
    result = await svc.mark_to_market("b-1", {"BAD": bad_price})
    assert result.priced == 0 and result.unpriced == 1
    detail = result.positions_detail[0]
    assert detail["price"] is None
    assert detail["market_value"] == pytest.approx(2_000.0)  # carried at cost


@pytest.mark.parametrize(("cash", "expected_nav"), [(-20_000.0, -15_000.0), (-5_000.0, 0.0)])
async def test_mark_to_market_nonpositive_nav_zeroes_weights(cash, expected_nav):
    # One position mv = 50 * 100 = 5_000; deeply negative cash drags nav ≤ 0.
    svc = _service(_summary(cash=cash), [_position("AAA", 50, 6_000.0)])
    result = await svc.mark_to_market("b-1", {"AAA": 100.0})
    assert result.nav == pytest.approx(expected_nav)
    assert all(d["weight"] == 0.0 for d in result.positions_detail)


async def test_mark_to_market_logs_event_with_trigger():
    svc = _service(_summary(), [_position("AAA", 100, 10_000.0)])
    result = await svc.mark_to_market("b-1", {"AAA": 100.0})
    events = svc.event_log.events
    assert len(events) == 1
    assert events[0].trigger == "mark_to_market"
    assert events[0].nav == pytest.approx(result.nav)
    assert events[0].unrealized_pnl == pytest.approx(result.unrealized_pnl)


async def test_mark_to_market_no_portfolio_raises():
    svc = PortfolioService(
        portfolio_repo=FakePortfolioRepo(None),
        position_repo=FakePositionRepo([]),
        snapshot_repo=None,
        event_log=FakeEventLog(),
    )
    with pytest.raises(ValueError):
        await svc.mark_to_market("b-x", {})
