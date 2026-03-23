from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import OrderSide, OrderType, SignalDirection
from app.common.events.signal import SignalGeneratedEvent
from app.common.interfaces.repositories import EventLogRepository
from app.common.models.order import OrderRequest
from app.db.models import AgentSignalModel, PortfolioDecisionModel, ScreeningRunModel
from app.modules.equities.agents.graph import build_equities_graph
from app.modules.equities.agents.portfolio_manager import PortfolioManager
from app.modules.equities.config import EquitiesConfig
from app.modules.equities.models import RebalanceOrder, RunResult
from app.modules.equities.universe.provider import UniverseProvider
from app.modules.equities.universe.screener import (
    DividendYieldFilter,
    EarningsGrowthFilter,
    EarningsRecencyFilter,
    EarningsSurpriseFilter,
    FCFYieldFilter,
    GrossMarginTrendFilter,
    LeverageFilter,
    LiquidityFilter,
    MarketCapFilter,
    MomentumFilter,
    PBFilter,
    PEFilter,
    PEGFilter,
    PriceRangeFilter,
    RevenueGrowthFilter,
    ROEFilter,
    Screener,
    VolatilityFilter,
)
from app.modules.portfolio.service import PortfolioService
from app.modules.trade_execution.service import TradeExecutionService

logger = logging.getLogger(__name__)


class EquitiesBranchService:
    """Orchestrates the full equities branch pipeline."""

    def __init__(
        self,
        config: EquitiesConfig,
        data_service=None,
        trade_execution_service=None,
        portfolio_service=None,
        event_log=None,
        universe_provider: UniverseProvider | None = None,
        screener: Screener | None = None,
        news_analyst=None,
        fundamentals_analyst=None,
        technical_analyst=None,
        sec_edgar=None,
    ) -> None:
        self.config = config
        self.data_service = data_service
        self.trade_execution_service = trade_execution_service
        self.portfolio_service = portfolio_service
        self.event_log = event_log
        self.universe_provider = universe_provider or UniverseProvider()
        self.screener = screener
        self.news_analyst = news_analyst
        self.fundamentals_analyst = fundamentals_analyst
        self.technical_analyst = technical_analyst
        self.sec_edgar = sec_edgar

    def _build_screener(self, branch_name: str) -> Screener:
        """Build a branch-specific screener with the correct filter pipeline."""
        cfg = self.config.screening
        shared = [
            LiquidityFilter(cfg.min_avg_daily_volume),
            MarketCapFilter(cfg.min_market_cap),
            EarningsRecencyFilter(cfg.max_days_since_earnings),
            VolatilityFilter(cfg.max_volatility_percentile),
            LeverageFilter(cfg.max_debt_to_equity),
        ]
        if "growth" in branch_name:
            branch_filters = [
                RevenueGrowthFilter(cfg.min_revenue_growth_yoy),
                EarningsGrowthFilter(cfg.min_earnings_growth_yoy),
                GrossMarginTrendFilter(cfg.margin_declining_quarters),
                EarningsSurpriseFilter(cfg.min_surprise_pct),
                MomentumFilter(cfg.min_return_6m),
                PEGFilter(cfg.max_peg_ratio),
            ]
        elif "value" in branch_name:
            branch_filters = [
                PEFilter(cfg.max_pe_percentile),
                PBFilter(cfg.max_pb_percentile),
                FCFYieldFilter(cfg.min_fcf_yield),
                DividendYieldFilter(cfg.min_dividend_yield),
                ROEFilter(cfg.min_roe),
                PriceRangeFilter(cfg.max_52w_range_percentile),
            ]
        else:
            branch_filters = []
        return Screener(filters=shared + branch_filters)

    async def run_pipeline(
        self,
        branch_name: str,
        branch_id: str,
        trade_execution_service: TradeExecutionService | None = None,
        portfolio_service: PortfolioService | None = None,
        event_log_repo: EventLogRepository | None = None,
        session: AsyncSession | None = None,
        instrument_ids: dict[str, str] | None = None,
    ) -> RunResult:
        """Run the full pipeline: universe -> screen -> analyze -> rebalance -> execute."""
        tes = trade_execution_service or self.trade_execution_service
        ps = portfolio_service or self.portfolio_service
        el = event_log_repo or self.event_log

        # Set branch_name on LLM analysts so skill composition picks the right overlay
        for analyst in [self.news_analyst, self.fundamentals_analyst, self.technical_analyst]:
            if hasattr(analyst, "branch_name"):
                analyst.branch_name = branch_name

        # --- Gap 5: Build branch-specific screener ---
        screener = self._build_screener(branch_name)

        # --- Gap 2: Read real portfolio state ---
        current_positions: dict[str, float] = {}
        nav = 1_000_000.0
        if ps:
            try:
                portfolio = await ps.get_portfolio(branch_id)
                if portfolio:
                    nav = float(portfolio.nav) if portfolio.nav else 1_000_000.0
                    for pos in portfolio.positions:
                        if pos.long_quantity > 0 and nav > 0:
                            # Use market value (price × qty) instead of cost basis
                            # so weights reflect current allocation, not historical cost
                            price = None
                            if self.data_service:
                                price = await self.data_service.get_current_price(pos.symbol)
                            if price:
                                current_positions[pos.symbol] = (price * float(pos.long_quantity)) / nav
                            else:
                                current_positions[pos.symbol] = pos.long_cost_basis / nav
            except Exception:
                logger.warning("Could not read portfolio for %s, using defaults", branch_id)

        # --- Upsert instruments so FK constraints are satisfied ---
        # orders.instrument_id, trades.instrument_id, and positions.instrument_id
        # all FK to instruments.id. We pre-fetch the universe (warming the 90-day
        # cache) and upsert each stock so _execute_trade can use real DB UUIDs.
        # Company facts (sector, industry, etc.) are cached from hydration.
        _instrument_ids: dict[str, str] = instrument_ids or {}
        if not _instrument_ids and session and self.data_service:
            try:
                branch_key = "growth" if "growth" in branch_name else "value"
                universe_stocks = await self.universe_provider.get_holdings(branch_key)
                for stock in universe_stocks:
                    instrument_data: dict = {
                        "symbol": stock.symbol,
                        "name": stock.company_name,
                        "asset_class": "equity",
                    }
                    # Enrich with company facts (cache hit from hydration)
                    try:
                        facts = await self.data_service.get_company_facts(stock.symbol)
                        instrument_data.update(
                            {
                                "name": facts.get("name", stock.company_name),
                                "exchange": facts.get("exchange"),
                                "currency": facts.get("currency", "USD"),
                                "sector": facts.get("sector"),
                                "industry": facts.get("industry"),
                                "country": facts.get("country"),
                                "metadata": {
                                    k: v
                                    for k, v in {
                                        "description": facts.get("description"),
                                        "employees": facts.get("employees"),
                                        "website": facts.get("website"),
                                    }.items()
                                    if v is not None
                                },
                            }
                        )
                    except Exception:
                        logger.debug("Company facts unavailable for %s", stock.symbol)

                    upsert_result = await self.data_service.upsert_instrument(session, instrument_data)
                    _instrument_ids[stock.symbol] = upsert_result["instrument_id"]
                # Also upsert instruments for current positions (needed for SELL orders
                # when a stock has been removed from the ETF but is still held).
                for symbol in current_positions:
                    if symbol not in _instrument_ids:
                        upsert_result = await self.data_service.upsert_instrument(
                            session,
                            {"symbol": symbol, "name": symbol, "asset_class": "equity"},
                        )
                        _instrument_ids[symbol] = upsert_result["instrument_id"]
                logger.info("Upserted %d instruments for branch %s", len(_instrument_ids), branch_name)
            except Exception:
                logger.warning(
                    "Instrument upsert failed for branch %s; instrument_ids is empty, "
                    "all trade orders will be skipped this run.",
                    branch_name,
                    exc_info=True,
                )

        # --- Gap 1: Create trade execution bridge ---
        execute_trade_fn = None
        if tes:

            async def _execute_trade(order: RebalanceOrder) -> dict | None:
                try:
                    instr_id = _instrument_ids.get(order.symbol)
                    if not instr_id:
                        logger.warning("No instrument_id for %s — skipping order", order.symbol)
                        return None
                    req = OrderRequest(
                        branch_id=branch_id,
                        instrument_id=instr_id,
                        symbol=order.symbol,
                        side=OrderSide(order.side),
                        order_type=OrderType.MARKET,
                        quantity=order.quantity,
                    )
                    try:
                        return await tes.submit_order(req)
                    except Exception:
                        # Fallback: retry with whole shares if fractional rejected
                        whole_qty = int(order.quantity)
                        if whole_qty == 0:
                            logger.warning(
                                "Fractional order rejected and whole qty is 0 for %s",
                                order.symbol,
                            )
                            return None
                        logger.info("Retrying %s with whole shares: %d", order.symbol, whole_qty)
                        req.quantity = float(whole_qty)
                        return await tes.submit_order(req)
                except Exception as e:
                    logger.warning("Trade execution failed for %s: %s", order.symbol, e)
                    return None

            execute_trade_fn = _execute_trade

        pm = PortfolioManager(
            agents_config=self.config.agents,
            portfolio_config=self.config.portfolio,
        )

        graph = build_equities_graph(branch_name)

        initial_state = {
            "branch_name": branch_name,
            "branch_id": branch_id,
            "universe": [],
            "screened": [],
            "signals": [],
            "scores": [],
            "orders": [],
            "trades": [],
            "deps": {
                "config": self.config,
                "universe_provider": self.universe_provider,
                "screener": screener,
                "data_service": self.data_service,
                "news_analyst": self.news_analyst,
                "fundamentals_analyst": self.fundamentals_analyst,
                "technical_analyst": self.technical_analyst,
                "portfolio_manager": pm,
                "current_positions": current_positions,
                "nav": nav,
                "execute_trade_fn": execute_trade_fn,
                "max_concurrent_analyses": self.config.agents.max_concurrent_analyses,
            },
        }

        result = await graph.ainvoke(initial_state)

        signals = result.get("signals", [])
        scores = result.get("scores", [])
        orders = result.get("orders", [])
        screened = result.get("screened", [])
        universe = result.get("universe", [])

        # --- Gap 3: Event logging ---
        if el:
            await self._log_signal_events(el, branch_id, signals)

        # --- Gap 4: Persist run artifacts to DB ---
        if session:
            await self._persist_run_artifacts(
                session,
                branch_name,
                branch_id,
                universe,
                screened,
                signals,
                scores,
                orders,
                current_positions,
            )

        return RunResult(
            branch_name=branch_name,
            universe_count=len(universe),
            screened_count=len(screened),
            signals=signals,
            composite_scores=scores,
            orders=orders,
            trades_executed=len(result.get("trades", [])),
        )

    async def _log_signal_events(
        self,
        el: EventLogRepository,
        branch_id: str,
        signals: list,
    ) -> None:
        try:
            for sig in signals:
                bullish = sig.bullish_score if hasattr(sig, "bullish_score") else 5
                conf = sig.confidence if hasattr(sig, "confidence") else 5
                sym = sig.symbol if hasattr(sig, "symbol") else ""
                a_type = sig.analyst_type if hasattr(sig, "analyst_type") else ""
                summary = sig.summary if hasattr(sig, "summary") else ""
                direction = (
                    SignalDirection.BULLISH
                    if bullish >= 7
                    else SignalDirection.BEARISH
                    if bullish <= 3
                    else SignalDirection.NEUTRAL
                )
                await el.append(
                    SignalGeneratedEvent(
                        source=f"equities.{a_type}_analyst",
                        branch_id=branch_id,
                        agent_name=a_type,
                        instrument_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, sym)),
                        symbol=sym,
                        direction=direction,
                        confidence=float(conf) * 10.0,
                        reasoning=summary,
                    )
                )
        except Exception:
            logger.warning("Failed to log signal events", exc_info=True)

    async def _persist_run_artifacts(
        self,
        session: AsyncSession,
        branch_name: str,
        branch_id: str,
        universe: list,
        screened: list,
        signals: list,
        scores: list,
        orders: list,
        current_positions: dict,
    ) -> None:
        try:
            bid = uuid.UUID(branch_id) if isinstance(branch_id, str) else branch_id

            screening_run = ScreeningRunModel(
                branch_id=bid,
                branch_name=branch_name,
                universe_count=len(universe),
                passed_count=len(screened),
                config_snapshot=self.config.screening.model_dump(),
                passed_symbols=[s.symbol if hasattr(s, "symbol") else s for s in screened],
            )
            session.add(screening_run)
            await session.flush()

            for sig in signals:
                session.add(
                    AgentSignalModel(
                        screening_run_id=screening_run.id,
                        symbol=sig.symbol if hasattr(sig, "symbol") else "",
                        analyst_type=sig.analyst_type if hasattr(sig, "analyst_type") else "",
                        bullish_score=sig.bullish_score if hasattr(sig, "bullish_score") else 5,
                        confidence=sig.confidence if hasattr(sig, "confidence") else 5,
                        summary=sig.summary if hasattr(sig, "summary") else "",
                    )
                )

            target_holdings = {}
            composite_scores_dict = {}
            for sc in scores:
                sym = sc.symbol if hasattr(sc, "symbol") else ""
                target_holdings[sym] = sc.target_weight if hasattr(sc, "target_weight") else 0
                composite_scores_dict[sym] = {
                    "score": sc.composite_score if hasattr(sc, "composite_score") else 0,
                    "confidence": sc.composite_confidence if hasattr(sc, "composite_confidence") else 0,
                }

            orders_list = [o.model_dump(mode="json") if hasattr(o, "model_dump") else o for o in orders]

            session.add(
                PortfolioDecisionModel(
                    screening_run_id=screening_run.id,
                    branch_id=bid,
                    branch_name=branch_name,
                    target_holdings=target_holdings,
                    current_holdings=current_positions,
                    orders_generated=orders_list,
                    composite_scores=composite_scores_dict,
                )
            )
            await session.flush()
        except Exception:
            logger.warning("Failed to persist run artifacts", exc_info=True)
