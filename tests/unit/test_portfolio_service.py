"""Unit tests for PortfolioService business logic.

Also holds Postgres repository statement-shape tests (compiled-SQL assertions
on `PostgresPortfolioRepository`/`PostgresSnapshotRepository` query builders).
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.enums import ExecutionMode, OrderSide
from app.common.models.portfolio import PortfolioSummary
from app.common.models.position import Position
from app.common.models.trade import Trade
from app.modules.portfolio.repository import PostgresPortfolioRepository, PostgresSnapshotRepository
from app.modules.portfolio.service import PortfolioService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_portfolio(**overrides) -> PortfolioSummary:
    defaults = dict(
        id="port-1",
        branch_id="branch-1",
        branch_type="equities",
        cash=100_000.0,
        allocated_capital=0.0,
        margin_requirement=0.5,
        margin_used=0.0,
        nav=100_000.0,
        total_long_exposure=0.0,
        total_short_exposure=0.0,
        gross_exposure=0.0,
        net_exposure=0.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        positions=[],
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return PortfolioSummary(**defaults)


def _make_position(**overrides) -> Position:
    defaults = dict(
        id="pos-1",
        portfolio_id="port-1",
        instrument_id="inst-1",
        symbol="AAPL",
        long_quantity=0.0,
        long_cost_basis=0.0,
        short_quantity=0.0,
        short_cost_basis=0.0,
        short_margin_used=0.0,
        realized_pnl_long=0.0,
        realized_pnl_short=0.0,
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Position(**defaults)


def _make_trade(side: OrderSide = OrderSide.BUY, **overrides) -> Trade:
    defaults = dict(
        id="trade-1",
        order_id="order-1",
        branch_id="branch-1",
        instrument_id="inst-1",
        symbol="AAPL",
        side=side,
        quantity=10.0,
        price=150.0,
        commission=0.0,
        slippage=0.0,
        execution_mode=ExecutionMode.PAPER,
        executed_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Trade(**defaults)


@pytest.fixture
def mocks():
    portfolio_repo = AsyncMock()
    position_repo = AsyncMock()
    snapshot_repo = AsyncMock()
    event_log = AsyncMock()
    return portfolio_repo, position_repo, snapshot_repo, event_log


@pytest.fixture
def service(mocks):
    portfolio_repo, position_repo, snapshot_repo, event_log = mocks
    return PortfolioService(
        portfolio_repo=portfolio_repo,
        position_repo=position_repo,
        snapshot_repo=snapshot_repo,
        event_log=event_log,
    )


# ---------------------------------------------------------------------------
# handle_trade_executed — BUY
# ---------------------------------------------------------------------------


class TestHandleBuy:
    async def test_buy_new_position(self, service, mocks):
        portfolio_repo, position_repo, _, event_log = mocks
        portfolio = _make_portfolio()
        portfolio_repo.get_by_branch.return_value = portfolio
        position_repo.get_by_symbol.return_value = None  # No existing position
        position_repo.upsert.side_effect = lambda p: p
        position_repo.get_by_portfolio.return_value = []

        trade = _make_trade(OrderSide.BUY, quantity=10, price=150.0, commission=1.0)
        await service.handle_trade_executed(trade)

        # Position should have been upserted
        upserted = position_repo.upsert.call_args[0][0]
        assert upserted.long_quantity == 10.0
        assert upserted.long_cost_basis == 1501.0  # 150*10 + 1 commission

        # Cash should decrease by trade_cost
        update_call = portfolio_repo.update_portfolio_fields.call_args
        assert update_call.kwargs["cash"] == 100_000.0 - 1501.0

        # Event logged
        event_log.append.assert_called_once()
        event = event_log.append.call_args[0][0]
        assert event.trigger == "trade_executed"

    async def test_buy_adds_to_existing_position(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio()
        existing = _make_position(long_quantity=5.0, long_cost_basis=750.0)
        position_repo.get_by_symbol.return_value = existing
        position_repo.upsert.side_effect = lambda p: p
        position_repo.get_by_portfolio.return_value = []

        trade = _make_trade(OrderSide.BUY, quantity=10, price=150.0, commission=0.0)
        await service.handle_trade_executed(trade)

        upserted = position_repo.upsert.call_args[0][0]
        assert upserted.long_quantity == 15.0  # 5 + 10
        assert upserted.long_cost_basis == 2250.0  # 750 + 150*10


# ---------------------------------------------------------------------------
# handle_trade_executed — SELL
# ---------------------------------------------------------------------------


class TestHandleSell:
    async def test_sell_partial_close(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio()
        existing = _make_position(long_quantity=10.0, long_cost_basis=1500.0)  # avg cost = 150
        position_repo.get_by_symbol.return_value = existing
        position_repo.upsert.side_effect = lambda p: p
        position_repo.get_by_portfolio.return_value = []

        trade = _make_trade(OrderSide.SELL, quantity=5, price=160.0, commission=0.0)
        await service.handle_trade_executed(trade)

        upserted = position_repo.upsert.call_args[0][0]
        assert upserted.long_quantity == 5.0
        # Realized P&L: (160 - 150) * 5 = 50
        assert upserted.realized_pnl_long == pytest.approx(50.0)
        # Cost basis reduced: 1500 - 150*5 = 750
        assert upserted.long_cost_basis == pytest.approx(750.0)

        # Cash increases by trade_proceeds = 160*5 - 0 = 800
        update_call = portfolio_repo.update_portfolio_fields.call_args
        assert update_call.kwargs["cash"] == pytest.approx(100_800.0)

    async def test_sell_full_close_deletes_position(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio()
        existing = _make_position(long_quantity=10.0, long_cost_basis=1500.0)
        position_repo.get_by_symbol.return_value = existing
        position_repo.upsert.side_effect = lambda p: p
        position_repo.get_by_portfolio.return_value = []

        trade = _make_trade(OrderSide.SELL, quantity=10, price=150.0, commission=0.0)
        await service.handle_trade_executed(trade)

        upserted = position_repo.upsert.call_args[0][0]
        assert upserted.long_quantity == 0.0
        position_repo.delete_if_flat.assert_called_once_with("port-1", "inst-1")

    async def test_sell_insufficient_position_raises(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio()
        existing = _make_position(long_quantity=5.0, long_cost_basis=750.0)
        position_repo.get_by_symbol.return_value = existing

        trade = _make_trade(OrderSide.SELL, quantity=20, price=150.0)
        with pytest.raises(ValueError, match="Cannot sell"):
            await service.handle_trade_executed(trade)

    async def test_sell_with_commission_reduces_pnl(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio()
        existing = _make_position(long_quantity=10.0, long_cost_basis=1500.0)
        position_repo.get_by_symbol.return_value = existing
        position_repo.upsert.side_effect = lambda p: p
        position_repo.get_by_portfolio.return_value = []

        trade = _make_trade(OrderSide.SELL, quantity=5, price=160.0, commission=10.0)
        await service.handle_trade_executed(trade)

        upserted = position_repo.upsert.call_args[0][0]
        # Realized P&L: (160 - 150) * 5 - 10 = 40
        assert upserted.realized_pnl_long == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# handle_trade_executed — SHORT
# ---------------------------------------------------------------------------


class TestHandleShort:
    async def test_short_opens_position(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio = _make_portfolio(margin_requirement=0.5)
        portfolio_repo.get_by_branch.return_value = portfolio
        position_repo.get_by_symbol.return_value = None
        position_repo.upsert.side_effect = lambda p: p
        position_repo.get_by_portfolio.return_value = []

        trade = _make_trade(OrderSide.SHORT, quantity=10, price=200.0, commission=0.0)
        await service.handle_trade_executed(trade)

        upserted = position_repo.upsert.call_args[0][0]
        assert upserted.short_quantity == 10.0
        assert upserted.short_cost_basis == 2000.0  # proceeds = 200*10
        assert upserted.short_margin_used == 1000.0  # 200*10*0.5

        # Cash increases by proceeds
        update_call = portfolio_repo.update_portfolio_fields.call_args
        assert update_call.kwargs["cash"] == pytest.approx(102_000.0)


# ---------------------------------------------------------------------------
# handle_trade_executed — COVER
# ---------------------------------------------------------------------------


class TestHandleCover:
    async def test_cover_closes_short(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio = _make_portfolio(margin_requirement=0.5)
        portfolio_repo.get_by_branch.return_value = portfolio
        existing = _make_position(
            short_quantity=10.0,
            short_cost_basis=2000.0,  # avg entry = 200
            short_margin_used=1000.0,
        )
        position_repo.get_by_symbol.return_value = existing
        position_repo.upsert.side_effect = lambda p: p
        position_repo.get_by_portfolio.return_value = []

        trade = _make_trade(OrderSide.COVER, quantity=5, price=190.0, commission=0.0)
        await service.handle_trade_executed(trade)

        upserted = position_repo.upsert.call_args[0][0]
        assert upserted.short_quantity == 5.0
        # Realized P&L: (200 - 190) * 5 = 50
        assert upserted.realized_pnl_short == pytest.approx(50.0)
        # Margin released: 1000 * (5/10) = 500
        assert upserted.short_margin_used == pytest.approx(500.0)

    async def test_cover_insufficient_short_raises(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio()
        existing = _make_position(short_quantity=3.0, short_cost_basis=600.0)
        position_repo.get_by_symbol.return_value = existing

        trade = _make_trade(OrderSide.COVER, quantity=10, price=190.0)
        with pytest.raises(ValueError, match="Cannot cover"):
            await service.handle_trade_executed(trade)


# ---------------------------------------------------------------------------
# handle_trade_executed — edge cases
# ---------------------------------------------------------------------------


class TestHandleTradeEdgeCases:
    async def test_no_portfolio_raises(self, service, mocks):
        portfolio_repo, _, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = None

        trade = _make_trade(OrderSide.BUY)
        with pytest.raises(ValueError, match="No portfolio"):
            await service.handle_trade_executed(trade)

    async def test_portfolio_aggregates_recalculated(self, service, mocks):
        """After a buy, the portfolio fields should include the new position's exposure."""
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio()
        position_repo.get_by_symbol.return_value = None
        position_repo.upsert.side_effect = lambda p: p

        # After upsert, get_by_portfolio returns the updated position
        new_pos = _make_position(long_quantity=10.0, long_cost_basis=1500.0)
        position_repo.get_by_portfolio.return_value = [new_pos]

        trade = _make_trade(OrderSide.BUY, quantity=10, price=150.0, commission=0.0)
        await service.handle_trade_executed(trade)

        update_call = portfolio_repo.update_portfolio_fields.call_args
        assert update_call.kwargs["total_long_exposure"] == 1500.0
        assert update_call.kwargs["nav"] == pytest.approx(100_000.0 - 1500.0 + 1500.0)

    async def test_trade_resets_unrealized_pnl_to_zero(self, service, mocks):
        """A trade reverts exposures/NAV to cost basis, so a stale marked
        unrealized_pnl must be reset alongside them (consistent until next mark)."""
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio(unrealized_pnl=2_500.0)
        position_repo.get_by_symbol.return_value = None
        position_repo.upsert.side_effect = lambda p: p
        position_repo.get_by_portfolio.return_value = []

        trade = _make_trade(OrderSide.BUY, quantity=10, price=150.0, commission=0.0)
        await service.handle_trade_executed(trade)

        update_call = portfolio_repo.update_portfolio_fields.call_args
        assert update_call.kwargs["unrealized_pnl"] == 0.0


# ---------------------------------------------------------------------------
# handle_trade_executed — portfolio-level realized P&L accumulation
# ---------------------------------------------------------------------------


class TestRealizedPnlAccumulation:
    """portfolios.realized_pnl is ledger-lifetime, not a sum over surviving rows.

    delete_if_flat removes fully-closed positions, so recomputing realized from
    position rows silently drops their P&L (prod: SCHW closure 2026-06-22 lost
    +951.58 on the value branch). The portfolio-level figure must accumulate
    incrementally, mirroring the cash update.
    """

    async def test_full_close_preserves_realized_at_portfolio_level(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio(realized_pnl=500.0)
        existing = _make_position(long_quantity=10.0, long_cost_basis=1500.0)
        position_repo.get_by_symbol.return_value = existing
        position_repo.upsert.side_effect = lambda p: p
        # Position fully closed and deleted — no surviving rows to sum over.
        position_repo.get_by_portfolio.return_value = []

        trade = _make_trade(OrderSide.SELL, quantity=10, price=160.0, commission=0.0)
        await service.handle_trade_executed(trade)

        update_call = portfolio_repo.update_portfolio_fields.call_args
        # (160 - 150) * 10 = +100 realized on the closing trade, on top of 500.
        assert update_call.kwargs["realized_pnl"] == pytest.approx(600.0)

    async def test_partial_sell_accumulates_from_portfolio_not_rows(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio(realized_pnl=500.0)
        existing = _make_position(long_quantity=10.0, long_cost_basis=1500.0)
        position_repo.get_by_symbol.return_value = existing
        position_repo.upsert.side_effect = lambda p: p
        # Surviving rows carry an unrelated per-row figure; it must NOT be the
        # source of the portfolio-level number.
        stale_row = _make_position(id="pos-2", instrument_id="inst-2", symbol="OTHER", realized_pnl_long=999.0)
        position_repo.get_by_portfolio.return_value = [existing, stale_row]

        trade = _make_trade(OrderSide.SELL, quantity=5, price=160.0, commission=0.0)
        await service.handle_trade_executed(trade)

        update_call = portfolio_repo.update_portfolio_fields.call_args
        # (160 - 150) * 5 = +50 on top of 500 — not 50 + 999 from the rows.
        assert update_call.kwargs["realized_pnl"] == pytest.approx(550.0)

    async def test_buy_leaves_realized_unchanged(self, service, mocks):
        portfolio_repo, position_repo, _, _ = mocks
        portfolio_repo.get_by_branch.return_value = _make_portfolio(realized_pnl=500.0)
        position_repo.get_by_symbol.return_value = None
        position_repo.upsert.side_effect = lambda p: p
        position_repo.get_by_portfolio.return_value = []

        trade = _make_trade(OrderSide.BUY, quantity=10, price=150.0, commission=0.0)
        await service.handle_trade_executed(trade)

        update_call = portfolio_repo.update_portfolio_fields.call_args
        assert update_call.kwargs["realized_pnl"] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# adjust_cash
# ---------------------------------------------------------------------------


class TestAdjustCash:
    async def test_positive_adjustment(self, service, mocks):
        portfolio_repo, _, _, event_log = mocks
        updated = _make_portfolio(cash=105_000.0, nav=105_000.0)
        portfolio_repo.update_cash.return_value = updated

        result = await service.adjust_cash("branch-1", 5000.0, "funding")
        assert result.cash == 105_000.0
        event_log.append.assert_called_once()

    async def test_overdraft_propagates_repo_error(self, service, mocks):
        portfolio_repo, _, _, _ = mocks
        portfolio_repo.update_cash.side_effect = ValueError("Insufficient cash")

        with pytest.raises(ValueError, match="Insufficient cash"):
            await service.adjust_cash("branch-1", -200_000.0, "withdrawal")


# ---------------------------------------------------------------------------
# PostgresPortfolioRepository.update_cash — NAV recomputation formula
# ---------------------------------------------------------------------------


def _fake_portfolio_row(**overrides):
    """Bare ORM-row stand-in with the numeric fields update_cash touches."""
    defaults = dict(
        id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        cash=10_000.0,
        margin_requirement=0.0,
        margin_used=0.0,
        nav=0.0,
        total_long_exposure=16_500.0,
        total_short_exposure=1_000.0,
        unrealized_pnl=1_500.0,
        realized_pnl=0.0,
        updated_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestUpdateCashNavFormula:
    async def test_nav_excludes_unrealized_pnl(self):
        """total_long_exposure is market-valued after a mark_to_market, so NAV
        must not add unrealized_pnl on top — that would double-count it."""
        row = _fake_portfolio_row()
        session = MagicMock()
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = row
        session.execute = AsyncMock(return_value=exec_result)
        session.flush = AsyncMock()
        session.get = AsyncMock(return_value=None)

        repo = PostgresPortfolioRepository(session)
        summary = await repo.update_cash(str(row.branch_id), 500.0, "funding")

        # new_cash 10_500 + long 16_500 - short 1_000 = 26_000 (unrealized 1_500 NOT added)
        assert row.nav == pytest.approx(26_000.0)
        assert summary.nav == pytest.approx(26_000.0)
        assert summary.cash == pytest.approx(10_500.0)


# ---------------------------------------------------------------------------
# PostgresSnapshotRepository.latest_by_branch — filter/order/limit shape
# ---------------------------------------------------------------------------


def _fake_snapshot_row(**overrides):
    """Bare ORM-row stand-in with the fields PostgresSnapshotRepository._to_domain touches."""
    defaults = dict(
        id=uuid.uuid4(),
        portfolio_id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        cash=10_000.0,
        nav=10_500.0,
        total_long_exposure=500.0,
        total_short_exposure=0.0,
        gross_exposure=500.0,
        net_exposure=500.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        margin_used=0.0,
        position_count=1,
        positions_detail=None,
        snapshot_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestSnapshotLatestByBranch:
    async def test_with_before_filters_orders_and_limits(self):
        """latest_by_branch must filter by branch_id AND snapshot_at < before,
        order by snapshot_at desc, and limit to 1 row — and map the row to a
        PortfolioSnapshot domain object."""
        row = _fake_snapshot_row()
        session = MagicMock()
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = row
        session.execute = AsyncMock(return_value=exec_result)

        repo = PostgresSnapshotRepository(session)
        before = datetime.now(UTC)
        result = await repo.latest_by_branch(str(row.branch_id), before=before)

        stmt = session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "portfolio_snapshots.branch_id = " in sql
        assert "portfolio_snapshots.snapshot_at < " in sql
        assert "ORDER BY portfolio_snapshots.snapshot_at DESC" in sql
        assert "LIMIT" in sql

        assert result is not None
        assert result.id == str(row.id)
        assert result.branch_id == str(row.branch_id)
        assert result.nav == pytest.approx(10_500.0)

    async def test_no_row_returns_none(self):
        session = MagicMock()
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=exec_result)

        repo = PostgresSnapshotRepository(session)
        result = await repo.latest_by_branch("branch-x")
        assert result is None
