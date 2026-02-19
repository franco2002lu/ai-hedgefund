# Phase 1: Shared Infrastructure -- Module Interfaces & Data Models

This document defines the concrete module interfaces, data models, event schemas, and
project structure for the shared infrastructure layer that all branch modules depend on.

> **Architecture**: This project uses a **modular monolith** -- a single FastAPI process
> with clean module boundaries. See [HIGH-LEVEL-ARCH.md](HIGH-LEVEL-ARCH.md) for rationale.

---

## 1. Project Structure

Single Python package with module subdirectories. All modules run within one FastAPI
process and share a single PostgreSQL database.

```
ai-hedge-fund/
├── app/                                   # Main application package
│   ├── __init__.py
│   ├── main.py                            # Single FastAPI app, router registration, lifespan
│   ├── config.py                          # App configuration (env vars, defaults)
│   ├── dependencies.py                    # Top-level DI wiring (composition root)
│   │
│   ├── common/                            # Shared library (models, enums, interfaces)
│   │   ├── __init__.py
│   │   ├── enums.py                       # Shared enumerations
│   │   ├── events/                        # Event schemas (Pydantic, logged to DB)
│   │   │   ├── __init__.py
│   │   │   ├── base.py                    # BaseEvent
│   │   │   ├── trade.py                   # Trade lifecycle events
│   │   │   ├── portfolio.py               # Portfolio events
│   │   │   ├── allocation.py              # Allocation events
│   │   │   ├── risk.py                    # Risk alert events
│   │   │   └── signal.py                  # Agent signal events
│   │   ├── models/                        # Domain models (Pydantic)
│   │   │   ├── __init__.py
│   │   │   ├── instrument.py
│   │   │   ├── position.py
│   │   │   ├── portfolio.py
│   │   │   ├── order.py
│   │   │   └── trade.py
│   │   ├── interfaces/                    # Abstract interfaces (ABCs)
│   │   │   ├── __init__.py
│   │   │   ├── broker.py                  # BrokerAdapter ABC
│   │   │   ├── price_data.py              # PriceDataAdapter ABC
│   │   │   ├── fundamentals.py            # FundamentalsAdapter ABC
│   │   │   ├── news.py                    # NewsAdapter ABC
│   │   │   ├── macro.py                   # MacroAdapter ABC
│   │   │   └── repositories.py            # Repository ABCs
│   │   └── schemas/                       # Shared API response schemas
│   │       ├── __init__.py
│   │       ├── responses.py               # ErrorResponse
│   │       └── pagination.py              # PaginationParams, PaginatedResponse
│   │
│   ├── db/                                # Database layer (shared across modules)
│   │   ├── __init__.py
│   │   ├── connection.py                  # SQLAlchemy async engine + session factory
│   │   ├── models.py                      # All ORM models (single source of truth)
│   │   └── migrations/                    # Alembic migrations
│   │       ├── env.py
│   │       └── versions/
│   │
│   ├── modules/                           # Business logic modules
│   │   ├── __init__.py
│   │   │
│   │   ├── portfolio/                     # Portfolio Module
│   │   │   ├── __init__.py
│   │   │   ├── api.py                     # FastAPI routes (thin)
│   │   │   ├── service.py                 # Business logic
│   │   │   └── repository.py              # PostgreSQL data access
│   │   │
│   │   ├── trade_execution/               # Trade Execution Module
│   │   │   ├── __init__.py
│   │   │   ├── api.py                     # FastAPI routes (thin)
│   │   │   ├── service.py                 # Order routing + validation
│   │   │   ├── repository.py              # PostgreSQL data access
│   │   │   └── adapters/                  # Broker adapters
│   │   │       ├── __init__.py
│   │   │       └── paper.py               # PaperTradingAdapter
│   │   │
│   │   ├── data_platform/                 # Data Platform Module
│   │   │   ├── __init__.py
│   │   │   ├── api.py                     # FastAPI routes (thin)
│   │   │   ├── service.py                 # Routing + fallback logic
│   │   │   ├── cache.py                   # In-memory TTL cache (cachetools)
│   │   │   ├── rate_limiter.py            # Per-source rate limiting
│   │   │   └── adapters/                  # Data source adapters
│   │   │       ├── __init__.py
│   │   │       └── yahoo_finance.py       # YahooFinanceAdapter (primary for Phase 1)
│   │   │
│   │   └── event_log/                     # Event Log Module
│   │       ├── __init__.py
│   │       ├── service.py                 # append_event(), query_events()
│   │       └── repository.py              # PostgreSQL events table access
│   │
│   └── central_orchestrator/              # Central Orchestration (Phase 3, stubbed)
│       └── __init__.py
│
├── infrastructure/
│   └── docker-compose.yml                 # Local dev: PostgreSQL only
│
├── pyproject.toml                         # Single project dependencies
├── Dockerfile                             # Single container
│
├── plans/                                 # Architecture docs
│   └── architecture/
│       ├── HIGH-LEVEL-ARCH.md
│       └── PHASE1-SHARED-INFRASTRUCTURE.md  (this file)
│
└── src/                                   # Existing repo (reference, not modified)
```

**Key differences from a microservices layout:**
- One `app/main.py` creates a single FastAPI app and registers all module routers
- One `app/db/` directory with shared ORM models and migrations (no per-service databases)
- Modules call each other's service classes directly (no HTTP or message bus between them)
- One `Dockerfile`, one `pyproject.toml`, one deployment unit

---

## 2. Shared Enumerations

```python
# common/enums.py

from enum import Enum


class AssetClass(str, Enum):
    EQUITY = "equity"
    CRYPTO = "crypto"
    BOND = "bond"
    COMMODITY = "commodity"
    FUTURES = "futures"
    OPTION = "option"


class BranchType(str, Enum):
    EQUITIES = "equities"
    CRYPTO = "crypto"
    BONDS = "bonds"
    COMMODITIES = "commodities"
    QUANT = "quant"


class OrderSide(str, Enum):
    """
    Matches the existing repo's action model:
    BUY = open long, SELL = close long, SHORT = open short, COVER = close short.
    """
    BUY = "buy"
    SELL = "sell"
    SHORT = "short"
    COVER = "cover"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    PENDING = "pending"            # Created, not yet submitted to broker
    SUBMITTED = "submitted"        # Sent to broker, awaiting fill
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TimeInForce(str, Enum):
    DAY = "day"                    # Cancel at end of trading session
    GTC = "gtc"                    # Good 'til cancelled
    IOC = "ioc"                    # Immediate or cancel
    FOK = "fok"                    # Fill or kill


class ExecutionMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class RiskAlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SignalDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
```

---

## 3. Domain Models

### 3.1 Instrument

```python
# common/models/instrument.py

from pydantic import BaseModel
from common.enums import AssetClass


class Instrument(BaseModel):
    """
    Unified instrument model across all asset classes.

    Examples:
      - Equity:    symbol="AAPL", asset_class=EQUITY, exchange="NASDAQ", currency="USD"
      - Crypto:    symbol="BTC-USD", asset_class=CRYPTO, exchange="COINBASE", currency="USD"
      - Bond:      symbol="US10Y", asset_class=BOND, exchange=None, currency="USD"
      - Commodity: symbol="CL=F", asset_class=COMMODITY, exchange="NYMEX", currency="USD"
    """
    id: str                              # UUID
    symbol: str                          # Ticker / trading symbol
    name: str                            # Human-readable name
    asset_class: AssetClass
    exchange: str | None = None
    currency: str = "USD"
    is_active: bool = True
    metadata: dict | None = None         # Asset-class-specific fields
                                         # e.g. {"sector": "Technology"} for equities
                                         # e.g. {"contract_size": 1000} for futures
```

### 3.2 Position

```python
# common/models/position.py

from pydantic import BaseModel
from datetime import datetime


class Position(BaseModel):
    """
    Represents a holding in a single instrument within a portfolio.
    Tracks long and short sides independently (consistent with existing repo).
    """
    id: str                              # UUID
    portfolio_id: str
    instrument_id: str
    symbol: str                          # Denormalized for convenience

    long_quantity: float = 0.0           # float to support fractional shares and crypto
    long_cost_basis: float = 0.0         # Total cost of long position

    short_quantity: float = 0.0          # float to support fractional shares and crypto
    short_cost_basis: float = 0.0        # Total proceeds from short sale
    short_margin_used: float = 0.0       # Margin held for short position

    realized_pnl_long: float = 0.0       # Cumulative realized P&L from long trades
    realized_pnl_short: float = 0.0      # Cumulative realized P&L from short trades

    updated_at: datetime
```

### 3.3 Portfolio

```python
# common/models/portfolio.py

from pydantic import BaseModel
from datetime import datetime
from common.models.position import Position


class PortfolioSummary(BaseModel):
    """
    Current state of a branch's portfolio.
    Returned by the Portfolio Service.
    """
    id: str                              # UUID
    branch_id: str
    branch_type: str                     # e.g. "equities", "crypto"

    cash: float
    allocated_capital: float             # Capital budget from central allocator
    margin_requirement: float            # e.g. 0.5 = 50% margin required
    margin_used: float

    # Computed fields (from positions + market prices)
    nav: float                           # Net asset value
    total_long_exposure: float
    total_short_exposure: float
    gross_exposure: float
    net_exposure: float
    unrealized_pnl: float
    realized_pnl: float

    positions: list[Position] = []       # Populated on detail requests
    updated_at: datetime


class PortfolioSnapshot(BaseModel):
    """
    Point-in-time snapshot, stored periodically for historical tracking.
    """
    id: str
    portfolio_id: str
    branch_id: str

    cash: float
    nav: float
    total_long_exposure: float
    total_short_exposure: float
    gross_exposure: float
    net_exposure: float
    unrealized_pnl: float
    realized_pnl: float
    margin_used: float

    position_count: int
    top_holdings: list[dict]             # [{symbol, side, quantity, weight}]

    snapshot_at: datetime
```

### 3.4 Order

```python
# common/models/order.py

from pydantic import BaseModel
from datetime import datetime
from common.enums import OrderSide, OrderType, OrderStatus, TimeInForce


class OrderRequest(BaseModel):
    """
    Submitted by a branch service to request a trade.
    """
    branch_id: str
    instrument_id: str
    symbol: str                          # Denormalized
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    quantity: float                      # float to support fractional shares and crypto
    limit_price: float | None = None     # Required for LIMIT and STOP_LIMIT
    stop_price: float | None = None      # Required for STOP and STOP_LIMIT
    time_in_force: TimeInForce = TimeInForce.DAY

    # Agent context (stored for auditability, not used by execution)
    confidence: float | None = None      # 0-100
    reasoning: str | None = None
    agent_signals: dict | None = None    # {agent_name: {signal, confidence, reasoning}}


class Order(BaseModel):
    """
    Full order record with lifecycle state.
    """
    id: str                              # UUID
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float                      # float to support fractional shares and crypto
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce

    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0         # float to support fractional shares and crypto
    average_fill_price: float = 0.0
    commission: float = 0.0

    confidence: float | None = None
    reasoning: str | None = None
    agent_signals: dict | None = None

    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
```

### 3.5 Trade (Fill)

```python
# common/models/trade.py

from pydantic import BaseModel
from datetime import datetime
from common.enums import OrderSide, ExecutionMode


class Trade(BaseModel):
    """
    A single fill / execution. One order can produce multiple trades
    (partial fills), though for MVP most orders will produce one trade.
    """
    id: str                              # UUID
    order_id: str
    branch_id: str
    instrument_id: str
    symbol: str

    side: OrderSide
    quantity: float                      # float to support fractional shares and crypto
    price: float                         # Fill price
    commission: float = 0.0
    slippage: float = 0.0                # Difference from mid-price at time of fill

    execution_mode: ExecutionMode        # paper or live
    executed_at: datetime
```

---

## 4. Event Schemas

### 4.1 Event Log Design

Instead of Kafka topics, all events are logged to a single PostgreSQL `events` table.
Events are append-only rows with JSON payloads, providing the same audit trail as a
message bus but with SQL queryability and zero additional infrastructure.

| Event Type | Logged By | Used By |
|---|---|---|
| `trade.requested` | Branch modules | Audit trail, replay |
| `trade.executed` | Trade Execution module | Portfolio module (called directly), audit trail |
| `trade.rejected` | Trade Execution module | Audit trail |
| `order.status_changed` | Trade Execution module | Audit trail |
| `portfolio.updated` | Portfolio module | Audit trail |
| `portfolio.snapshot` | Portfolio module | Central Orchestrator (queries event log) |
| `allocation.directive` | Central Orchestrator | Audit trail |
| `risk.alert` | Global Risk Manager | Central Orchestrator (queries event log) |
| `signal.generated` | Branch modules (agents) | Audit trail, analytics |

Events are serialized as JSON in the `payload` column. The Pydantic event schemas
(sections 4.2-4.7) define the structure of each event type's payload.

### 4.2 Base Event

```python
# common/events/base.py

from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid


class BaseEvent(BaseModel):
    """All events inherit from this."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str                      # e.g. "trade.requested"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str                          # Service that produced the event
    correlation_id: str | None = None    # For tracing a request across services
```

### 4.3 Trade Events

```python
# common/events/trade.py

from common.events.base import BaseEvent
from common.enums import OrderSide, OrderType, OrderStatus, TimeInForce, ExecutionMode


class TradeRequestedEvent(BaseEvent):
    event_type: str = "trade.requested"

    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY

    # Agent context
    confidence: float | None = None
    reasoning: str | None = None
    agent_signals: dict | None = None


class TradeExecutedEvent(BaseEvent):
    event_type: str = "trade.executed"

    trade_id: str
    order_id: str
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    quantity: float
    fill_price: float
    commission: float = 0.0
    slippage: float = 0.0
    execution_mode: ExecutionMode


class TradeRejectedEvent(BaseEvent):
    event_type: str = "trade.rejected"

    order_id: str
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    quantity: float
    rejection_reason: str                # e.g. "insufficient_cash", "risk_limit_exceeded"


class OrderStatusChangedEvent(BaseEvent):
    event_type: str = "order.status_changed"

    order_id: str
    branch_id: str
    previous_status: OrderStatus
    new_status: OrderStatus
    filled_quantity: float = 0.0
    average_fill_price: float = 0.0
```

### 4.4 Portfolio Events

```python
# common/events/portfolio.py

from common.events.base import BaseEvent


class PortfolioUpdatedEvent(BaseEvent):
    event_type: str = "portfolio.updated"

    portfolio_id: str
    branch_id: str
    trigger: str                         # "trade_executed", "allocation_adjusted", "manual"

    cash: float
    nav: float
    margin_used: float
    total_long_exposure: float
    total_short_exposure: float
    unrealized_pnl: float
    realized_pnl: float

    # The trade that triggered this update (if applicable)
    trade_id: str | None = None


class PortfolioSnapshotEvent(BaseEvent):
    event_type: str = "portfolio.snapshot"

    snapshot_id: str
    portfolio_id: str
    branch_id: str

    cash: float
    nav: float
    total_long_exposure: float
    total_short_exposure: float
    gross_exposure: float
    net_exposure: float
    unrealized_pnl: float
    realized_pnl: float
    margin_used: float
    position_count: int
```

### 4.5 Allocation Events

```python
# common/events/allocation.py

from pydantic import BaseModel
from common.events.base import BaseEvent


class BranchAllocation(BaseModel):
    branch_id: str
    branch_type: str
    target_capital: float
    current_capital: float
    delta: float                         # target - current
    action: str                          # "increase", "decrease", "hold"


class AllocationDirectiveEvent(BaseEvent):
    event_type: str = "allocation.directive"

    fund_id: str
    total_aum: float
    regime: str                          # Market regime classification
    allocations: list[BranchAllocation]
    reasoning: str
```

### 4.6 Risk Events

```python
# common/events/risk.py

from common.events.base import BaseEvent
from common.enums import RiskAlertLevel


class RiskAlertEvent(BaseEvent):
    event_type: str = "risk.alert"

    level: RiskAlertLevel
    source: str                          # "global" or branch_id
    metric: str                          # e.g. "max_drawdown", "concentration", "correlation"
    current_value: float
    threshold: float
    message: str
    action_required: str | None = None   # e.g. "reduce_exposure", "halt_trading"
    affected_branches: list[str] = []
```

### 4.7 Signal Events

```python
# common/events/signal.py

from common.events.base import BaseEvent
from common.enums import SignalDirection


class SignalGeneratedEvent(BaseEvent):
    event_type: str = "signal.generated"

    branch_id: str
    agent_name: str
    instrument_id: str
    symbol: str

    direction: SignalDirection
    confidence: float                    # 0-100
    reasoning: str
    data_sources_used: list[str] = []    # e.g. ["financial_metrics", "insider_trades"]
```

---

## 5. Repository Pattern

All database access is abstracted behind repository interfaces defined in `common/interfaces/repositories.py`.
Modules depend on the abstract interface; concrete implementations live in each module's `repository.py`.
This enables swapping persistence for testing (in-memory) or backtesting (historical replay) without changing business logic.

### 5.1 Repository Interfaces

```python
# common/interfaces/repositories.py

from abc import ABC, abstractmethod
from datetime import datetime


class PortfolioRepository(ABC):
    """Data access for portfolio state."""

    @abstractmethod
    async def get_by_branch(self, branch_id: str) -> PortfolioSummary | None:
        ...

    @abstractmethod
    async def create(self, branch_id: str, branch_type: str, initial_cash: float, margin_requirement: float) -> PortfolioSummary:
        ...

    @abstractmethod
    async def update_cash(self, branch_id: str, amount: float, reason: str) -> PortfolioSummary:
        ...

    @abstractmethod
    async def get_fund_summary(self, fund_id: str) -> FundSummaryResponse:
        ...


class PositionRepository(ABC):
    """Data access for positions within a portfolio."""

    @abstractmethod
    async def get_by_portfolio(self, portfolio_id: str) -> list[Position]:
        ...

    @abstractmethod
    async def get_by_symbol(self, portfolio_id: str, symbol: str) -> Position | None:
        ...

    @abstractmethod
    async def upsert(self, position: Position) -> Position:
        """Create or update a position. Used after trade execution."""
        ...

    @abstractmethod
    async def delete_if_flat(self, portfolio_id: str, instrument_id: str) -> bool:
        """Remove position record if both long and short quantities are zero."""
        ...


class SnapshotRepository(ABC):
    """Data access for portfolio snapshots."""

    @abstractmethod
    async def create(self, portfolio_id: str, branch_id: str) -> PortfolioSnapshot:
        ...

    @abstractmethod
    async def list_by_branch(
        self, branch_id: str, limit: int = 30, offset: int = 0
    ) -> tuple[list[PortfolioSnapshot], int]:
        """Returns (snapshots, total_count) for pagination."""
        ...


class OrderRepository(ABC):
    """Data access for orders."""

    @abstractmethod
    async def create(self, order: Order) -> Order:
        ...

    @abstractmethod
    async def get_by_id(self, order_id: str) -> Order | None:
        ...

    @abstractmethod
    async def update_status(
        self, order_id: str, status: OrderStatus, **kwargs
    ) -> Order:
        """Update order status and optional fields (filled_quantity, average_fill_price, etc.)."""
        ...

    @abstractmethod
    async def list_orders(
        self,
        branch_id: str | None = None,
        status: OrderStatus | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Order], int]:
        """Returns (orders, total_count) for pagination."""
        ...


class TradeRepository(ABC):
    """Data access for executed trades (fills)."""

    @abstractmethod
    async def create(self, trade: Trade) -> Trade:
        ...

    @abstractmethod
    async def get_by_id(self, trade_id: str) -> Trade | None:
        ...

    @abstractmethod
    async def list_trades(
        self,
        branch_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Trade], int]:
        """Returns (trades, total_count) for pagination."""
        ...


class EventLogRepository(ABC):
    """
    Append-only event log for auditability and replay.
    Every business action logs an event to this store.
    """

    @abstractmethod
    async def append(self, event: "BaseEvent") -> None:
        """Persist an event to the event log."""
        ...

    @abstractmethod
    async def query(
        self,
        event_type: str | None = None,
        branch_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query events with optional filters. Returns raw event dicts."""
        ...
```

### 5.2 Implementation Notes

- **PostgreSQL implementations** (e.g., `PostgresPortfolioRepository`) use SQLAlchemy async sessions
  and are the default for all modules.
- **In-memory implementations** (e.g., `InMemoryPortfolioRepository`) can be used for unit tests
  and future backtesting, implementing the same interface with dict-based storage.
- Repository methods that return lists use the `tuple[list[T], int]` pattern to support
  paginated responses (items + total count).
- Repositories do NOT log events -- that is the service layer's responsibility (via the EventLogRepository).

---

## 6. PostgreSQL Schema

All tables use UUIDs as primary keys. Timestamps are timezone-aware.

```sql
-- ============================================================
-- CORE ENTITIES
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE funds (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    total_aum       NUMERIC(18, 2) NOT NULL DEFAULT 0,
    execution_mode  VARCHAR(20) NOT NULL DEFAULT 'paper',    -- 'paper' or 'live'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

CREATE TABLE branches (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    fund_id             UUID NOT NULL REFERENCES funds(id),
    name                VARCHAR(200) NOT NULL,
    branch_type         VARCHAR(50) NOT NULL,                -- matches BranchType enum
    status              VARCHAR(50) NOT NULL DEFAULT 'active', -- 'active', 'paused', 'disabled'
    allocated_capital   NUMERIC(18, 2) NOT NULL DEFAULT 0,
    execution_cadence   VARCHAR(50) NOT NULL DEFAULT 'daily', -- 'continuous', 'hourly', 'daily', 'weekly'
    config              JSONB,                                -- branch-specific configuration
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ
);

CREATE TABLE instruments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol          VARCHAR(50) NOT NULL,
    name            VARCHAR(300) NOT NULL,
    asset_class     VARCHAR(50) NOT NULL,                    -- matches AssetClass enum
    exchange        VARCHAR(100),
    currency        VARCHAR(10) NOT NULL DEFAULT 'USD',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    metadata        JSONB,                                    -- asset-class-specific fields
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(symbol, asset_class, exchange)
);

CREATE INDEX idx_instruments_symbol ON instruments(symbol);
CREATE INDEX idx_instruments_asset_class ON instruments(asset_class);


-- ============================================================
-- PORTFOLIO STATE (mutable, query-optimized)
-- ============================================================

CREATE TABLE portfolios (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    branch_id           UUID NOT NULL UNIQUE REFERENCES branches(id),

    cash                NUMERIC(18, 2) NOT NULL DEFAULT 0,
    margin_requirement  NUMERIC(5, 4) NOT NULL DEFAULT 0,    -- e.g. 0.5000 = 50%
    margin_used         NUMERIC(18, 2) NOT NULL DEFAULT 0,

    -- Computed / cached aggregates (updated on each trade)
    nav                         NUMERIC(18, 2) NOT NULL DEFAULT 0,
    total_long_exposure         NUMERIC(18, 2) NOT NULL DEFAULT 0,
    total_short_exposure        NUMERIC(18, 2) NOT NULL DEFAULT 0,
    unrealized_pnl              NUMERIC(18, 2) NOT NULL DEFAULT 0,
    realized_pnl                NUMERIC(18, 2) NOT NULL DEFAULT 0,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ
);

CREATE TABLE positions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    portfolio_id        UUID NOT NULL REFERENCES portfolios(id),
    instrument_id       UUID NOT NULL REFERENCES instruments(id),
    symbol              VARCHAR(50) NOT NULL,                -- denormalized

    long_quantity       NUMERIC(18, 8) NOT NULL DEFAULT 0,  -- supports fractional shares and crypto
    long_cost_basis     NUMERIC(18, 2) NOT NULL DEFAULT 0,

    short_quantity      NUMERIC(18, 8) NOT NULL DEFAULT 0,  -- supports fractional shares and crypto
    short_cost_basis    NUMERIC(18, 2) NOT NULL DEFAULT 0,
    short_margin_used   NUMERIC(18, 2) NOT NULL DEFAULT 0,

    realized_pnl_long   NUMERIC(18, 2) NOT NULL DEFAULT 0,
    realized_pnl_short  NUMERIC(18, 2) NOT NULL DEFAULT 0,

    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(portfolio_id, instrument_id)
);

CREATE INDEX idx_positions_portfolio ON positions(portfolio_id);
CREATE INDEX idx_positions_symbol ON positions(symbol);


-- ============================================================
-- ORDER AND TRADE HISTORY
-- ============================================================

CREATE TABLE orders (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    branch_id           UUID NOT NULL REFERENCES branches(id),
    instrument_id       UUID NOT NULL REFERENCES instruments(id),
    symbol              VARCHAR(50) NOT NULL,

    side                VARCHAR(20) NOT NULL,                -- OrderSide enum
    order_type          VARCHAR(20) NOT NULL,                -- OrderType enum
    quantity            NUMERIC(18, 8) NOT NULL,           -- supports fractional shares and crypto
    limit_price         NUMERIC(18, 6),
    stop_price          NUMERIC(18, 6),
    time_in_force       VARCHAR(10) NOT NULL DEFAULT 'day',

    status              VARCHAR(30) NOT NULL DEFAULT 'pending',
    filled_quantity     NUMERIC(18, 8) NOT NULL DEFAULT 0, -- supports fractional shares and crypto
    average_fill_price  NUMERIC(18, 6) NOT NULL DEFAULT 0,
    commission          NUMERIC(12, 4) NOT NULL DEFAULT 0,
    rejection_reason    TEXT,

    -- Agent decision context (audit trail)
    confidence          NUMERIC(5, 2),
    reasoning           TEXT,
    agent_signals       JSONB,

    correlation_id      UUID,                                -- Links to originating event

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ,
    submitted_at        TIMESTAMPTZ,
    filled_at           TIMESTAMPTZ
);

CREATE INDEX idx_orders_branch ON orders(branch_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_created ON orders(created_at);

CREATE TABLE trades (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id            UUID NOT NULL REFERENCES orders(id),
    branch_id           UUID NOT NULL REFERENCES branches(id),
    instrument_id       UUID NOT NULL REFERENCES instruments(id),
    symbol              VARCHAR(50) NOT NULL,

    side                VARCHAR(20) NOT NULL,
    quantity            NUMERIC(18, 8) NOT NULL,           -- supports fractional shares and crypto
    price               NUMERIC(18, 6) NOT NULL,
    commission          NUMERIC(12, 4) NOT NULL DEFAULT 0,
    slippage            NUMERIC(12, 6) NOT NULL DEFAULT 0,

    execution_mode      VARCHAR(10) NOT NULL,                -- 'paper' or 'live'
    executed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_trades_branch ON trades(branch_id);
CREATE INDEX idx_trades_order ON trades(order_id);
CREATE INDEX idx_trades_executed ON trades(executed_at);


-- ============================================================
-- SNAPSHOTS AND HISTORY
-- ============================================================

CREATE TABLE portfolio_snapshots (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    portfolio_id            UUID NOT NULL REFERENCES portfolios(id),
    branch_id               UUID NOT NULL REFERENCES branches(id),

    cash                    NUMERIC(18, 2) NOT NULL,
    nav                     NUMERIC(18, 2) NOT NULL,
    total_long_exposure     NUMERIC(18, 2) NOT NULL,
    total_short_exposure    NUMERIC(18, 2) NOT NULL,
    gross_exposure          NUMERIC(18, 2) NOT NULL,
    net_exposure            NUMERIC(18, 2) NOT NULL,
    unrealized_pnl          NUMERIC(18, 2) NOT NULL,
    realized_pnl            NUMERIC(18, 2) NOT NULL,
    margin_used             NUMERIC(18, 2) NOT NULL,

    position_count          INTEGER NOT NULL,
    positions_detail        JSONB,                            -- Full position breakdown

    snapshot_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_snapshots_portfolio ON portfolio_snapshots(portfolio_id);
CREATE INDEX idx_snapshots_branch ON portfolio_snapshots(branch_id);
CREATE INDEX idx_snapshots_at ON portfolio_snapshots(snapshot_at);


CREATE TABLE allocation_directives (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    fund_id             UUID NOT NULL REFERENCES funds(id),
    total_aum           NUMERIC(18, 2) NOT NULL,
    regime              VARCHAR(50),
    allocations         JSONB NOT NULL,                       -- list of BranchAllocation
    reasoning           TEXT,
    issued_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_allocations_fund ON allocation_directives(fund_id);
CREATE INDEX idx_allocations_issued ON allocation_directives(issued_at);


CREATE TABLE risk_alerts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    level               VARCHAR(20) NOT NULL,                -- RiskAlertLevel enum
    source              VARCHAR(100) NOT NULL,               -- 'global' or branch_id
    metric              VARCHAR(100) NOT NULL,
    current_value       NUMERIC(18, 6) NOT NULL,
    threshold           NUMERIC(18, 6) NOT NULL,
    message             TEXT NOT NULL,
    action_required     TEXT,
    affected_branches   JSONB,
    resolved            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX idx_risk_alerts_level ON risk_alerts(level);
CREATE INDEX idx_risk_alerts_resolved ON risk_alerts(resolved);


-- ============================================================
-- EVENT LOG (append-only audit trail)
-- ============================================================

CREATE TABLE events (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type          VARCHAR(100) NOT NULL,           -- e.g. "trade.executed"
    branch_id           VARCHAR(100),                    -- NULL for fund-level events
    source              VARCHAR(100) NOT NULL,           -- Module that produced the event
    correlation_id      UUID,                            -- For tracing related events
    payload             JSONB NOT NULL,                  -- Full event data (Pydantic model → JSON)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_events_type ON events(event_type);
CREATE INDEX idx_events_branch ON events(branch_id);
CREATE INDEX idx_events_created ON events(created_at);
CREATE INDEX idx_events_correlation ON events(correlation_id) WHERE correlation_id IS NOT NULL;

-- Composite index for common query pattern: "recent events of type X for branch Y"
CREATE INDEX idx_events_type_branch_created ON events(event_type, branch_id, created_at DESC);
```

---

## 7. Module Layer Architecture

This section defines how the three layers (API routes, service, repository) interact
within each module and the rules for what logic belongs where.

### 7.1 Layer Responsibilities

```
┌─────────────────────────────────────────────────────────────────┐
│  API Routes (api.py)            Thin entry points               │
│  - Deserialize request          - No business logic             │
│  - Call service method           - No direct DB access           │
│  - Serialize response            - Input validation only         │
├─────────────────────────────────────────────────────────────────┤
│  Service Layer (service.py)     All business logic              │
│  - Orchestrates workflow        - Calls repositories for data   │
│  - Enforces business rules       - Logs events to event log      │
│  - Computes derived values       - Calls other modules directly  │
├─────────────────────────────────────────────────────────────────┤
│  Repository Layer (repository.py)   Data access only            │
│  - CRUD operations              - No business logic             │
│  - Query construction            - No event logging              │
│  - Pagination support            - No cross-module coordination  │
└─────────────────────────────────────────────────────────────────┘
```

No event handler layer needed -- there is no Kafka. Modules call each other's
service methods directly within the same process.

### 7.2 Transaction Boundaries

Service methods that modify state must execute within a single database transaction
to ensure atomicity between mutable state updates and event log inserts. All
repositories in the same call chain share the same `AsyncSession`, which is
committed once at the boundary (the API route or the calling service method).

```python
# app/db/connection.py

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

engine = create_async_engine(settings.database_url)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """
    Yields a single session per request. All repositories in the request
    share this session, so their operations are part of the same transaction.
    The session is committed on success, rolled back on exception.
    """
    async with async_session_factory() as session:
        async with session.begin():
            yield session
            # session.begin() context manager commits on exit, rolls back on exception
```

**Key rules:**
- Repositories receive the session via constructor injection (not via `Depends()` individually)
- The session's `begin()` context manager handles commit/rollback
- All repository calls within a single service method share the same session and transaction
- This means `order_repo.create()`, `trade_repo.create()`, `event_log.append()`, and
  `portfolio_service.handle_trade_executed()` all execute in the same transaction when
  called from `TradeExecutionService.submit_order()`

### 7.3 Entry Points: REST API and Direct Calls

Each module can be invoked two ways: via HTTP (for external clients / dashboards)
or via direct function call (from other modules in the same process). Both paths
execute the same service method.

```python
# Example: Trade Execution Module

# --- Entry point 1: REST API (for dashboards / external clients) ---
# app/modules/trade_execution/api.py

@router.post("/api/v1/orders", response_model=SubmitOrderResponse)
async def submit_order(
    req: SubmitOrderRequest,
    service: TradeExecutionService = Depends(get_trade_execution_service),
):
    result = await service.submit_order(OrderRequest(**req.model_dump()))
    return SubmitOrderResponse(
        order_id=result.order_id,
        status=result.status,
        message=result.message,
    )


# --- Entry point 2: Direct call from another module ---
# (e.g., a branch module calling trade execution after analysis)

# In branch module's service.py:
async def execute_trades(self, decisions: list[TradeDecision]):
    for decision in decisions:
        order_req = OrderRequest(
            branch_id=self.branch_id,
            instrument_id=decision.instrument_id,
            symbol=decision.symbol,
            side=decision.side,
            quantity=decision.quantity,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
        )
        # Direct function call -- same process, no HTTP overhead
        result = await self.trade_execution_service.submit_order(order_req)


# --- Shared business logic ---
# app/modules/trade_execution/service.py

class TradeExecutionService:
    def __init__(
        self,
        order_repo: OrderRepository,
        trade_repo: TradeRepository,
        broker: BrokerAdapter,
        event_log: EventLogRepository,
        portfolio_service: "PortfolioService",  # Direct reference
    ):
        self.order_repo = order_repo
        self.trade_repo = trade_repo
        self.broker = broker
        self.event_log = event_log
        self.portfolio_service = portfolio_service

    async def submit_order(self, req: OrderRequest) -> OrderResult:
        # 1. Persist order
        order = await self.order_repo.create(Order.from_request(req))

        # 2. Log intent before execution (audit trail)
        await self.event_log.append(
            TradeRequestedEvent.from_order_request(req, order.id)
        )

        # 3. Route to broker adapter
        result = await self.broker.submit_order(req)

        # 4. Update order status
        if result.success:
            await self.order_repo.update_status(
                order.id, OrderStatus.FILLED,
                filled_quantity=req.quantity,
                average_fill_price=result.trade.price,
            )
            await self.trade_repo.create(result.trade)

            # Log event for audit trail (append-only, not for coordination)
            await self.event_log.append(
                TradeExecutedEvent.from_trade(result.trade)
            )

            # Notify portfolio module directly (same process)
            await self.portfolio_service.handle_trade_executed(result.trade)
        else:
            await self.order_repo.update_status(
                order.id, OrderStatus.REJECTED,
                rejection_reason=result.rejection_reason,
            )
            await self.event_log.append(
                TradeRejectedEvent.from_order(order, result.rejection_reason)
            )

        return result
```

### 7.4 Dependency Injection (Single Composition Root)

All modules share a single `app/dependencies.py` that wires together repositories,
adapters, and service instances. FastAPI's `Depends()` is used for route-level injection.

**Lifecycle rules:**
- **Repositories**: Created per-request (they hold a session reference)
- **Services**: Created per-request (they hold repository references which are per-request)
- **Adapters and stateless singletons** (e.g., `PaperTradingAdapter`, `DataPlatformService`):
  Created once at startup and reused across all requests

All repositories within a single request receive the **same** `AsyncSession` instance,
ensuring they participate in the same database transaction (see Section 7.2).

```python
# app/dependencies.py  (single composition root for the entire app)

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.modules.portfolio.repository import PostgresPortfolioRepository, PostgresPositionRepository
from app.modules.portfolio.service import PortfolioService
from app.modules.trade_execution.repository import PostgresOrderRepository, PostgresTradeRepository
from app.modules.trade_execution.service import TradeExecutionService
from app.modules.trade_execution.adapters.paper import PaperTradingAdapter
from app.modules.data_platform.service import DataPlatformService
from app.modules.event_log.repository import PostgresEventLogRepository


# --- Singletons (created once at startup in app lifespan) ---
# These are stateless or hold only configuration; safe to share across requests.

_data_platform_service: DataPlatformService  # initialized in app lifespan
_paper_broker: PaperTradingAdapter           # initialized in app lifespan


def init_singletons(data_platform_service: DataPlatformService):
    """Called once from app lifespan (main.py) after adapter registry is configured."""
    global _data_platform_service, _paper_broker
    _data_platform_service = data_platform_service
    _paper_broker = PaperTradingAdapter(data_platform_service=data_platform_service)


# --- Per-request dependencies ---
# Each request gets one session; all repos share it for transactional consistency.

async def get_portfolio_service(
    session: AsyncSession = Depends(get_session),
) -> PortfolioService:
    return PortfolioService(
        portfolio_repo=PostgresPortfolioRepository(session),
        position_repo=PostgresPositionRepository(session),
        event_log=PostgresEventLogRepository(session),
    )


async def get_trade_execution_service(
    session: AsyncSession = Depends(get_session),
) -> TradeExecutionService:
    event_log = PostgresEventLogRepository(session)
    portfolio_service = PortfolioService(
        portfolio_repo=PostgresPortfolioRepository(session),
        position_repo=PostgresPositionRepository(session),
        event_log=event_log,
    )
    return TradeExecutionService(
        order_repo=PostgresOrderRepository(session),
        trade_repo=PostgresTradeRepository(session),
        broker=_paper_broker,
        event_log=event_log,
        portfolio_service=portfolio_service,
    )


async def get_data_platform_service() -> DataPlatformService:
    return _data_platform_service
```

---

## 8. Shared API Schemas

All services use common response wrappers for consistency. Defined in `common/schemas/`.

### 8.1 Error Response

Every service returns errors in this format. FastAPI exception handlers map domain
exceptions to the appropriate HTTP status code + `ErrorResponse` body.

```python
# common/schemas/responses.py

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """
    Consistent error response across all services.

    Examples:
      {"error": "insufficient_cash", "message": "Branch has $5,000 cash but order requires $12,000", "details": {"available": 5000, "required": 12000}}
      {"error": "instrument_not_found", "message": "No instrument with symbol XYZ", "details": null}
      {"error": "risk_limit_exceeded", "message": "Order would exceed max position size", "details": {"limit": 0.05, "would_be": 0.08}}
    """
    error: str              # Machine-readable error code (snake_case)
    message: str            # Human-readable explanation
    details: dict | None = None  # Optional structured context


# Standard HTTP status codes used:
#   400 - Bad request (validation, business rule violation)
#   404 - Resource not found
#   409 - Conflict (e.g., duplicate order, stale state)
#   422 - Unprocessable entity (valid JSON but invalid semantics)
#   429 - Rate limited
#   500 - Internal server error
#   503 - Service unavailable (dependency down)
```

### 8.2 Pagination

All list endpoints accept pagination parameters and return paginated responses.

```python
# common/schemas/pagination.py

from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    """
    Query parameters for paginated list endpoints.
    Used as a FastAPI dependency.
    """
    limit: int = Field(default=50, ge=1, le=500, description="Max items to return")
    offset: int = Field(default=0, ge=0, description="Number of items to skip")


class PaginatedResponse(BaseModel):
    """
    Wrapper for paginated list responses.
    Generic -- each endpoint specifies the item type in its own response model.
    """
    items: list           # list[T] -- typed in each endpoint's response model
    total: int            # Total matching items (for computing total pages)
    limit: int            # Echoed from request
    offset: int           # Echoed from request
    has_more: bool        # Convenience: offset + limit < total
```

### 8.3 Usage in Routes

```python
# Example: listing orders with pagination

from common.schemas.pagination import PaginationParams, PaginatedResponse

@router.get("/api/v1/orders", response_model=PaginatedResponse)
async def list_orders(
    branch_id: str | None = None,
    status: OrderStatus | None = None,
    since: datetime | None = None,
    pagination: PaginationParams = Depends(),
    service: TradeExecutionService = Depends(get_trade_execution_service),
):
    orders, total = await service.list_orders(
        branch_id=branch_id,
        status=status,
        since=since,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return PaginatedResponse(
        items=orders,
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
        has_more=(pagination.offset + pagination.limit) < total,
    )
```

---

## 9. Portfolio Module API

All modules are served by a single FastAPI process. Route prefixes organize endpoints by module.

### Endpoints

All endpoints are prefixed with `/api/v1`. Error responses use the shared `ErrorResponse` schema.
List endpoints support pagination via `limit` and `offset` query parameters.

```
GET    /health                                    Health check (no version prefix)

GET    /api/v1/portfolios/{branch_id}                    Get portfolio summary for a branch
GET    /api/v1/portfolios/{branch_id}/positions          List positions (paginated)
GET    /api/v1/portfolios/{branch_id}/positions/{symbol} Get single position detail

POST   /api/v1/portfolios                                Create a new portfolio for a branch
PUT    /api/v1/portfolios/{branch_id}/cash               Adjust cash (allocation changes)

GET    /api/v1/portfolios/{branch_id}/snapshots          List historical snapshots (paginated)
POST   /api/v1/portfolios/{branch_id}/snapshots          Trigger a snapshot now

GET    /api/v1/fund/summary                              Aggregate fund view (all branches)
GET    /api/v1/fund/snapshots                             Aggregate snapshot history (paginated)
```

### Request/Response Schemas

```python
# POST /api/v1/portfolios
class CreatePortfolioRequest(BaseModel):
    branch_id: str
    branch_type: str
    initial_cash: float
    margin_requirement: float = 0.0

class CreatePortfolioResponse(BaseModel):
    portfolio_id: str
    branch_id: str
    cash: float
    margin_requirement: float
    created_at: datetime


# GET /api/v1/portfolios/{branch_id}
class PortfolioResponse(BaseModel):
    portfolio_id: str
    branch_id: str
    branch_type: str
    cash: float
    allocated_capital: float
    margin_requirement: float
    margin_used: float
    nav: float
    total_long_exposure: float
    total_short_exposure: float
    gross_exposure: float
    net_exposure: float
    unrealized_pnl: float
    realized_pnl: float
    positions: list[PositionResponse]
    updated_at: datetime


class PositionResponse(BaseModel):
    instrument_id: str
    symbol: str
    long_quantity: float
    long_cost_basis: float
    short_quantity: float
    short_cost_basis: float
    short_margin_used: float
    realized_pnl_long: float
    realized_pnl_short: float
    # Computed (requires current market price from Data Platform)
    current_price: float | None = None
    unrealized_pnl: float | None = None
    market_value: float | None = None


# GET /api/v1/portfolios/{branch_id}/positions?limit=50&offset=0
# Response: PaginatedResponse with items: list[PositionResponse]


# PUT /api/v1/portfolios/{branch_id}/cash
class AdjustCashRequest(BaseModel):
    amount: float                        # Positive = deposit, negative = withdrawal
    reason: str                          # "allocation_increase", "allocation_decrease", "manual"


# GET /api/v1/portfolios/{branch_id}/snapshots?limit=30&offset=0
# Response: PaginatedResponse with items: list[PortfolioSnapshotResponse]

class PortfolioSnapshotResponse(BaseModel):
    snapshot_id: str
    portfolio_id: str
    branch_id: str
    cash: float
    nav: float
    total_long_exposure: float
    total_short_exposure: float
    gross_exposure: float
    net_exposure: float
    unrealized_pnl: float
    realized_pnl: float
    margin_used: float
    position_count: int
    snapshot_at: datetime


# GET /api/v1/fund/summary
class FundSummaryResponse(BaseModel):
    fund_id: str
    total_aum: float
    total_nav: float
    total_cash: float
    total_long_exposure: float
    total_short_exposure: float
    execution_mode: str
    branches: list[BranchSummary]

class BranchSummary(BaseModel):
    branch_id: str
    branch_type: str
    status: str
    allocated_capital: float
    nav: float
    cash: float
    unrealized_pnl: float
    realized_pnl: float
    position_count: int
```

### Called By Other Modules

The Portfolio module exposes service methods that other modules call directly:

| Caller | Method | Trigger |
|---|---|---|
| Trade Execution module | `portfolio_service.handle_trade_executed(trade)` | After a successful fill -- updates position, recalculates cash/margin/PnL, logs `PortfolioUpdatedEvent` |
| Central Orchestrator | `portfolio_service.handle_allocation_directive(directive)` | After allocation decision -- updates `allocated_capital`, adjusts cash |

No Kafka consumers or event handlers needed -- these are direct function calls within the same process.

---

## 10. Trade Execution Module API

### Endpoints

All endpoints are prefixed with `/api/v1`. Error responses use the shared `ErrorResponse` schema.
List endpoints support pagination via `limit` and `offset` query parameters.

```
GET    /health                                             Health check (no version prefix)

POST   /api/v1/orders                                      Submit a new order (sync alternative to event)
GET    /api/v1/orders/{order_id}                           Get order status and details
DELETE /api/v1/orders/{order_id}                           Cancel an order
GET    /api/v1/orders?branch_id=&status=&since=&limit=&offset=  List orders (paginated)

GET    /api/v1/trades?branch_id=&since=&limit=&offset=    List executed trades (paginated)
GET    /api/v1/trades/{trade_id}                           Get trade detail

GET    /api/v1/config/mode                                 Get current execution mode (paper/live)
PUT    /api/v1/config/mode                                 Set execution mode
```

### Request/Response Schemas

```python
# POST /api/v1/orders
class SubmitOrderRequest(BaseModel):
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    quantity: float
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    confidence: float | None = None
    reasoning: str | None = None
    agent_signals: dict | None = None

class SubmitOrderResponse(BaseModel):
    order_id: str
    status: OrderStatus
    message: str


# GET /api/v1/orders/{order_id}
class OrderResponse(BaseModel):
    order_id: str
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    limit_price: float | None
    stop_price: float | None
    time_in_force: TimeInForce
    status: OrderStatus
    filled_quantity: float
    average_fill_price: float
    commission: float
    rejection_reason: str | None
    created_at: datetime
    submitted_at: datetime | None
    filled_at: datetime | None


# GET /api/v1/orders?branch_id=X&status=filled&limit=50&offset=0
# Response: PaginatedResponse with items: list[OrderResponse]

# GET /api/v1/trades?branch_id=X&since=2024-01-01&limit=50&offset=0
# Response: PaginatedResponse with items: list[TradeResponse]

class TradeResponse(BaseModel):
    trade_id: str
    order_id: str
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    commission: float
    slippage: float
    execution_mode: ExecutionMode
    executed_at: datetime


# PUT /api/v1/config/mode
class SetExecutionModeRequest(BaseModel):
    mode: ExecutionMode                  # "paper" or "live"
    branch_id: str | None = None         # None = global, else per-branch override
```

### Called By Other Modules

Branch modules call `trade_execution_service.submit_order(order_request)` directly.
No Kafka consumer needed.

### Broker Adapter Interface

```python
# common/interfaces/broker.py

from abc import ABC, abstractmethod
from common.models.order import OrderRequest, Order
from common.models.trade import Trade


class OrderResult(BaseModel):
    success: bool
    order_id: str | None = None
    trade: Trade | None = None           # Populated on immediate fill
    rejection_reason: str | None = None


class AccountInfo(BaseModel):
    buying_power: float
    cash: float
    portfolio_value: float
    positions: list[dict]


class BrokerAdapter(ABC):
    """
    Abstract interface for broker integrations.
    Each broker (paper, Alpaca, IBKR, Coinbase, etc.) implements this.
    """

    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """Submit an order for execution. Returns immediately with result or pending status."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending/submitted order. Returns True if cancellation succeeded."""
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Order:
        """Get current status of an order."""
        ...

    @abstractmethod
    async def get_account_info(self) -> AccountInfo:
        """Get broker account information (balance, positions)."""
        ...

    @abstractmethod
    def supports_asset_class(self, asset_class: str) -> bool:
        """Whether this adapter can handle the given asset class."""
        ...
```

### Paper Trading Adapter

```python
# app/modules/trade_execution/adapters/paper.py

class PaperTradingAdapter(BrokerAdapter):
    """
    Simulates trade execution for paper trading mode.

    Behavior:
    - Market orders: Fill immediately at current price + configurable slippage
    - Limit orders: Fill if current price meets limit condition
    - Uses Data Platform module to fetch current prices
    - Configurable commission model (flat fee, per-share, percentage)
    - Configurable slippage model (fixed bps, random within range, volume-based)
    """

    def __init__(
        self,
        data_platform_service: "DataPlatformService",  # Direct reference, not URL
        slippage_bps: float = 5.0,       # Default: 5 basis points slippage
        commission_per_trade: float = 0.0, # Default: zero commission
    ):
        ...

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """
        1. Fetch current price from Data Platform module (direct call)
        2. Apply slippage model to determine fill price
        3. Calculate commission
        4. Return immediate fill result (paper mode = no partial fills)
        """
        ...

    def supports_asset_class(self, asset_class: str) -> bool:
        return True  # Paper adapter supports all asset classes
```

---

## 11. Data Platform Module API

### Endpoints

All endpoints are prefixed with `/api/v1`. Error responses use the shared `ErrorResponse` schema.
List endpoints support pagination via `limit` and `offset` query parameters.
Multi-word URL segments use kebab-case (e.g., `line-items`, `insider-trades`).

```
GET    /health                                             Health check (no version prefix)

# Price data
GET    /api/v1/prices/{symbol}                             Get price bars
       ?start_date=&end_date=&interval=day

# Fundamentals (equities)
GET    /api/v1/fundamentals/{symbol}/metrics               Financial metrics
       ?period=ttm&limit=10&offset=0
GET    /api/v1/fundamentals/{symbol}/line-items              Specific line items
       ?items=net_income,capex&period=ttm&limit=10&offset=0
GET    /api/v1/fundamentals/{symbol}/facts                 Company facts

# News (paginated)
GET    /api/v1/news                                        News articles
       ?symbols=AAPL,MSFT&since=2024-01-01&limit=100&offset=0

# Insider data (paginated)
GET    /api/v1/insider-trades/{symbol}                     Insider transactions
       ?start_date=&end_date=&limit=100&offset=0

# Macro indicators (bonds/rates, future use)
GET    /api/v1/macro/{indicator}                           Macro data (FRED, etc.)
       ?start_date=&end_date=

# Market data (crypto, future use)
GET    /api/v1/crypto/{symbol}                             Crypto-specific data
       ?start_date=&end_date=

# Metadata (paginated where applicable)
GET    /api/v1/instruments/search                          Search instruments
       ?query=&asset_class=&exchange=&limit=50&offset=0
GET    /api/v1/instruments/{symbol}                        Get instrument details
PUT    /api/v1/instruments                                 Upsert instrument (create or update by symbol+asset_class+exchange)
GET    /api/v1/sources                                     List available data sources and status
```

### Response Schemas

```python
# GET /api/v1/prices/{symbol}
class PriceBar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

class PriceResponse(BaseModel):
    symbol: str
    interval: str
    bars: list[PriceBar]
    source: str                          # Which adapter served this data


# GET /api/v1/fundamentals/{symbol}/metrics
class FinancialMetric(BaseModel):
    """Same fields as existing FinancialMetrics model in src/data/models.py"""
    report_period: str
    period: str
    currency: str
    # ... all 40+ metric fields from the existing model ...
    model_config = {"extra": "allow"}    # Allow additional fields per source

class MetricsResponse(BaseModel):
    symbol: str
    metrics: list[FinancialMetric]
    source: str


# GET /api/v1/fundamentals/{symbol}/line-items
class LineItemResponse(BaseModel):
    symbol: str
    items: list[dict]                    # Flexible schema (extra="allow")
    source: str


# GET /api/v1/news
class NewsArticle(BaseModel):
    title: str
    author: str | None
    source: str
    published_at: datetime
    url: str
    symbols: list[str]
    sentiment: str | None = None         # If available from source

class NewsResponse(BaseModel):
    articles: list[NewsArticle]
    source: str


# GET /api/v1/macro/{indicator}
class MacroDataPoint(BaseModel):
    date: str
    value: float

class MacroResponse(BaseModel):
    indicator: str
    description: str
    unit: str
    data: list[MacroDataPoint]
    source: str


# PUT /api/v1/instruments
class UpsertInstrumentRequest(BaseModel):
    """
    Create or update an instrument. Matched by (symbol, asset_class, exchange).
    Used by data adapters when they encounter a new symbol, and by admin operations.
    """
    symbol: str
    name: str
    asset_class: str                     # AssetClass enum value
    exchange: str | None = None
    currency: str = "USD"
    is_active: bool = True
    metadata: dict | None = None         # Asset-class-specific fields

class InstrumentResponse(BaseModel):
    instrument_id: str
    symbol: str
    name: str
    asset_class: str
    exchange: str | None
    currency: str
    is_active: bool
    metadata: dict | None
    created_at: datetime
```

### Source Adapter Interfaces

```python
# common/interfaces/price_data.py

from abc import ABC, abstractmethod
from datetime import date


class PriceDataAdapter(ABC):
    """Adapter for fetching price/OHLCV data."""

    @abstractmethod
    async def get_prices(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "day",
    ) -> list[PriceBar]:
        ...

    @abstractmethod
    def supported_asset_classes(self) -> list[str]:
        """Which asset classes this adapter can serve price data for."""
        ...


# common/interfaces/fundamentals.py

class FundamentalsAdapter(ABC):
    """Adapter for fetching financial fundamentals."""

    @abstractmethod
    async def get_metrics(
        self,
        symbol: str,
        end_date: date,
        period: str = "ttm",
        limit: int = 10,
    ) -> list[dict]:
        ...

    @abstractmethod
    async def search_line_items(
        self,
        symbol: str,
        items: list[str],
        end_date: date,
        period: str = "ttm",
        limit: int = 10,
    ) -> list[dict]:
        ...

    @abstractmethod
    async def get_company_facts(self, symbol: str) -> dict:
        ...


# common/interfaces/news.py

class NewsAdapter(ABC):
    """Adapter for fetching news articles."""

    @abstractmethod
    async def get_news(
        self,
        symbols: list[str] | None = None,
        query: str | None = None,
        since: date | None = None,
        limit: int = 100,
    ) -> list[NewsArticle]:
        ...


# common/interfaces/macro.py

class MacroAdapter(ABC):
    """Adapter for fetching macroeconomic indicators."""

    @abstractmethod
    async def get_indicator(
        self,
        indicator: str,
        start_date: date,
        end_date: date,
    ) -> list[MacroDataPoint]:
        ...

    @abstractmethod
    def available_indicators(self) -> list[str]:
        ...
```

### Adapter Routing and Fallback

```python
# app/modules/data_platform/service.py

class DataPlatformService:
    """
    Routes data requests to the appropriate adapter based on asset class.
    Falls back to alternate adapters on failure.

    Adapter registry example:
      prices:
        equity:     [YahooFinanceAdapter, FinancialDatasetsAdapter (future)]
        crypto:     [YahooFinanceAdapter, CoinGeckoAdapter (future)]
        commodity:  [QuandlAdapter (future)]
      fundamentals:
        equity:     [YahooFinanceAdapter, FinancialDatasetsAdapter (future)]
      news:
        all:        [YahooFinanceAdapter]
      macro:
        all:        [FREDAdapter (future)]
    """

    def __init__(self, adapter_registry: dict):
        self.registry = adapter_registry
        self.cache = TTLCache(maxsize=1000, ttl=60)  # cachetools in-memory cache
        self.rate_limiter = RateLimiter(...)

    async def get_prices(self, symbol: str, asset_class: str, **kwargs) -> PriceResponse:
        cache_key = f"prices:{symbol}:{kwargs}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        adapters = self.registry["prices"].get(asset_class, [])
        for adapter in adapters:
            try:
                await self.rate_limiter.acquire(adapter.name)
                result = await adapter.get_prices(symbol, **kwargs)
                self.cache[cache_key] = result  # TTL handled by cachetools
                return PriceResponse(symbol=symbol, bars=result, source=adapter.name)
            except Exception:
                continue  # Try next adapter

        raise DataUnavailableError(f"No adapter could serve prices for {symbol}")
```

---

## 12. Infrastructure Configuration

### docker-compose.yml (local development)

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: hedgefund
      POSTGRES_USER: hedgefund
      POSTGRES_PASSWORD: localdev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./infrastructure/db/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U hedgefund"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

That's it. No Kafka, no Redis, no separate service containers. The FastAPI app runs
directly on the host during development (`uvicorn app.main:app --reload`) and connects
to PostgreSQL in Docker.

For containerized deployment, a single `Dockerfile` builds the entire app:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install .
COPY app/ app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 13. Module Communication Summary

All communication between modules is via direct Python function calls within the same process.
The event log is written to for audit purposes but is not used for inter-module communication.

```
Caller                      Callee                        Method
──────                      ──────                        ──────
Branch Module
  → Data Platform module    data_platform_service         .get_prices(), .get_metrics(), .get_news()
  → Trade Execution module  trade_execution_service       .submit_order()
  → Portfolio module        portfolio_service             .get_portfolio(), .get_positions()

Trade Execution module
  → Data Platform module    data_platform_service         .get_prices() (for paper trading fill price)
  → Portfolio module        portfolio_service             .handle_trade_executed()

Central Orchestrator (Phase 3)
  → Portfolio module        portfolio_service             .get_fund_summary(), .get_snapshots()
  → Data Platform module    data_platform_service         .get_macro_indicator()
  → Branch modules          branch_service                .handle_allocation_directive()
  → Event Log module        event_log_service             .query() (for recent risk alerts)
```

All modules also call `event_log_service.append(event)` to log events for auditability,
but this is write-only -- modules do NOT read from the event log for real-time
coordination (they call each other directly instead).

---

## 14. Phase 1 Verification Tests

These are the tests that prove Phase 1 is working. Organized by what they verify,
not by module — a passing test suite here means the shared infrastructure is ready
for Phase 2 (Equities Branch).

### 14.1 Infrastructure

| Test | Verifies |
|------|----------|
| App starts, `/health` returns 200 | FastAPI + DB connection + Alembic migrations applied |
| All tables exist with correct columns | Schema migration ran cleanly |

### 14.2 Portfolio Module

| Test | Verifies |
|------|----------|
| Create portfolio for a branch, retrieve it | Basic CRUD, DB round-trip |
| Create portfolio with initial cash, verify NAV = cash (no positions) | NAV calculation baseline |
| Adjust cash up/down, verify new cash balance | `update_cash` works, negative withdrawals rejected if insufficient |
| Take a snapshot, retrieve it from snapshot history | Snapshot creation and pagination |
| Get fund summary with multiple branches | Aggregate view computes correctly across branches |

### 14.3 Trade Execution Module (Paper Trading)

| Test | Verifies |
|------|----------|
| Submit a BUY market order, get back a filled result | Paper adapter fetches price, applies slippage, returns fill |
| Submit a SELL for a position you hold, verify fill | Sell-side execution works |
| Submit a BUY with insufficient cash, get rejection | Order validation catches insufficient funds |
| Submit a LIMIT order above current price (buy), verify rejection or pending | Limit order logic in paper adapter |
| Verify order record persists with correct status and fill price | Order repository write + read |
| Verify trade record persists with slippage and commission | Trade repository write + read |
| List orders with filters (branch, status, since), verify pagination | Query filtering and pagination |

### 14.4 Portfolio + Trade Execution Integration

| Test | Verifies |
|------|----------|
| BUY 100 shares → position created with correct quantity and cost basis | `handle_trade_executed` creates position |
| BUY 100 then SELL 50 → position quantity reduced, realized P&L computed | Partial close calculates P&L |
| BUY 100 then SELL 100 → position removed (flat), realized P&L recorded | Full close cleans up position |
| SHORT 100 → short position created, margin reserved | Short selling works, margin accounting |
| SHORT 100 then COVER 100 → flat, margin released, P&L computed | Short cover lifecycle |
| BUY → verify cash decreased by (fill_price * quantity + commission) | Cash accounting on buy |
| SELL → verify cash increased by (fill_price * quantity - commission) | Cash accounting on sell |
| After any trade → NAV, exposure, and P&L fields updated on portfolio | Aggregate recomputation |
| After any trade → `PortfolioUpdatedEvent` in event log | Event logging on state change |
| Fractional quantity (0.5 shares, 0.001 BTC) → correct position and P&L | NUMERIC(18,8) works end-to-end |

### 14.5 Event Log

| Test | Verifies |
|------|----------|
| Append event, query it back by type | Basic write + read |
| Query events filtered by branch_id, event_type, since | Filtering works |
| Submit order → `trade.requested` event logged before execution | Event ordering (intent before outcome) |
| Successful fill → `trade.executed` event logged | Execution events |
| Rejected order → `trade.rejected` event logged with reason | Rejection events |
| Event payloads deserialize back to their Pydantic models | Schema consistency (JSON ↔ Pydantic) |

### 14.6 Data Platform Module

| Test | Verifies |
|------|----------|
| Get prices for a known equity ticker, receive OHLCV bars | YahooFinanceAdapter works |
| Get financial metrics for a ticker | Fundamentals adapter works (via yfinance) |
| Get news articles | News adapter works (via yfinance) |
| Request with invalid symbol → meaningful error | Error handling, not a 500 |
| Second request for same symbol within TTL → served from cache (no API call) | In-memory cache works |
| Request after TTL expires → fresh API call | Cache expiration works |
| Upsert instrument → created on first call, updated on second | Instrument creation/update |

### 14.7 Transaction Atomicity

| Test | Verifies |
|------|----------|
| Force an error after order creation but before trade creation → order rolled back | Single transaction, no partial state |
| Force an error in `handle_trade_executed` → trade and order both rolled back | Cross-module atomicity within one session |
| Successful trade → order, trade, position update, and event log all committed together | Happy path atomicity |

### 14.8 End-to-End Smoke Test

A single test that exercises the full Phase 1 stack top-to-bottom:

```
1. Start app (FastAPI + PostgreSQL)
2. Create a fund and an equities branch
3. Create a portfolio for the branch with $100,000 cash
4. Upsert instrument AAPL
5. Fetch AAPL price via Data Platform
6. Submit BUY order for 10 shares of AAPL via Trade Execution
7. Verify: order status = FILLED
8. Verify: position exists with quantity=10, cost_basis ≈ fill_price * 10
9. Verify: portfolio cash decreased, NAV ≈ $100,000 (minus slippage/commission)
10. Verify: event log contains trade.requested + trade.executed events
11. Take a portfolio snapshot
12. Submit SELL order for 5 shares of AAPL
13. Verify: position quantity = 5, realized P&L computed
14. Verify: portfolio cash increased
15. Fetch portfolio summary → all fields consistent
16. Fetch fund summary → aggregates across the one branch
```

If this passes, Phase 1 is done and the infrastructure is ready for branch modules.

### 14.9 Test Infrastructure

| Concern | Approach |
|---------|----------|
| Database | Testcontainers (disposable PostgreSQL per test session) or a dedicated test database with per-test transaction rollback |
| Data Platform | Mock adapters for unit/integration tests; real API calls only in a dedicated "live adapter" test suite (skipped in CI by default) |
| Test client | `httpx.AsyncClient` with FastAPI's `TestClient` for API-level tests |
| Fixtures | Factory functions for creating funds, branches, portfolios, instruments, and orders with sensible defaults |

---

## 15. Compatibility with Existing Repo

This is a new repo (`ai-hedgefund-final`). The original repo lives at
`/Users/franco_lu/desktop/ai-hedge-fund` and is kept as a reference for
agent logic, data models, and API patterns. Nothing is imported from it
during Phase 1 — it becomes relevant in Phase 2 when we adapt the existing
agents into branch modules.

**Reference mapping** (original repo → this repo's Phase 1 equivalents):

| Original Repo (`ai-hedge-fund`) | This Repo (`ai-hedgefund-final`) |
|---|---|
| `src/data/models.py` (Price, FinancialMetrics, etc.) | `common/models/` + Data Platform response schemas |
| `src/backtesting/types.py` (Action, PositionState) | `common/enums.py` (OrderSide) + `common/models/position.py` |
| `src/backtesting/portfolio.py` (Portfolio class) | Portfolio module (server-side state management) |
| `src/backtesting/trader.py` (TradeExecutor) | Trade Execution module |
| `src/tools/api.py` (get_prices, etc.) | Data Platform module + YahooFinanceAdapter (FinancialDatasetsAdapter added later) |
| `src/graph/state.py` (AgentState) | Adapted in Phase 2 within branch modules |
| `app/backend/database/` (SQLite ORM) | PostgreSQL schema (this document) |