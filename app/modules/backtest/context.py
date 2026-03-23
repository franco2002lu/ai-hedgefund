"""BacktestContext — DI factory that wires all backtest components."""

from __future__ import annotations

import asyncio
import logging
from uuid import NAMESPACE_DNS, uuid5

from app.modules.backtest.adapters.backtest_broker import BacktestBrokerAdapter
from app.modules.backtest.adapters.historical_data import (
    HistoricalDataAdapter,
    HistoricalPriceStore,
)
from app.modules.backtest.config import BacktestConfig
from app.modules.backtest.engine import compute_rebalance_schedule
from app.modules.backtest.llm_analyst_cache import CachedAnalystWrapper
from app.modules.backtest.quantitative_analysts import (
    QuantitativeFundamentalsAnalyst,
    QuantitativeNewsAnalyst,
    QuantitativeTechnicalAnalyst,
)
from app.modules.backtest.state import (
    InMemoryEventLogRepository,
    InMemoryOrderRepository,
    InMemoryPortfolioRepository,
    InMemoryPositionRepository,
    InMemorySnapshotRepository,
    InMemoryTradeRepository,
)
from app.modules.backtest.time_provider import BacktestTimeProvider
from app.modules.data_platform.noop import NoOpCache, NoOpRateLimiter
from app.modules.data_platform.service import DataPlatformService
from app.modules.equities.config import EquitiesConfig
from app.modules.equities.service import EquitiesBranchService
from app.modules.equities.universe.provider import UniverseProvider
from app.modules.portfolio.service import PortfolioService
from app.modules.trade_execution.service import TradeExecutionService

logger = logging.getLogger(__name__)


class BacktestContext:
    """Holds all wired components for a single backtest run."""

    def __init__(self) -> None:
        self.time_provider: BacktestTimeProvider | None = None
        self.store: HistoricalPriceStore | None = None
        self.data_service: DataPlatformService | None = None
        self.portfolio_service: PortfolioService | None = None
        self.trade_execution_service: TradeExecutionService | None = None
        self.equities_service: EquitiesBranchService | None = None
        self.event_log: InMemoryEventLogRepository | None = None
        self.instrument_ids: dict[str, str] = {}
        self.trading_days: list = []
        self.rebalance_days: set = set()
        self.cancelled: asyncio.Event = asyncio.Event()
        self.trade_repo: InMemoryTradeRepository | None = None
        self.llm_cache: dict = {}
        self.llm_counter: list[int] = [0]
        self.llm_wrappers: list[CachedAnalystWrapper] = []

    @classmethod
    async def build(
        cls,
        config: BacktestConfig,
        live_config: EquitiesConfig,
    ) -> BacktestContext:
        ctx = cls()

        # 1. Time provider
        tp = BacktestTimeProvider(config.start_date)
        ctx.time_provider = tp

        # 2. Load universe symbols
        universe_provider = UniverseProvider()
        universe_stocks = await universe_provider.get_holdings(config.branch_name)
        symbols = [s.symbol for s in universe_stocks]
        all_symbols = list(set(symbols + config.benchmark_symbols))
        logger.info("Universe loaded: %d stocks for branch '%s'", len(symbols), config.branch_name)

        # 3. Preload OHLCV from yfinance
        store = HistoricalPriceStore()
        await store.preload(
            symbols=all_symbols,
            start_date=config.start_date,
            end_date=config.end_date,
        )
        ctx.store = store

        # 4. Pre-cache fundamentals from live Yahoo (parallel burst)
        from app.modules.data_platform.adapters.yahoo_finance import YahooFinanceAdapter

        live_yahoo = YahooFinanceAdapter()
        fundamentals_cache: dict[str, list] = {}
        facts_cache: dict[str, dict] = {}

        sem = asyncio.Semaphore(20)  # Match OHLCV batch size to avoid rate limits

        async def _fetch_fundamentals(sym: str) -> None:
            async with sem:
                try:
                    metrics = await live_yahoo.get_metrics(sym, config.end_date)
                    fundamentals_cache[sym] = metrics
                except Exception:
                    logger.debug("Fundamentals unavailable for %s", sym)
                try:
                    facts = await live_yahoo.get_company_facts(sym)
                    facts_cache[sym] = facts
                except Exception:
                    logger.debug("Company facts unavailable for %s", sym)

        await asyncio.gather(*(_fetch_fundamentals(sym) for sym in symbols))

        # 5. Historical data adapter → DataPlatformService (correct nested registry)
        adapter = HistoricalDataAdapter(
            store=store,
            time_provider=tp,
            fundamentals_cache=fundamentals_cache,
            facts_cache=facts_cache,
        )
        data_service = DataPlatformService(
            adapter_registry={
                "prices": {
                    "equity": [adapter],
                    "all": [adapter],
                },
                "fundamentals": {
                    "equity": [adapter],
                },
            },
            cache=NoOpCache(),
            rate_limiter=NoOpRateLimiter(),
        )
        ctx.data_service = data_service

        # 6. In-memory repositories
        portfolio_repo = InMemoryPortfolioRepository()
        position_repo = InMemoryPositionRepository()
        snapshot_repo = InMemorySnapshotRepository()
        order_repo = InMemoryOrderRepository()
        trade_repo = InMemoryTradeRepository()
        event_log_repo = InMemoryEventLogRepository()
        ctx.event_log = event_log_repo
        ctx.trade_repo = trade_repo

        # 7. Portfolio and trade execution services
        portfolio_service = PortfolioService(
            portfolio_repo=portfolio_repo,
            position_repo=position_repo,
            snapshot_repo=snapshot_repo,
            event_log=event_log_repo,
        )
        ctx.portfolio_service = portfolio_service

        broker = BacktestBrokerAdapter(
            store=store,
            time_provider=tp,
            slippage_bps=config.slippage_bps,
            commission_per_trade=config.commission_per_trade,
        )

        trade_execution_service = TradeExecutionService(
            order_repo=order_repo,
            trade_repo=trade_repo,
            broker=broker,
            event_log=event_log_repo,
            portfolio_service=portfolio_service,
        )
        ctx.trade_execution_service = trade_execution_service

        # 8. Create initial portfolio (must exist before run_pipeline calls get_portfolio)
        await portfolio_service.create_portfolio(
            branch_id=config.branch_name,
            branch_type="equities",
            initial_cash=config.initial_capital,
        )

        # 9. Analysts (quantitative by default)
        effective_config = config.equities_config_override or live_config

        if config.use_llm_agents:
            from app.modules.equities.agents.fundamentals_analyst import FundamentalsAnalyst
            from app.modules.equities.agents.llm_client import AnthropicAnalystClient
            from app.modules.equities.agents.news_analyst import NewsAnalyst
            from app.modules.equities.agents.technical_analyst import TechnicalAnalyst

            llm_cfg = effective_config.agents

            raw_news = NewsAnalyst(
                config=llm_cfg.news_analyst,
                data_service=data_service,
                llm_client=AnthropicAnalystClient(
                    model=llm_cfg.news_analyst.model,
                    temperature=llm_cfg.news_analyst.temperature,
                ),
            )
            raw_fundamentals = FundamentalsAnalyst(
                config=llm_cfg.fundamentals_analyst,
                data_service=data_service,
                llm_client=AnthropicAnalystClient(
                    model=llm_cfg.fundamentals_analyst.model,
                    temperature=llm_cfg.fundamentals_analyst.temperature,
                ),
                time_provider=tp,
            )
            raw_technical = TechnicalAnalyst(
                config=llm_cfg.technical_analyst,
                data_service=data_service,
                llm_client=AnthropicAnalystClient(
                    model=llm_cfg.technical_analyst.model,
                    temperature=llm_cfg.technical_analyst.temperature,
                ),
                time_provider=tp,
            )

            cache_cfg = config.llm_config
            shared_cache: dict = {}
            shared_counter: list[int] = [0]

            news_analyst = CachedAnalystWrapper(
                analyst=raw_news,
                analyst_type="news",
                cache=shared_cache,
                llm_counter=shared_counter,
                max_calls_per_rebalance=cache_cfg.max_llm_calls_per_rebalance,
                cache_enabled=cache_cfg.cache_signals,
                current_date_fn=lambda: tp.today(),
            )
            fundamentals_analyst = CachedAnalystWrapper(
                analyst=raw_fundamentals,
                analyst_type="fundamentals",
                cache=shared_cache,
                llm_counter=shared_counter,
                max_calls_per_rebalance=cache_cfg.max_llm_calls_per_rebalance,
                cache_enabled=cache_cfg.cache_signals,
                current_date_fn=lambda: tp.today(),
            )
            technical_analyst = CachedAnalystWrapper(
                analyst=raw_technical,
                analyst_type="technical",
                cache=shared_cache,
                llm_counter=shared_counter,
                max_calls_per_rebalance=cache_cfg.max_llm_calls_per_rebalance,
                cache_enabled=cache_cfg.cache_signals,
                current_date_fn=lambda: tp.today(),
            )

            ctx.llm_cache = shared_cache
            ctx.llm_counter = shared_counter
            ctx.llm_wrappers = [news_analyst, fundamentals_analyst, technical_analyst]
        else:
            news_analyst = QuantitativeNewsAnalyst(data_service, tp)
            fundamentals_analyst = QuantitativeFundamentalsAnalyst(data_service, tp)
            technical_analyst = QuantitativeTechnicalAnalyst(data_service, tp)

        # 10. Equities branch service (new instance, NOT the singleton)
        equities_service = EquitiesBranchService(
            config=effective_config,
            data_service=data_service,
            trade_execution_service=trade_execution_service,
            portfolio_service=portfolio_service,
            event_log=event_log_repo,
            news_analyst=news_analyst,
            fundamentals_analyst=fundamentals_analyst,
            technical_analyst=technical_analyst,
        )
        ctx.equities_service = equities_service

        # 11. Deterministic instrument IDs from actual universe symbols
        ctx.instrument_ids = {symbol: str(uuid5(NAMESPACE_DNS, symbol)) for symbol in symbols}

        # 12. Trading days + rebalance schedule
        trading_days = store.get_trading_days(config.start_date, config.end_date)
        ctx.trading_days = trading_days

        ctx.rebalance_days = compute_rebalance_schedule(
            trading_days,
            config.rebalance_frequency,
        )

        # 13. Cancellation event
        ctx.cancelled = asyncio.Event()

        return ctx
