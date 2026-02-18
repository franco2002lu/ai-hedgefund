# Phase 1: Shared Infrastructure -- Service Interfaces & Data Models

This document defines the concrete service interfaces, data models, event schemas, and
project structure for the shared infrastructure layer that all branch services depend on.

---

## 1. Project Structure

Monorepo layout. Each service is a separate Python package with its own FastAPI app,
but shares common models via the `common` library.

```
ai-hedge-fund/
├── services/
│   ├── common/                          # Shared library (installed as editable dep)
│   │   ├── pyproject.toml
│   │   └── common/
│   │       ├── __init__.py
│   │       ├── enums.py                 # Shared enumerations
│   │       ├── events/                  # Event schemas (Pydantic)
│   │       │   ├── __init__.py
│   │       │   ├── base.py              # BaseEvent
│   │       │   ├── trade.py             # Trade lifecycle events
│   │       │   ├── portfolio.py         # Portfolio events
│   │       │   ├── allocation.py        # Allocation events
│   │       │   ├── risk.py              # Risk alert events
│   │       │   └── signal.py            # Agent signal events
│   │       ├── models/                  # Domain models
│   │       │   ├── __init__.py
│   │       │   ├── instrument.py        # Instrument model
│   │       │   ├── position.py          # Position model
│   │       │   ├── portfolio.py         # Portfolio model
│   │       │   ├── order.py             # Order model
│   │       │   └── trade.py             # Trade (fill) model
│   │       ├── interfaces/              # Abstract adapter interfaces
│   │       │   ├── __init__.py
│   │       │   ├── broker.py            # BrokerAdapter ABC
│   │       │   ├── price_data.py        # PriceDataAdapter ABC
│   │       │   ├── fundamentals.py      # FundamentalsAdapter ABC
│   │       │   ├── news.py              # NewsAdapter ABC
│   │       │   └── macro.py             # MacroAdapter ABC
│   │       ├── kafka/                   # Kafka producer/consumer helpers
│   │       │   ├── __init__.py
│   │       │   ├── producer.py
│   │       │   └── consumer.py
│   │       └── config.py                # Shared configuration (env vars, defaults)
│   │
│   ├── portfolio-service/               # Portfolio Service
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── portfolio_service/
│   │       ├── __init__.py
│   │       ├── main.py                  # FastAPI app
│   │       ├── api/
│   │       │   ├── __init__.py
│   │       │   ├── portfolios.py        # Portfolio endpoints
│   │       │   ├── positions.py         # Position endpoints
│   │       │   └── snapshots.py         # Snapshot endpoints
│   │       ├── db/
│   │       │   ├── __init__.py
│   │       │   ├── connection.py        # SQLAlchemy engine + session
│   │       │   ├── models.py            # ORM models
│   │       │   └── migrations/          # Alembic migrations
│   │       ├── event_handlers.py        # Kafka event consumers
│   │       └── service.py               # Business logic
│   │
│   ├── trade-execution-service/         # Trade Execution Service
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── trade_execution_service/
│   │       ├── __init__.py
│   │       ├── main.py                  # FastAPI app
│   │       ├── api/
│   │       │   ├── __init__.py
│   │       │   └── orders.py            # Order endpoints
│   │       ├── adapters/
│   │       │   ├── __init__.py
│   │       │   ├── paper.py             # PaperTradingAdapter
│   │       │   ├── alpaca.py            # AlpacaAdapter (future)
│   │       │   └── coinbase.py          # CoinbaseAdapter (future)
│   │       ├── event_handlers.py        # Kafka event consumers
│   │       └── service.py               # Order routing + validation logic
│   │
│   ├── data-platform-service/           # Data Platform Service
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── data_platform_service/
│   │       ├── __init__.py
│   │       ├── main.py                  # FastAPI app
│   │       ├── api/
│   │       │   ├── __init__.py
│   │       │   ├── prices.py            # Price data endpoints
│   │       │   ├── fundamentals.py      # Fundamentals endpoints
│   │       │   ├── news.py              # News endpoints
│   │       │   └── macro.py             # Macro indicator endpoints
│   │       ├── adapters/
│   │       │   ├── __init__.py
│   │       │   ├── financial_datasets.py  # FinancialDatasetsAdapter
│   │       │   ├── yahoo_finance.py       # YahooFinanceAdapter (future)
│   │       │   ├── fred.py                # FREDAdapter (future)
│   │       │   └── coingecko.py           # CoinGeckoAdapter (future)
│   │       ├── cache.py                 # Redis cache layer
│   │       ├── rate_limiter.py          # Per-source rate limiting
│   │       └── service.py               # Routing + fallback logic
│   │
│   └── central-orchestrator/            # Central Orchestration (Phase 3, stubbed)
│       └── ...
│
├── infrastructure/
│   ├── docker-compose.yml               # Local dev: Kafka, PostgreSQL, Redis
│   ├── kafka/
│   │   └── topics.yml                   # Topic definitions
│   └── db/
│       └── init.sql                     # Initial schema (also managed by Alembic)
│
├── plans/                               # Architecture docs
│   └── architecture/
│       ├── HIGH-LEVEL-ARCH.md
│       └── PHASE1-SHARED-INFRASTRUCTURE.md  (this file)
│
└── src/                                 # Existing repo (reference, not modified)
```

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

    long_quantity: int = 0
    long_cost_basis: float = 0.0         # Total cost of long position

    short_quantity: int = 0
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

    positions: list                      # list[Position] -- populated on detail requests
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
    quantity: int
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
    quantity: int
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce

    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
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
    quantity: int
    price: float                         # Fill price
    commission: float = 0.0
    slippage: float = 0.0                # Difference from mid-price at time of fill

    execution_mode: ExecutionMode        # paper or live
    executed_at: datetime
```

---

## 4. Event Schemas

### 4.1 Kafka Topic Design

| Topic Name | Key | Partitioned By | Producers | Consumers |
|---|---|---|---|---|
| `trade.requested` | `branch_id` | branch | Branch services | Trade Execution Service |
| `trade.executed` | `branch_id` | branch | Trade Execution Service | Portfolio Service, Branch services |
| `trade.rejected` | `branch_id` | branch | Trade Execution Service | Branch services |
| `order.status_changed` | `order_id` | order | Trade Execution Service | Branch services |
| `portfolio.updated` | `branch_id` | branch | Portfolio Service | Central Orchestrator, Branch services |
| `portfolio.snapshot` | `branch_id` | branch | Portfolio Service | Central Orchestrator |
| `allocation.directive` | `branch_id` | branch | Central Orchestrator | Branch services, Portfolio Service |
| `risk.alert` | `branch_id` | branch | Global Risk Manager | Central Orchestrator, Branch services |
| `signal.generated` | `branch_id` | branch | Branch services (agents) | Logging, Analytics |

All events are serialized as JSON. Schema registry (e.g., Confluent Schema Registry) is
recommended for production but not required for MVP.

### 4.2 Base Event

```python
# common/events/base.py

from pydantic import BaseModel, Field
from datetime import datetime
import uuid


class BaseEvent(BaseModel):
    """All events inherit from this."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str                      # e.g. "trade.requested"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
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
    quantity: int
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
    quantity: int
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
    quantity: int
    rejection_reason: str                # e.g. "insufficient_cash", "risk_limit_exceeded"


class OrderStatusChangedEvent(BaseEvent):
    event_type: str = "order.status_changed"

    order_id: str
    branch_id: str
    previous_status: OrderStatus
    new_status: OrderStatus
    filled_quantity: int = 0
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

## 5. PostgreSQL Schema

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

    long_quantity       INTEGER NOT NULL DEFAULT 0,
    long_cost_basis     NUMERIC(18, 2) NOT NULL DEFAULT 0,

    short_quantity      INTEGER NOT NULL DEFAULT 0,
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
    quantity            INTEGER NOT NULL,
    limit_price         NUMERIC(18, 6),
    stop_price          NUMERIC(18, 6),
    time_in_force       VARCHAR(10) NOT NULL DEFAULT 'day',

    status              VARCHAR(30) NOT NULL DEFAULT 'pending',
    filled_quantity     INTEGER NOT NULL DEFAULT 0,
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
    quantity            INTEGER NOT NULL,
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
```

---

## 6. Portfolio Service API

Base URL: `http://portfolio-service:8001`

### Endpoints

```
GET    /health                                    Health check

GET    /portfolios/{branch_id}                    Get portfolio summary for a branch
GET    /portfolios/{branch_id}/positions          List all positions for a branch
GET    /portfolios/{branch_id}/positions/{symbol} Get single position detail

POST   /portfolios                                Create a new portfolio for a branch
PUT    /portfolios/{branch_id}/cash               Adjust cash (allocation changes)

GET    /portfolios/{branch_id}/snapshots          List historical snapshots
POST   /portfolios/{branch_id}/snapshots          Trigger a snapshot now

GET    /fund/summary                              Aggregate fund view (all branches)
GET    /fund/snapshots                             Aggregate snapshot history
```

### Request/Response Schemas

```python
# POST /portfolios
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


# GET /portfolios/{branch_id}
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
    long_quantity: int
    long_cost_basis: float
    short_quantity: int
    short_cost_basis: float
    short_margin_used: float
    realized_pnl_long: float
    realized_pnl_short: float
    # Computed (requires current market price from Data Platform)
    current_price: float | None = None
    unrealized_pnl: float | None = None
    market_value: float | None = None


# PUT /portfolios/{branch_id}/cash
class AdjustCashRequest(BaseModel):
    amount: float                        # Positive = deposit, negative = withdrawal
    reason: str                          # "allocation_increase", "allocation_decrease", "manual"


# GET /fund/summary
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

### Event Handlers

The Portfolio Service subscribes to:

| Event | Topic | Action |
|---|---|---|
| `TradeExecutedEvent` | `trade.executed` | Update position, recalculate cash/margin/PnL, emit `PortfolioUpdatedEvent` |
| `AllocationDirectiveEvent` | `allocation.directive` | Update `allocated_capital` on branch, adjust cash if directed |

---

## 7. Trade Execution Service API

Base URL: `http://trade-execution-service:8002`

### Endpoints

```
GET    /health                                    Health check

POST   /orders                                    Submit a new order (sync alternative to event)
GET    /orders/{order_id}                         Get order status and details
DELETE /orders/{order_id}                         Cancel an order
GET    /orders?branch_id=&status=&since=          List orders with filters

GET    /trades?branch_id=&since=&limit=           List executed trades
GET    /trades/{trade_id}                         Get trade detail

GET    /config/mode                               Get current execution mode (paper/live)
PUT    /config/mode                               Set execution mode
```

### Request/Response Schemas

```python
# POST /orders
class SubmitOrderRequest(BaseModel):
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    quantity: int
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


# GET /orders/{order_id}
class OrderResponse(BaseModel):
    order_id: str
    branch_id: str
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    limit_price: float | None
    stop_price: float | None
    time_in_force: TimeInForce
    status: OrderStatus
    filled_quantity: int
    average_fill_price: float
    commission: float
    rejection_reason: str | None
    created_at: datetime
    submitted_at: datetime | None
    filled_at: datetime | None


# PUT /config/mode
class SetExecutionModeRequest(BaseModel):
    mode: ExecutionMode                  # "paper" or "live"
    branch_id: str | None = None         # None = global, else per-branch override
```

### Event Handlers

| Event | Topic | Action |
|---|---|---|
| `TradeRequestedEvent` | `trade.requested` | Validate order, route to broker adapter, emit result |

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
# trade_execution_service/adapters/paper.py

class PaperTradingAdapter(BrokerAdapter):
    """
    Simulates trade execution for paper trading mode.

    Behavior:
    - Market orders: Fill immediately at current price + configurable slippage
    - Limit orders: Fill if current price meets limit condition
    - Uses Data Platform Service to fetch current prices
    - Configurable commission model (flat fee, per-share, percentage)
    - Configurable slippage model (fixed bps, random within range, volume-based)
    """

    def __init__(
        self,
        data_platform_url: str,
        slippage_bps: float = 5.0,       # Default: 5 basis points slippage
        commission_per_trade: float = 0.0, # Default: zero commission
    ):
        ...

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """
        1. Fetch current price from Data Platform Service
        2. Apply slippage model to determine fill price
        3. Calculate commission
        4. Return immediate fill result (paper mode = no partial fills)
        """
        ...

    def supports_asset_class(self, asset_class: str) -> bool:
        return True  # Paper adapter supports all asset classes
```

---

## 8. Data Platform Service API

Base URL: `http://data-platform-service:8003`

### Endpoints

```
GET    /health                                    Health check

# Price data
GET    /prices/{symbol}                           Get price bars
       ?start_date=&end_date=&interval=day

# Fundamentals (equities)
GET    /fundamentals/{symbol}/metrics             Financial metrics
       ?period=ttm&limit=10
GET    /fundamentals/{symbol}/line-items           Specific line items
       ?items=net_income,capex&period=ttm&limit=10
GET    /fundamentals/{symbol}/facts               Company facts

# News
GET    /news                                      News articles
       ?symbols=AAPL,MSFT&since=2024-01-01&limit=100

# Insider data
GET    /insider-trades/{symbol}                   Insider transactions
       ?start_date=&end_date=&limit=1000

# Macro indicators (bonds/rates, future use)
GET    /macro/{indicator}                         Macro data (FRED, etc.)
       ?start_date=&end_date=

# Market data (crypto, future use)
GET    /crypto/{symbol}                           Crypto-specific data
       ?start_date=&end_date=

# Metadata
GET    /instruments/search                        Search instruments
       ?query=&asset_class=&exchange=
GET    /instruments/{symbol}                      Get instrument details
GET    /sources                                   List available data sources and status
```

### Response Schemas

```python
# GET /prices/{symbol}
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


# GET /fundamentals/{symbol}/metrics
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


# GET /fundamentals/{symbol}/line-items
class LineItemResponse(BaseModel):
    symbol: str
    items: list[dict]                    # Flexible schema (extra="allow")
    source: str


# GET /news
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


# GET /macro/{indicator}
class MacroDataPoint(BaseModel):
    date: str
    value: float

class MacroResponse(BaseModel):
    indicator: str
    description: str
    unit: str
    data: list[MacroDataPoint]
    source: str
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
# data_platform_service/service.py

class DataPlatformService:
    """
    Routes data requests to the appropriate adapter based on asset class.
    Falls back to alternate adapters on failure.

    Adapter registry example:
      prices:
        equity:     [FinancialDatasetsAdapter, YahooFinanceAdapter]
        crypto:     [CoinGeckoAdapter]
        commodity:  [QuandlAdapter]
      fundamentals:
        equity:     [FinancialDatasetsAdapter]
      news:
        all:        [FinancialDatasetsAdapter]
      macro:
        all:        [FREDAdapter]
    """

    def __init__(self, adapter_registry: dict):
        self.registry = adapter_registry
        self.cache = RedisCache(...)
        self.rate_limiter = RateLimiter(...)

    async def get_prices(self, symbol: str, asset_class: str, **kwargs) -> PriceResponse:
        cache_key = f"prices:{symbol}:{kwargs}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        adapters = self.registry["prices"].get(asset_class, [])
        for adapter in adapters:
            try:
                await self.rate_limiter.acquire(adapter.name)
                result = await adapter.get_prices(symbol, **kwargs)
                await self.cache.set(cache_key, result, ttl=60)  # 1 min for prices
                return PriceResponse(symbol=symbol, bars=result, source=adapter.name)
            except Exception:
                continue  # Try next adapter

        raise DataUnavailableError(f"No adapter could serve prices for {symbol}")
```

---

## 9. Infrastructure Configuration

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

  kafka:
    image: confluentinc/cp-kafka:7.6.0
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      CLUSTER_ID: "local-dev-cluster-001"
    ports:
      - "9092:9092"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  # -- Services --

  portfolio-service:
    build:
      context: .
      dockerfile: services/portfolio-service/Dockerfile
    environment:
      DATABASE_URL: postgresql://hedgefund:localdev@postgres:5432/hedgefund
      KAFKA_BOOTSTRAP_SERVERS: kafka:9092
    ports:
      - "8001:8001"
    depends_on:
      - postgres
      - kafka

  trade-execution-service:
    build:
      context: .
      dockerfile: services/trade-execution-service/Dockerfile
    environment:
      DATABASE_URL: postgresql://hedgefund:localdev@postgres:5432/hedgefund
      KAFKA_BOOTSTRAP_SERVERS: kafka:9092
      DATA_PLATFORM_URL: http://data-platform-service:8003
      EXECUTION_MODE: paper
    ports:
      - "8002:8002"
    depends_on:
      - postgres
      - kafka
      - data-platform-service

  data-platform-service:
    build:
      context: .
      dockerfile: services/data-platform-service/Dockerfile
    environment:
      REDIS_URL: redis://redis:6379/0
      FINANCIAL_DATASETS_API_KEY: ${FINANCIAL_DATASETS_API_KEY}
    ports:
      - "8003:8003"
    depends_on:
      - redis

volumes:
  pgdata:
```

### Kafka Topic Initialization

```yaml
# infrastructure/kafka/topics.yml
# Apply via a startup script or kafka-topics CLI

topics:
  - name: trade.requested
    partitions: 6
    replication_factor: 1
    config:
      retention.ms: 604800000           # 7 days

  - name: trade.executed
    partitions: 6
    replication_factor: 1
    config:
      retention.ms: 2592000000          # 30 days

  - name: trade.rejected
    partitions: 6
    replication_factor: 1
    config:
      retention.ms: 2592000000          # 30 days

  - name: order.status_changed
    partitions: 6
    replication_factor: 1
    config:
      retention.ms: 604800000           # 7 days

  - name: portfolio.updated
    partitions: 6
    replication_factor: 1
    config:
      retention.ms: 604800000           # 7 days

  - name: portfolio.snapshot
    partitions: 6
    replication_factor: 1
    config:
      retention.ms: -1                  # Retain forever (historical record)

  - name: allocation.directive
    partitions: 1                       # Low volume, ordering matters
    replication_factor: 1
    config:
      retention.ms: -1                  # Retain forever

  - name: risk.alert
    partitions: 3
    replication_factor: 1
    config:
      retention.ms: 7776000000          # 90 days

  - name: signal.generated
    partitions: 6
    replication_factor: 1
    config:
      retention.ms: 2592000000          # 30 days
```

---

## 10. Service Communication Summary

```
                            Sync (REST/gRPC)         Async (Kafka)
                            ────────────────         ─────────────
Branch Service
  → Data Platform Service   GET /prices, etc.        -
  → Trade Execution Service POST /orders (alt)       TradeRequestedEvent
  → Portfolio Service       GET /portfolios          -
  ← Trade Execution Service -                        TradeExecutedEvent, TradeRejectedEvent
  ← Portfolio Service       -                        PortfolioUpdatedEvent
  ← Central Orchestrator    -                        AllocationDirectiveEvent

Trade Execution Service
  → Data Platform Service   GET /prices (for paper)  -
  → Portfolio Service       -                        -
  ← Branch Services         -                        TradeRequestedEvent
  → Portfolio Service       -                        TradeExecutedEvent

Portfolio Service
  → Data Platform Service   GET /prices (for NAV)    -
  ← Trade Execution Service -                        TradeExecutedEvent
  ← Central Orchestrator    -                        AllocationDirectiveEvent
  → Central Orchestrator    -                        PortfolioUpdatedEvent, PortfolioSnapshotEvent

Central Orchestrator (Phase 3)
  → Portfolio Service       GET /fund/summary        -
  → Data Platform Service   GET /macro               -
  ← Portfolio Service       -                        PortfolioSnapshotEvent
  → Branch Services         -                        AllocationDirectiveEvent
  → Risk Manager            -                        RiskAlertEvent
```

---

## 11. Compatibility with Existing Repo

The existing `src/` code is not modified. Branch services (Phase 2) will import agent
logic from the existing codebase and wrap it in the new service structure:

| Existing Component | Phase 1 Equivalent |
|---|---|
| `src/data/models.py` (Price, FinancialMetrics, etc.) | `common/models/` + Data Platform response schemas |
| `src/backtesting/types.py` (Action, PositionState) | `common/enums.py` (OrderSide) + `common/models/position.py` |
| `src/backtesting/portfolio.py` (Portfolio class) | Portfolio Service (server-side state management) |
| `src/backtesting/trader.py` (TradeExecutor) | Trade Execution Service |
| `src/tools/api.py` (get_prices, etc.) | Data Platform Service + FinancialDatasetsAdapter |
| `src/graph/state.py` (AgentState) | Kept as-is within branch services (Phase 2) |
| `app/backend/database/` (SQLite ORM) | PostgreSQL schema (this document) |