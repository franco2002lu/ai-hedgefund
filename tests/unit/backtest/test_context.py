"""Tests for BacktestContext factory — verifies DI wiring correctness."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import NAMESPACE_DNS, uuid5

import pytest

from app.modules.backtest.config import BacktestConfig
from app.modules.backtest.context import BacktestContext
from app.modules.backtest.time_provider import BacktestTimeProvider
from app.modules.equities.config import EquitiesConfig

from .conftest import _make_backtest_config


def _configure_mock_store(mock_store_cls, trading_days=None):
    """Configure the mocked HistoricalPriceStore class to return a properly
    set up instance with all required synchronous and async methods."""
    if trading_days is None:
        trading_days = [date(2024, 1, 2), date(2024, 1, 3)]
    mock_instance = MagicMock()
    mock_store_cls.return_value = mock_instance
    # Async methods
    mock_instance.preload = AsyncMock()
    # Synchronous methods used by survivorship filter
    mock_instance.get_first_trading_date = MagicMock(return_value=None)
    mock_instance.get_data_coverage = MagicMock(return_value=1.0)
    mock_instance.get_trading_days = MagicMock(return_value=trading_days)
    # get_bar and get_latest_close used by HistoricalDataAdapter
    mock_instance.get_bar = MagicMock(return_value=None)
    mock_instance.get_bars = MagicMock(return_value=[])
    mock_instance.get_latest_close = MagicMock(return_value=None)
    return mock_instance


_CONTEXT_PATCHES = [
    "app.modules.backtest.context.DataPlatformService",
    "app.modules.backtest.context.HistoricalDataAdapter",
    "app.modules.backtest.context.HistoricalPriceStore",
    "app.modules.backtest.context.HistoricalFundamentalsStore",
]


def _setup_mocks(mock_dps, mock_adapter, mock_store, mock_fund_store, trading_days=None):
    """Common mock setup for all context tests."""
    mock_instance = _configure_mock_store(mock_store, trading_days)
    mock_fund_store.return_value = AsyncMock()
    mock_fund_store.return_value.preload = AsyncMock()
    return mock_instance


class TestBacktestContext:
    @patch(_CONTEXT_PATCHES[0])
    @patch(_CONTEXT_PATCHES[1])
    @patch(_CONTEXT_PATCHES[2])
    @patch(_CONTEXT_PATCHES[3])
    async def test_creates_backtest_time_provider(
        self, mock_fund_store, mock_store, mock_adapter, mock_dps,
    ):
        """BacktestTimeProvider should be created with start_date."""
        _setup_mocks(mock_dps, mock_adapter, mock_store, mock_fund_store)

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert isinstance(ctx.time_provider, BacktestTimeProvider)
        assert ctx.time_provider.today() == config.start_date

    @patch(_CONTEXT_PATCHES[0])
    @patch(_CONTEXT_PATCHES[1])
    @patch(_CONTEXT_PATCHES[2])
    @patch(_CONTEXT_PATCHES[3])
    async def test_uses_in_memory_repos_not_postgres(
        self, mock_fund_store, mock_store, mock_adapter, mock_dps,
    ):
        """Context should create in-memory repositories, not Postgres ones."""
        _setup_mocks(mock_dps, mock_adapter, mock_store, mock_fund_store)

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        from app.modules.backtest.state import InMemoryEventLogRepository
        assert isinstance(ctx.event_log, InMemoryEventLogRepository)

    @patch(_CONTEXT_PATCHES[0])
    @patch(_CONTEXT_PATCHES[1])
    @patch(_CONTEXT_PATCHES[2])
    @patch(_CONTEXT_PATCHES[3])
    async def test_creates_new_equities_service_instance(
        self, mock_fund_store, mock_store, mock_adapter, mock_dps,
    ):
        """A NEW EquitiesBranchService instance should be created, not the singleton."""
        _setup_mocks(mock_dps, mock_adapter, mock_store, mock_fund_store)

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert ctx.equities_service is not None

    @patch(_CONTEXT_PATCHES[0])
    @patch(_CONTEXT_PATCHES[1])
    @patch(_CONTEXT_PATCHES[2])
    @patch(_CONTEXT_PATCHES[3])
    async def test_trading_days_computed(
        self, mock_fund_store, mock_store, mock_adapter, mock_dps,
    ):
        """trading_days should be a sorted list of weekdays from the store."""
        expected_days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        _setup_mocks(mock_dps, mock_adapter, mock_store, mock_fund_store, trading_days=expected_days)

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert ctx.trading_days == expected_days

    @patch(_CONTEXT_PATCHES[0])
    @patch(_CONTEXT_PATCHES[1])
    @patch(_CONTEXT_PATCHES[2])
    @patch(_CONTEXT_PATCHES[3])
    async def test_rebalance_schedule_includes_first_day(
        self, mock_fund_store, mock_store, mock_adapter, mock_dps,
    ):
        """First trading day must always be a rebalance day."""
        days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        _setup_mocks(mock_dps, mock_adapter, mock_store, mock_fund_store, trading_days=days)

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert days[0] in ctx.rebalance_days

    @patch(_CONTEXT_PATCHES[0])
    @patch(_CONTEXT_PATCHES[1])
    @patch(_CONTEXT_PATCHES[2])
    @patch(_CONTEXT_PATCHES[3])
    async def test_cancelled_event_initialized(
        self, mock_fund_store, mock_store, mock_adapter, mock_dps,
    ):
        """cancelled asyncio.Event should be initialized and not set."""
        _setup_mocks(mock_dps, mock_adapter, mock_store, mock_fund_store,
                     trading_days=[date(2024, 1, 2)])

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert isinstance(ctx.cancelled, asyncio.Event)
        assert not ctx.cancelled.is_set()

    @patch(_CONTEXT_PATCHES[0])
    @patch(_CONTEXT_PATCHES[1])
    @patch(_CONTEXT_PATCHES[2])
    @patch(_CONTEXT_PATCHES[3])
    async def test_instrument_ids_deterministic(
        self, mock_fund_store, mock_store, mock_adapter, mock_dps,
    ):
        """instrument_ids should use uuid5(NAMESPACE_DNS, symbol) for determinism."""
        _setup_mocks(mock_dps, mock_adapter, mock_store, mock_fund_store,
                     trading_days=[date(2024, 1, 2)])

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        if ctx.instrument_ids:
            for symbol, uuid_str in ctx.instrument_ids.items():
                expected = str(uuid5(NAMESPACE_DNS, symbol))
                assert uuid_str == expected

    @patch(_CONTEXT_PATCHES[0])
    @patch(_CONTEXT_PATCHES[1])
    @patch(_CONTEXT_PATCHES[2])
    @patch(_CONTEXT_PATCHES[3])
    async def test_store_preloaded(
        self, mock_fund_store, mock_store, mock_adapter, mock_dps,
    ):
        """Store.preload() should be called during context build."""
        mock_instance = _setup_mocks(mock_dps, mock_adapter, mock_store, mock_fund_store,
                                     trading_days=[date(2024, 1, 2)])

        config = _make_backtest_config()
        await BacktestContext.build(config, EquitiesConfig())

        mock_instance.preload.assert_called_once()

    @patch(_CONTEXT_PATCHES[0])
    @patch(_CONTEXT_PATCHES[1])
    @patch(_CONTEXT_PATCHES[2])
    @patch(_CONTEXT_PATCHES[3])
    async def test_equities_service_has_universe_provider(
        self, mock_fund_store, mock_store, mock_adapter, mock_dps,
    ):
        """EquitiesBranchService should have a universe_provider."""
        _setup_mocks(mock_dps, mock_adapter, mock_store, mock_fund_store,
                     trading_days=[date(2024, 1, 2)])

        config = _make_backtest_config()
        ctx = await BacktestContext.build(config, EquitiesConfig())

        assert ctx.equities_service.universe_provider is not None


class TestBundleResolutionAndCache:
    def test_missing_skills_bundle_raises_value_error(self, tmp_path):
        """If config.skills_bundle is set but the directory doesn't exist, setup raises."""
        config = BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            use_llm_agents=True,
            skills_bundle="nonexistent_bundle",
        )
        with pytest.raises(ValueError, match="Skill bundle not found"):
            asyncio.run(BacktestContext.resolve_skills_bundle(config, bundles_root=tmp_path))

    def test_none_skills_bundle_returns_none(self, tmp_path):
        config = BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            use_llm_agents=True,
            skills_bundle=None,
        )
        path = asyncio.run(BacktestContext.resolve_skills_bundle(config, bundles_root=tmp_path))
        assert path is None

    def test_existing_skills_bundle_returns_path(self, tmp_path):
        bundle_dir = tmp_path / "my_bundle"
        (bundle_dir / "base").mkdir(parents=True)
        (bundle_dir / "base" / "fundamentals.md").write_text("# x")
        (bundle_dir / "output_format.md").write_text("## out")

        config = BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            use_llm_agents=True,
            skills_bundle="my_bundle",
        )
        path = asyncio.run(BacktestContext.resolve_skills_bundle(config, bundles_root=tmp_path))
        assert path == bundle_dir
