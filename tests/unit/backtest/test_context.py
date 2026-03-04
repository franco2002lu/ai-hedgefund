"""Tests for BacktestContext factory — verifies DI wiring correctness."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import NAMESPACE_DNS, uuid5

from app.modules.backtest.context import BacktestContext
from app.modules.backtest.time_provider import BacktestTimeProvider
from app.modules.equities.config import EquitiesConfig

from .conftest import _make_backtest_config


class TestBacktestContext:
    @patch("app.modules.backtest.context.HistoricalPriceStore")
    @patch("app.modules.backtest.context.HistoricalDataAdapter")
    @patch("app.modules.backtest.context.DataPlatformService")
    async def test_creates_backtest_time_provider(self, mock_dps, mock_adapter, mock_store):
        """BacktestTimeProvider should be created with start_date."""
        mock_store_instance = AsyncMock()
        mock_store.return_value = mock_store_instance
        mock_store_instance.preload = AsyncMock()
        mock_store_instance.get_trading_days = MagicMock(
            return_value=[date(2024, 1, 2), date(2024, 1, 3)]
        )

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert isinstance(ctx.time_provider, BacktestTimeProvider)
        assert ctx.time_provider.today() == config.start_date

    @patch("app.modules.backtest.context.HistoricalPriceStore")
    @patch("app.modules.backtest.context.HistoricalDataAdapter")
    @patch("app.modules.backtest.context.DataPlatformService")
    async def test_uses_in_memory_repos_not_postgres(self, mock_dps, mock_adapter, mock_store):
        """Context should create in-memory repositories, not Postgres ones."""
        mock_store_instance = AsyncMock()
        mock_store.return_value = mock_store_instance
        mock_store_instance.preload = AsyncMock()
        mock_store_instance.get_trading_days = MagicMock(
            return_value=[date(2024, 1, 2), date(2024, 1, 3)]
        )

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        # Should not have any Postgres repository types
        from app.modules.backtest.state import (
            InMemoryEventLogRepository,
        )
        assert isinstance(ctx.event_log, InMemoryEventLogRepository)

    @patch("app.modules.backtest.context.HistoricalPriceStore")
    @patch("app.modules.backtest.context.HistoricalDataAdapter")
    @patch("app.modules.backtest.context.DataPlatformService")
    async def test_creates_new_equities_service_instance(self, mock_dps, mock_adapter, mock_store):
        """A NEW EquitiesBranchService instance should be created, not the singleton."""
        mock_store_instance = AsyncMock()
        mock_store.return_value = mock_store_instance
        mock_store_instance.preload = AsyncMock()
        mock_store_instance.get_trading_days = MagicMock(
            return_value=[date(2024, 1, 2), date(2024, 1, 3)]
        )

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert ctx.equities_service is not None

    @patch("app.modules.backtest.context.HistoricalPriceStore")
    @patch("app.modules.backtest.context.HistoricalDataAdapter")
    @patch("app.modules.backtest.context.DataPlatformService")
    async def test_trading_days_computed(self, mock_dps, mock_adapter, mock_store):
        """trading_days should be a sorted list of weekdays from the store."""
        expected_days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        mock_store_instance = AsyncMock()
        mock_store.return_value = mock_store_instance
        mock_store_instance.preload = AsyncMock()
        mock_store_instance.get_trading_days = MagicMock(return_value=expected_days)

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert ctx.trading_days == expected_days

    @patch("app.modules.backtest.context.HistoricalPriceStore")
    @patch("app.modules.backtest.context.HistoricalDataAdapter")
    @patch("app.modules.backtest.context.DataPlatformService")
    async def test_rebalance_schedule_includes_first_day(self, mock_dps, mock_adapter, mock_store):
        """First trading day must always be a rebalance day."""
        days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        mock_store_instance = AsyncMock()
        mock_store.return_value = mock_store_instance
        mock_store_instance.preload = AsyncMock()
        mock_store_instance.get_trading_days = MagicMock(return_value=days)

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert days[0] in ctx.rebalance_days

    @patch("app.modules.backtest.context.HistoricalPriceStore")
    @patch("app.modules.backtest.context.HistoricalDataAdapter")
    @patch("app.modules.backtest.context.DataPlatformService")
    async def test_cancelled_event_initialized(self, mock_dps, mock_adapter, mock_store):
        """cancelled asyncio.Event should be initialized and not set."""
        mock_store_instance = AsyncMock()
        mock_store.return_value = mock_store_instance
        mock_store_instance.preload = AsyncMock()
        mock_store_instance.get_trading_days = MagicMock(
            return_value=[date(2024, 1, 2)]
        )

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert isinstance(ctx.cancelled, asyncio.Event)
        assert not ctx.cancelled.is_set()

    @patch("app.modules.backtest.context.HistoricalPriceStore")
    @patch("app.modules.backtest.context.HistoricalDataAdapter")
    @patch("app.modules.backtest.context.DataPlatformService")
    async def test_instrument_ids_deterministic(self, mock_dps, mock_adapter, mock_store):
        """instrument_ids should use uuid5(NAMESPACE_DNS, symbol) for determinism."""
        mock_store_instance = AsyncMock()
        mock_store.return_value = mock_store_instance
        mock_store_instance.preload = AsyncMock()
        mock_store_instance.get_trading_days = MagicMock(
            return_value=[date(2024, 1, 2)]
        )

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        if ctx.instrument_ids:
            for symbol, uuid_str in ctx.instrument_ids.items():
                expected = str(uuid5(NAMESPACE_DNS, symbol))
                assert uuid_str == expected

    @patch("app.modules.backtest.context.HistoricalPriceStore")
    @patch("app.modules.backtest.context.HistoricalDataAdapter")
    @patch("app.modules.backtest.context.DataPlatformService")
    async def test_store_preloaded(self, mock_dps, mock_adapter, mock_store):
        """Store.preload() should be called during context build."""
        mock_store_instance = AsyncMock()
        mock_store.return_value = mock_store_instance
        mock_store_instance.preload = AsyncMock()
        mock_store_instance.get_trading_days = MagicMock(
            return_value=[date(2024, 1, 2)]
        )

        config = _make_backtest_config()
        await BacktestContext.build(config, EquitiesConfig())

        mock_store_instance.preload.assert_called_once()
