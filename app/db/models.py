"""
SQLAlchemy ORM models — single source of truth for all database tables.
Maps directly to the PostgreSQL schema defined in the architecture doc.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


# ============================================================
# CORE ENTITIES
# ============================================================


class FundModel(Base):
    __tablename__ = "funds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    total_aum: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="paper")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=utcnow)

    branches: Mapped[list["BranchModel"]] = relationship(back_populates="fund")


class BranchModel(Base):
    __tablename__ = "branches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    fund_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("funds.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    branch_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    allocated_capital: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    execution_cadence: Mapped[str] = mapped_column(String(50), nullable=False, default="daily")
    config: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=utcnow)

    fund: Mapped["FundModel"] = relationship(back_populates="branches")
    portfolio: Mapped["PortfolioModel | None"] = relationship(back_populates="branch", uselist=False)


class InstrumentModel(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("symbol", "asset_class", "exchange", name="uq_instruments_symbol_class_exchange"),
        Index("idx_instruments_symbol", "symbol"),
        Index("idx_instruments_asset_class", "asset_class"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(50), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


# ============================================================
# PORTFOLIO STATE (mutable, query-optimized)
# ============================================================


class PortfolioModel(Base):
    __tablename__ = "portfolios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, unique=True
    )
    cash: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    margin_requirement: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    margin_used: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    nav: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_long_exposure: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_short_exposure: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=utcnow)

    branch: Mapped["BranchModel"] = relationship(back_populates="portfolio")
    positions: Mapped[list["PositionModel"]] = relationship(back_populates="portfolio")


class PositionModel(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "instrument_id", name="uq_positions_portfolio_instrument"),
        Index("idx_positions_portfolio", "portfolio_id"),
        Index("idx_positions_symbol", "symbol"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolios.id"), nullable=False)
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("instruments.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    long_quantity: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    long_cost_basis: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    short_quantity: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    short_cost_basis: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    short_margin_used: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    realized_pnl_long: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    realized_pnl_short: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    portfolio: Mapped["PortfolioModel"] = relationship(back_populates="positions")
    instrument: Mapped["InstrumentModel"] = relationship()


# ============================================================
# ORDER AND TRADE HISTORY
# ============================================================


class OrderModel(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_orders_branch", "branch_id"),
        Index("idx_orders_status", "status"),
        Index("idx_orders_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("instruments.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(20), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    stop_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    time_in_force: Mapped[str] = mapped_column(String(10), nullable=False, default="day")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    filled_quantity: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    average_fill_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    commission: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 2))
    reasoning: Mapped[str | None] = mapped_column(Text)
    agent_signals: Mapped[dict | None] = mapped_column(JSONB)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trades: Mapped[list["TradeModel"]] = relationship(back_populates="order")


class TradeModel(Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("idx_trades_branch", "branch_id"),
        Index("idx_trades_order", "order_id"),
        Index("idx_trades_executed", "executed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("instruments.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    commission: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    slippage: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    execution_mode: Mapped[str] = mapped_column(String(10), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    order: Mapped["OrderModel"] = relationship(back_populates="trades")


# ============================================================
# SNAPSHOTS AND HISTORY
# ============================================================


class PortfolioSnapshotModel(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        Index("idx_snapshots_portfolio", "portfolio_id"),
        Index("idx_snapshots_branch", "branch_id"),
        Index("idx_snapshots_at", "snapshot_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolios.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    cash: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    nav: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_long_exposure: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_short_exposure: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    gross_exposure: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    net_exposure: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    margin_used: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    positions_detail: Mapped[list[dict] | None] = mapped_column(JSONB)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AllocationDirectiveModel(Base):
    __tablename__ = "allocation_directives"
    __table_args__ = (
        Index("idx_allocations_fund", "fund_id"),
        Index("idx_allocations_issued", "issued_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    fund_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("funds.id"), nullable=False)
    total_aum: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    regime: Mapped[str | None] = mapped_column(String(50))
    allocations: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class RiskAlertModel(Base):
    __tablename__ = "risk_alerts"
    __table_args__ = (
        Index("idx_risk_alerts_level", "level"),
        Index("idx_risk_alerts_resolved", "resolved"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    metric: Mapped[str] = mapped_column(String(100), nullable=False)
    current_value: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    threshold: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    action_required: Mapped[str | None] = mapped_column(Text)
    affected_branches: Mapped[dict | None] = mapped_column(JSONB)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ============================================================
# EQUITIES BRANCH (screening, signals, decisions)
# ============================================================


class ScreeningRunModel(Base):
    __tablename__ = "screening_runs"
    __table_args__ = (Index("idx_screening_runs_branch", "branch_id", "run_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(20), nullable=False)
    run_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    universe_count: Mapped[int] = mapped_column(Integer, nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    passed_symbols: Mapped[dict] = mapped_column(JSONB, nullable=False)

    agent_signals: Mapped[list["AgentSignalModel"]] = relationship(back_populates="screening_run")
    portfolio_decisions: Mapped[list["PortfolioDecisionModel"]] = relationship(back_populates="screening_run")


class AgentSignalModel(Base):
    __tablename__ = "agent_signals"
    __table_args__ = (Index("idx_agent_signals_run", "screening_run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    screening_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("screening_runs.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    analyst_type: Mapped[str] = mapped_column(String(20), nullable=False)
    bullish_score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    screening_run: Mapped["ScreeningRunModel"] = relationship(back_populates="agent_signals")


class PortfolioDecisionModel(Base):
    __tablename__ = "portfolio_decisions"
    __table_args__ = (Index("idx_portfolio_decisions_branch", "branch_id", "decided_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    screening_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("screening_runs.id"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    target_holdings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    current_holdings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    orders_generated: Mapped[dict] = mapped_column(JSONB, nullable=False)
    composite_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)

    screening_run: Mapped["ScreeningRunModel"] = relationship(back_populates="portfolio_decisions")


# ============================================================
# EVENT LOG (append-only audit trail)
# ============================================================


class EventModel(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("idx_events_type", "event_type"),
        Index("idx_events_branch", "branch_id"),
        Index("idx_events_created", "created_at"),
        Index("idx_events_correlation", "correlation_id", postgresql_where=("correlation_id IS NOT NULL")),
        Index("idx_events_type_branch_created", "event_type", "branch_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    branch_id: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


# ============================================================
# BACKTEST (results persistence)
# ============================================================


class BacktestRunModel(Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index("idx_backtest_runs_status", "status"),
        Index("idx_backtest_runs_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    equities_config: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(12, 2))
    error_message: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    benchmark: Mapped[dict | None] = mapped_column(JSONB)
    rebalance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_pipeline_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    snapshots: Mapped[list["BacktestSnapshotModel"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    trades: Mapped[list["BacktestTradeModel"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    rebalance_details: Mapped[list["BacktestRebalanceDetailModel"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class BacktestSnapshotModel(Base):
    __tablename__ = "backtest_snapshots"
    __table_args__ = (
        Index("idx_backtest_snapshots_run", "backtest_run_id"),
        Index("idx_backtest_snapshots_date", "snapshot_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    nav: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    cash: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_long_exposure: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_short_exposure: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    positions: Mapped[dict | None] = mapped_column(JSONB)
    daily_return: Mapped[float] = mapped_column(Numeric(12, 8), nullable=False)
    cumulative_return: Mapped[float] = mapped_column(Numeric(12, 8), nullable=False)

    run: Mapped["BacktestRunModel"] = relationship(back_populates="snapshots")


class BacktestTradeModel(Base):
    __tablename__ = "backtest_trades"
    __table_args__ = (
        Index("idx_backtest_trades_run", "backtest_run_id"),
        Index("idx_backtest_trades_date", "trade_date"),
        Index("idx_backtest_trades_symbol", "symbol"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    commission: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    slippage: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)

    run: Mapped["BacktestRunModel"] = relationship(back_populates="trades")


class BacktestRebalanceDetailModel(Base):
    __tablename__ = "backtest_rebalance_details"
    __table_args__ = (
        Index("idx_backtest_rebalance_run", "backtest_run_id"),
        Index("idx_backtest_rebalance_date", "rebalance_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    rebalance_date: Mapped[date] = mapped_column(Date, nullable=False)
    screened_symbols: Mapped[dict] = mapped_column(JSONB, nullable=False)
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False)
    composite_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    orders_generated: Mapped[dict] = mapped_column(JSONB, nullable=False)

    run: Mapped["BacktestRunModel"] = relationship(back_populates="rebalance_details")


# ============================================================
# WEEKLY PIPELINE RUNS (scheduled autonomous execution tracking)
# ============================================================


class PipelineRunModel(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("idx_pipeline_runs_branch_date", "branch_id", "run_date"),
        Index("idx_pipeline_runs_status", "status"),
    )

    run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_json: Mapped[dict | None] = mapped_column(JSONB)
    error_msg: Mapped[str | None] = mapped_column(Text)


# ============================================================
# ATTRIBUTION REPORTS (Phase D — weekly post-hoc scoring)
# ============================================================


class AttributionReportModel(Base):
    """Weekly post-hoc scoring of a portfolio decision (Phase D attribution)."""

    __tablename__ = "attribution_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(50), nullable=False)
    decision_date: Mapped[date] = mapped_column(Date(), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date(), nullable=False)
    basket_return_conviction: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    basket_return_equal: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    benchmark_return: Mapped[float | None] = mapped_column(Numeric(10, 6))
    benchmark_symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    spy_return: Mapped[float | None] = mapped_column(Numeric(10, 6))
    analyst_ics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    n_holdings: Mapped[int] = mapped_column(Integer, nullable=False)
    n_holdings_priced: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("branch_id", "decision_date", name="uq_attribution_branch_decision"),)
