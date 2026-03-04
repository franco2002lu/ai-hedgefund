"""Tests for the 6 in-memory repository implementations in state.py."""

from datetime import UTC, datetime

import pytest

from app.common.enums import ExecutionMode, OrderSide, OrderStatus, OrderType, TimeInForce
from app.common.events.base import BaseEvent
from app.common.interfaces.repositories import (
    EventLogRepository,
    OrderRepository,
    PortfolioRepository,
    PositionRepository,
    SnapshotRepository,
    TradeRepository,
)
from app.common.models.order import Order
from app.common.models.position import Position
from app.common.models.trade import Trade
from app.modules.backtest.state import (
    InMemoryEventLogRepository,
    InMemoryOrderRepository,
    InMemoryPortfolioRepository,
    InMemoryPositionRepository,
    InMemorySnapshotRepository,
    InMemoryTradeRepository,
)


class _TestEvent(BaseEvent):
    """Minimal event with branch_id for testing the in-memory event log."""

    branch_id: str


# ---------------------------------------------------------------------------
# InMemoryPortfolioRepository
# ---------------------------------------------------------------------------


class TestInMemoryPortfolioRepository:
    def test_implements_portfolio_repository_abc(self):
        assert isinstance(InMemoryPortfolioRepository(), PortfolioRepository)

    async def test_create_stores_portfolio(self):
        repo = InMemoryPortfolioRepository()
        portfolio = await repo.create(
            branch_id="branch-1",
            branch_type="equities",
            initial_cash=1_000_000.0,
            margin_requirement=0.0,
        )
        assert portfolio.branch_id == "branch-1"
        assert portfolio.cash == 1_000_000.0

    async def test_get_by_branch_returns_created_portfolio(self):
        repo = InMemoryPortfolioRepository()
        await repo.create(
            branch_id="branch-1",
            branch_type="equities",
            initial_cash=1_000_000.0,
            margin_requirement=0.0,
        )
        result = await repo.get_by_branch("branch-1")
        assert result is not None
        assert result.branch_id == "branch-1"

    async def test_get_by_branch_returns_none_for_missing(self):
        repo = InMemoryPortfolioRepository()
        assert await repo.get_by_branch("nonexistent") is None

    async def test_update_cash_modifies_cash(self):
        repo = InMemoryPortfolioRepository()
        await repo.create(
            branch_id="branch-1",
            branch_type="equities",
            initial_cash=1_000_000.0,
            margin_requirement=0.0,
        )
        result = await repo.update_cash("branch-1", -50_000.0, "trade")
        assert result.cash == pytest.approx(950_000.0)

    async def test_update_portfolio_fields_modifies_fields(self):
        """Validates the ABC method added in Section 7.4."""
        repo = InMemoryPortfolioRepository()
        await repo.create(
            branch_id="branch-1",
            branch_type="equities",
            initial_cash=1_000_000.0,
            margin_requirement=0.0,
        )
        result = await repo.update_portfolio_fields(
            "branch-1",
            nav=1_050_000.0,
            unrealized_pnl=50_000.0,
        )
        assert result.nav == pytest.approx(1_050_000.0)
        assert result.unrealized_pnl == pytest.approx(50_000.0)


# ---------------------------------------------------------------------------
# InMemoryPositionRepository
# ---------------------------------------------------------------------------


class TestInMemoryPositionRepository:
    def test_implements_position_repository_abc(self):
        assert isinstance(InMemoryPositionRepository(), PositionRepository)

    async def test_upsert_creates_new_position(self):
        repo = InMemoryPositionRepository()
        pos = Position(
            id="pos-1",
            portfolio_id="portfolio-1",
            instrument_id="inst-1",
            symbol="AAPL",
            long_quantity=100.0,
            long_cost_basis=15_000.0,
            updated_at=datetime.now(UTC),
        )
        result = await repo.upsert(pos)
        assert result.symbol == "AAPL"
        assert result.long_quantity == 100.0

    async def test_upsert_updates_existing_position(self):
        repo = InMemoryPositionRepository()
        pos1 = Position(
            id="pos-1",
            portfolio_id="portfolio-1",
            instrument_id="inst-1",
            symbol="AAPL",
            long_quantity=100.0,
            long_cost_basis=15_000.0,
            updated_at=datetime.now(UTC),
        )
        await repo.upsert(pos1)

        pos2 = Position(
            id="pos-1",
            portfolio_id="portfolio-1",
            instrument_id="inst-1",
            symbol="AAPL",
            long_quantity=200.0,
            long_cost_basis=30_000.0,
            updated_at=datetime.now(UTC),
        )
        result = await repo.upsert(pos2)
        assert result.long_quantity == 200.0

    async def test_get_by_portfolio_returns_all_positions(self):
        repo = InMemoryPositionRepository()
        for symbol in ["AAPL", "MSFT", "GOOGL"]:
            await repo.upsert(
                Position(
                    id=f"pos-{symbol}",
                    portfolio_id="portfolio-1",
                    instrument_id=f"inst-{symbol}",
                    symbol=symbol,
                    long_quantity=100.0,
                    long_cost_basis=10_000.0,
                    updated_at=datetime.now(UTC),
                )
            )
        positions = await repo.get_by_portfolio("portfolio-1")
        assert len(positions) == 3

    async def test_get_by_symbol_returns_correct_position(self):
        repo = InMemoryPositionRepository()
        await repo.upsert(
            Position(
                id="pos-1",
                portfolio_id="portfolio-1",
                instrument_id="inst-1",
                symbol="AAPL",
                long_quantity=100.0,
                long_cost_basis=15_000.0,
                updated_at=datetime.now(UTC),
            )
        )
        result = await repo.get_by_symbol("portfolio-1", "AAPL")
        assert result is not None
        assert result.symbol == "AAPL"

    async def test_delete_if_flat_removes_zero_position(self):
        repo = InMemoryPositionRepository()
        await repo.upsert(
            Position(
                id="pos-1",
                portfolio_id="portfolio-1",
                instrument_id="inst-1",
                symbol="AAPL",
                long_quantity=0.0,
                long_cost_basis=0.0,
                short_quantity=0.0,
                updated_at=datetime.now(UTC),
            )
        )
        deleted = await repo.delete_if_flat("portfolio-1", "inst-1")
        assert deleted is True
        positions = await repo.get_by_portfolio("portfolio-1")
        assert len(positions) == 0


# ---------------------------------------------------------------------------
# InMemorySnapshotRepository
# ---------------------------------------------------------------------------


class TestInMemorySnapshotRepository:
    def test_implements_snapshot_repository_abc(self):
        assert isinstance(InMemorySnapshotRepository(), SnapshotRepository)

    async def test_create_stores_snapshot(self):
        repo = InMemorySnapshotRepository()
        snapshot = await repo.create("portfolio-1", "branch-1")
        assert snapshot.portfolio_id == "portfolio-1"
        assert snapshot.branch_id == "branch-1"

    async def test_list_by_branch_returns_stored_snapshots(self):
        repo = InMemorySnapshotRepository()
        await repo.create("portfolio-1", "branch-1")
        await repo.create("portfolio-1", "branch-1")

        snapshots, total = await repo.list_by_branch("branch-1")
        assert total == 2
        assert len(snapshots) == 2

    async def test_list_by_branch_paginates(self):
        repo = InMemorySnapshotRepository()
        for _ in range(5):
            await repo.create("portfolio-1", "branch-1")

        snapshots, total = await repo.list_by_branch("branch-1", limit=2, offset=0)
        assert total == 5
        assert len(snapshots) == 2


# ---------------------------------------------------------------------------
# InMemoryOrderRepository
# ---------------------------------------------------------------------------


class TestInMemoryOrderRepository:
    def test_implements_order_repository_abc(self):
        assert isinstance(InMemoryOrderRepository(), OrderRepository)

    async def test_create_stores_order(self):
        repo = InMemoryOrderRepository()
        order = Order(
            id="order-1",
            branch_id="branch-1",
            instrument_id="inst-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            time_in_force=TimeInForce.DAY,
            status=OrderStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        result = await repo.create(order)
        assert result.id == "order-1"

    async def test_get_by_id_returns_correct_order(self):
        repo = InMemoryOrderRepository()
        order = Order(
            id="order-1",
            branch_id="branch-1",
            instrument_id="inst-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            time_in_force=TimeInForce.DAY,
            status=OrderStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await repo.create(order)

        result = await repo.get_by_id("order-1")
        assert result is not None
        assert result.symbol == "AAPL"

    async def test_get_by_id_returns_none_for_missing(self):
        repo = InMemoryOrderRepository()
        assert await repo.get_by_id("nonexistent") is None

    async def test_update_status_changes_status(self):
        repo = InMemoryOrderRepository()
        order = Order(
            id="order-1",
            branch_id="branch-1",
            instrument_id="inst-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            time_in_force=TimeInForce.DAY,
            status=OrderStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await repo.create(order)

        result = await repo.update_status("order-1", OrderStatus.FILLED)
        assert result.status == OrderStatus.FILLED

    async def test_list_orders_filters_by_branch(self):
        repo = InMemoryOrderRepository()
        for i, branch in enumerate(["branch-1", "branch-1", "branch-2"]):
            await repo.create(
                Order(
                    id=f"order-{i}",
                    branch_id=branch,
                    instrument_id="inst-1",
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=100.0,
                    time_in_force=TimeInForce.DAY,
                    status=OrderStatus.PENDING,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )

        orders, total = await repo.list_orders(branch_id="branch-1")
        assert total == 2


# ---------------------------------------------------------------------------
# InMemoryTradeRepository
# ---------------------------------------------------------------------------


class TestInMemoryTradeRepository:
    def test_implements_trade_repository_abc(self):
        assert isinstance(InMemoryTradeRepository(), TradeRepository)

    async def test_create_stores_trade(self):
        repo = InMemoryTradeRepository()
        trade = Trade(
            id="trade-1",
            order_id="order-1",
            branch_id="branch-1",
            instrument_id="inst-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=100.0,
            price=150.0,
            commission=1.50,
            slippage=0.075,
            execution_mode=ExecutionMode.PAPER,
            executed_at=datetime.now(UTC),
        )
        result = await repo.create(trade)
        assert result.id == "trade-1"

    async def test_get_by_id_returns_correct_trade(self):
        repo = InMemoryTradeRepository()
        trade = Trade(
            id="trade-1",
            order_id="order-1",
            branch_id="branch-1",
            instrument_id="inst-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=100.0,
            price=150.0,
            execution_mode=ExecutionMode.PAPER,
            executed_at=datetime.now(UTC),
        )
        await repo.create(trade)

        result = await repo.get_by_id("trade-1")
        assert result is not None
        assert result.symbol == "AAPL"

    async def test_list_trades_paginates(self):
        repo = InMemoryTradeRepository()
        for i in range(5):
            await repo.create(
                Trade(
                    id=f"trade-{i}",
                    order_id=f"order-{i}",
                    branch_id="branch-1",
                    instrument_id="inst-1",
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=100.0,
                    price=150.0,
                    execution_mode=ExecutionMode.PAPER,
                    executed_at=datetime.now(UTC),
                )
            )

        trades, total = await repo.list_trades(branch_id="branch-1", limit=2, offset=0)
        assert total == 5
        assert len(trades) == 2

    async def test_all_trades_property_returns_all(self):
        """all_trades property is used by analytics for post-run analysis."""
        repo = InMemoryTradeRepository()
        for i in range(3):
            await repo.create(
                Trade(
                    id=f"trade-{i}",
                    order_id=f"order-{i}",
                    branch_id="branch-1",
                    instrument_id="inst-1",
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=100.0,
                    price=150.0,
                    execution_mode=ExecutionMode.PAPER,
                    executed_at=datetime.now(UTC),
                )
            )

        assert len(repo.all_trades) == 3


# ---------------------------------------------------------------------------
# InMemoryEventLogRepository
# ---------------------------------------------------------------------------


class TestInMemoryEventLogRepository:
    def test_implements_event_log_repository_abc(self):
        assert isinstance(InMemoryEventLogRepository(), EventLogRepository)

    async def test_append_stores_event(self):
        repo = InMemoryEventLogRepository()
        event = _TestEvent(
            event_type="test_event",
            source="test",
            branch_id="branch-1",
        )
        await repo.append(event)

        events = await repo.query(event_type="test_event")
        assert len(events) == 1

    async def test_query_filters_by_event_type(self):
        repo = InMemoryEventLogRepository()
        await repo.append(_TestEvent(event_type="trade", source="test", branch_id="b1"))
        await repo.append(_TestEvent(event_type="portfolio_update", source="test", branch_id="b1"))
        await repo.append(_TestEvent(event_type="trade", source="test", branch_id="b1"))

        trades = await repo.query(event_type="trade")
        assert len(trades) == 2

    async def test_query_filters_by_branch(self):
        repo = InMemoryEventLogRepository()
        await repo.append(_TestEvent(event_type="trade", source="test", branch_id="b1"))
        await repo.append(_TestEvent(event_type="trade", source="test", branch_id="b2"))

        events = await repo.query(branch_id="b1")
        assert len(events) == 1

    async def test_query_paginates(self):
        repo = InMemoryEventLogRepository()
        for _ in range(10):
            await repo.append(_TestEvent(event_type="trade", source="test", branch_id="b1"))

        events = await repo.query(limit=3, offset=0)
        assert len(events) == 3
