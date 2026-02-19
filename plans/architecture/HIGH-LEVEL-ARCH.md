# Agentic AI Hedge Fund -- High-Level Architecture

## 1. Design Principles

- **Modular monolith**: Clean module boundaries (portfolio, execution, data, branches) within a single deployable process. Modules communicate via direct function calls internally, preserving the option to extract into microservices later if scaling demands it
- **Event-sourced audit trail**: Every trade, allocation, and signal is persisted to a PostgreSQL event log. This provides full auditability, replay capability, and debugging -- without the infrastructure cost of a separate event streaming platform
- **Branch autonomy**: Each branch owns its portfolio and cadence; the central layer coordinates, not controls
- **Execution abstraction**: Paper/live trading is a configuration toggle at the execution layer, invisible to branch logic
- **Data-source agnostic**: Adapter pattern for all external data, so branches aren't coupled to specific providers

---

## 2. Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Architecture | Modular monolith; extract to microservices only when scaling demands it | Single process eliminates inter-service latency, distributed tracing, and messaging infrastructure. Module boundaries (adapters, repositories, interfaces) make future extraction trivial |
| Execution model | Hybrid: central orchestrator is scheduled batch (weekly/monthly); branches can run on their own cadence via APScheduler | Different asset classes have different market hours and rebalancing needs |
| Trading mode | Paper trading from day one, clean toggle to live | De-risk development; validate strategies before real capital |
| Fund allocation | Top-down initially; evolve to request-based | Start simple, add complexity once the feedback loop is proven |
| Cross-branch risk | Global risk layer in central orchestrator | Correlations span asset classes (e.g., tech equities + BTC) |
| Persistence | PostgreSQL for both current state and event log | Single database for queries and audit trail; `events` table is append-only with the same schemas as the Pydantic event models |
| Inter-module comms | Direct function calls within the monolith; PostgreSQL event log for audit trail and async replay | No network overhead; event log provides the same auditability as a message bus |
| Agent orchestration | LangGraph within branch modules | Good fit for parallel agent workflows within a single branch |
| Quant branch | Deferred; not LLM-based | Fundamentally different from other branches; build out other branches first |

---

## 3. System Architecture Overview

```
+=========================================================================+
|                      SINGLE FASTAPI PROCESS                             |
|                                                                         |
|  +-------------------------------------------------------------------+  |
|  |                   CENTRAL ORCHESTRATION LAYER                      |  |
|  |  +------------------+ +------------------+ +--------------------+  |  |
|  |  | Fund Allocator   | | Global Risk      | | Market Regime      |  |  |
|  |  | (weekly batch,   | | Manager          | | Analyzer           |  |  |
|  |  |  APScheduler)    | | (cross-branch    | | (macro trends,     |  |  |
|  |  |                  | |  correlations,   | |  volatility regime, |  |  |
|  |  | - capital budget | |  concentration,  | |  sector rotation)  |  |  |
|  |  |   per branch     | |  drawdown)       | |                    |  |  |
|  |  | - rebalancing    | |                  | |                    |  |  |
|  |  |   directives     | |                  | |                    |  |  |
|  |  +------------------+ +------------------+ +--------------------+  |  |
|  +-------------------------------------------------------------------+  |
|                                                                         |
|  +-------------------------------------------------------------------+  |
|  |                       BRANCH MODULES                               |  |
|  |                                                                    |  |
|  |  +-----------+ +-----------+ +-----------+ +-----------+ +------+  |  |
|  |  | Equities  | | Crypto    | | Bonds     | | Commod-   | |Quant |  |  |
|  |  | Module    | | Module    | | Module    | | ities     | |Module|  |  |
|  |  |           | |           | |           | | Module    | |(fut.)|  |  |
|  |  | +-------+ | |  24/7     | |           | |           | |      |  |  |
|  |  | |Growth | | |  cadence  | |           | |           | |      |  |  |
|  |  | +-------+ | |           | |           | |           | |      |  |  |
|  |  | |Value  | | |           | |           | |           | |      |  |  |
|  |  | +-------+ | |           | |           | |           | |      |  |  |
|  |  +-----------+ +-----------+ +-----------+ +-----------+ +------+  |  |
|  +-------------------------------------------------------------------+  |
|                                                                         |
|  +-------------------------------------------------------------------+  |
|  |                       SHARED MODULES                               |  |
|  |                                                                    |  |
|  |  +------------------+ +------------------+ +--------------------+  |  |
|  |  | Portfolio Module | | Trade Execution  | | Data Platform      |  |  |
|  |  | (state, P&L,     | | Module           | | Module             |  |  |
|  |  |  snapshots)      | | (paper/live,     | | (market data,      |  |  |
|  |  |                  | |  broker adapters)| |  source adapters,  |  |  |
|  |  |                  | |                  | |  in-memory cache)  |  |  |
|  |  +------------------+ +------------------+ +--------------------+  |  |
|  +-------------------------------------------------------------------+  |
|                                                                         |
|  +-------------------------------------------------------------------+  |
|  | Event Log (PostgreSQL events table -- append-only audit trail)     |  |
|  +-------------------------------------------------------------------+  |
|                                                                         |
|  +-------------------------------------------------------------------+  |
|  | Shared: models, enums, interfaces, config                         |  |
|  +-------------------------------------------------------------------+  |
+=========================================================================+
                                   |
                          +--------+--------+
                          |   PostgreSQL    |
                          |  (state +      |
                          |   event log)   |
                          +----------------+
```

**Key architectural property:** Module boundaries are enforced through Python interfaces
(ABCs) and the repository/adapter pattern. Each module exposes a service class with a
defined interface. This means extracting any module into a standalone microservice later
requires only: (1) adding a FastAPI route layer that delegates to the same service class,
and (2) replacing direct function calls with HTTP calls at the call sites.

---

## 4. Module Definitions

### 4.1 Central Orchestration Layer

Runs on a scheduled basis (weekly/monthly) via APScheduler. Not always active -- it triggers, executes its workflow, and goes idle until the next scheduled run.

#### Fund Allocator

- Collects portfolio snapshots from all active branches via the Portfolio module
- Evaluates branch performance (returns, Sharpe, drawdown) over trailing windows
- Consults the Market Regime Analyzer for macro context
- Runs an allocation model (can be LLM-based, mean-variance, risk-parity, or rule-based)
- Calls each branch's allocation handler and logs `AllocationDirective` events with capital budgets per branch
- Branches receive directives and adjust their portfolios accordingly (selling down or deploying new capital)

#### Global Risk Manager

- Computes cross-branch correlation matrix from position-level data
- Monitors aggregate fund metrics: total drawdown, net exposure, beta, sector concentration
- Enforces fund-level limits:
  - No single branch > X% of total AUM
  - No single position > Y% of total AUM across all branches
  - Aggregate drawdown circuit breaker (e.g., halt all trading if fund drops > Z%)
- Emits `RiskAlert` events when limits are breached or approaching
- **Future evolution**: Receives `TradeProposal` events from branches and approves/rejects/modifies them before execution (request-based model)

#### Market Regime Analyzer

- Classifies the current macro environment using a combination of:
  - Volatility indicators (VIX, realized vol)
  - Yield curve shape (inverted, steep, flat)
  - Credit spreads
  - Momentum/trend indicators across major indices
  - Sentiment data (put/call ratios, fund flows)
- Outputs a regime classification (e.g., risk-on, risk-off, transitional, crisis)
- This context is used by the Fund Allocator and can be consumed by individual branches

### 4.2 Branch Modules

Each branch is a Python module with a consistent internal structure but domain-specific strategy logic. All branches share the same process and database, but are isolated through clean interfaces.

#### Branch Module Internal Architecture

```
+-- Branch Module (e.g., Equities) -----------------------------+
|                                                                |
|  +--------------+   +--------------+   +------------------+   |
|  | Research      |   | Strategy     |   | Branch Risk     |   |
|  | Agents        |-->| Engine       |-->| Manager         |   |
|  | (parallel)    |   | (signals ->  |   | (position       |   |
|  |               |   |  decisions)  |   |  sizing,        |   |
|  |               |   |              |   |  limits)        |   |
|  +--------------+   +--------------+   +--------+---------+   |
|                                                  |             |
|  +--------------+                       +--------v---------+  |
|  | Sub-Branch   |                       | Trade Request    |  |
|  | Router       |                       | Handler          |  |
|  | (equities:   |                       | (-> trade exec   |  |
|  |  growth/val) |                       |    module)       |  |
|  +--------------+                       +------------------+  |
|                                                                |
|  +------------------------------------------------------------+|
|  | Branch Scheduler (APScheduler)                              ||
|  | (defines cadence: daily, hourly, continuous)                ||
|  +------------------------------------------------------------+|
+----------------------------------------------------------------+
```

**Components:**

- **Research Agents**: The analyst agents. Run in parallel within the branch. Fetch data via the Data Platform module, analyze, produce signals (bullish/bearish/neutral + confidence + reasoning). Orchestrated via LangGraph within the branch.
- **Strategy Engine**: Aggregates signals from all research agents into concrete trade decisions. Handles sub-branch routing (e.g., growth vs. value for equities). Resolves conflicts when the same instrument appears in multiple sub-branches.
- **Branch Risk Manager**: Local risk management scoped to the branch's portfolio. Position sizing (volatility-adjusted), single-instrument limits, sector concentration within the branch, correlation within the branch's holdings.
- **Branch Scheduler**: Controls execution cadence via APScheduler. Examples:
  - Equities: daily at market open
  - Crypto: hourly or continuous
  - Bonds: weekly
  - Commodities: daily during futures market hours
- **Trade Request Emitter**: Calls the Trade Execution module's service directly (function call). Also logs a `TradeRequested` event to the PostgreSQL event log for auditability.

#### Equities Branch: Growth / Value Sub-Branches

```
+-- Equities Branch Module -------------------------------------------+
|                                                                      |
|  Incoming: tickers + capital allocation from central orchestrator    |
|                                                                      |
|  +--------------------+                                             |
|  | Instrument          |  Classifies tickers as growth or value     |
|  | Classifier          |  (rule-based, LLM, or hybrid)             |
|  +---------+----------+                                             |
|            |                                                         |
|      +-----+-----+                                                  |
|      v           v                                                  |
|  +--------+   +--------+                                           |
|  | Growth |   | Value  |                                           |
|  | Sub    |   | Sub    |  Each has its own agent pool:             |
|  | Branch |   | Branch |  - Growth: Cathie Wood, Peter Lynch, etc. |
|  |        |   |        |  - Value: Buffett, Graham, Munger, etc.   |
|  +---+----+   +---+----+                                           |
|      |            |                                                  |
|      v            v                                                  |
|  +--------------------+                                             |
|  | Equities Strategy   |  Merges growth + value signals             |
|  | Aggregator          |  Resolves conflicts (same ticker both?)    |
|  +---------+----------+                                             |
|            |                                                         |
|            v                                                         |
|  +--------------------+                                             |
|  | Branch Risk Mgr    |  Position sizing, sector limits             |
|  +---------+----------+                                             |
|            |                                                         |
|            v  -> Trade Execution module                               |
+----------------------------------------------------------------------+
```

### 4.3 Trade Execution Module

The paper/live toggle point. Branches are completely unaware of which mode is active.

```
+-- Trade Execution Module -------------------------------------+
|                                                                |
|  Trade Request (from branch module)                            |
|       |                                                        |
|       v                                                        |
|  +----------------+                                            |
|  | Order           |  Validates against global risk            |
|  | Validator       |  limits before execution                  |
|  +-------+--------+                                            |
|          |                                                     |
|          v                                                     |
|  +----------------+     +-----------------------------+        |
|  | Execution       |     | Broker Adapters             |       |
|  | Router          |---->|  +- PaperTradingAdapter     |       |
|  | (mode flag)     |     |  +- AlpacaAdapter           |       |
|  +----------------+     |  +- IBKRAdapter              |       |
|                          |  +- CoinbaseAdapter          |       |
|                          |  +- ...                      |       |
|                          +-----------------------------+        |
|                                                                |
|  -> Logs: TradeExecuted / TradeRejected to event log           |
+----------------------------------------------------------------+
```

**Key design points:**

- `PaperTradingAdapter` simulates fills with realistic slippage and latency modeling
- Each broker adapter implements a common `BrokerAdapter` interface: `submit_order()`, `cancel_order()`, `get_order_status()`
- The Order Validator checks against global risk limits (from the Global Risk Manager) as a final safety net
- Every trade attempt (success or failure) is logged to the PostgreSQL event log for full auditability
- Mode (paper/live) is configurable per-branch or globally via application configuration

### 4.4 Portfolio Module

Source of truth for all portfolio state across the fund.

**Responsibilities:**

- Maintains current portfolio state for each branch and the aggregate fund
- Called by the Trade Execution module after each fill to update positions atomically
- Computes real-time P&L (realized + unrealized), NAV, drawdown, exposure metrics
- Provides snapshot methods for the central orchestrator (aggregate view) and branch modules (branch-specific view)
- Stores periodic snapshots for historical performance tracking

**Data model (PostgreSQL):**

```
funds
  +- id, name, total_aum, created_at

branches
  +- id, fund_id, name, type, allocated_capital, status

portfolios
  +- id, branch_id, cash, margin_requirement, margin_used, nav, updated_at

positions
  +- id, portfolio_id, instrument_id, long_quantity, long_cost_basis,
     short_quantity, short_cost_basis, realized_pnl_long, realized_pnl_short, updated_at

instruments
  +- id, symbol, asset_class, exchange, currency

portfolio_snapshots
  +- id, portfolio_id, nav, cash, total_exposure, timestamp
```

### 4.5 Data Platform Module

Unified data access layer. Branches request data through a single interface; they don't know which provider is behind it.

```
+-- Data Platform Module -------------------------------------------+
|                                                                   |
|  +----------------------------------------------------+         |
|  | Unified Data API                                    |         |
|  |  GET /prices/{instrument}?start=&end=&interval=     |         |
|  |  GET /fundamentals/{ticker}?metrics=                 |         |
|  |  GET /news/{topic}?since=                            |         |
|  |  GET /macro/{indicator}                              |         |
|  |  GET /sentiment/{instrument}                         |         |
|  |  GET /options-chain/{ticker}                         |         |
|  +-------------------------+----------------------------+         |
|                            |                                      |
|  +-------------------------v----------------------------+         |
|  | Source Adapters                                       |        |
|  |  +- YahooFinanceAdapter (equities + crypto, primary)  |        |
|  |  +- FinancialDatasetsAdapter (equities, future)      |        |
|  |  +- FREDAdapter (macro, bonds, interest rates)       |        |
|  |  +- CoinGeckoAdapter (crypto, future fallback)       |        |
|  |  +- QuandlAdapter (commodities, futures)             |        |
|  |  +- AlphaVantageAdapter (multi-asset)                |        |
|  |  +- ...                                               |        |
|  +------------------------------------------------------+        |
|                                                                   |
|  +---------------+  +---------------+  +------------------+      |
|  | Cache (in-mem)|  | Rate Limiter  |  | Fallback Router  |     |
|  | (cachetools)  |  |               |  |                  |     |
|  +---------------+  +---------------+  +------------------+      |
+-------------------------------------------------------------------+
```

**Key design points:**

- Source adapters implement a common interface per data type (e.g., `PriceDataAdapter`, `FundamentalsAdapter`)
- Fallback Router: If the primary adapter fails or rate-limits, automatically tries fallback adapters
- In-memory cache (cachetools TTLCache) with configurable TTLs per data type (e.g., prices: 1 min, fundamentals: 1 hour, news: 5 min). Upgrade to Redis if multi-instance caching is needed
- Rate limiter prevents any single branch from exhausting API quotas
- New data sources are added by implementing an adapter -- no changes to branch modules

---

## 5. Event Flow and Persistence

### 5.1 Unified Persistence Model (PostgreSQL)

```
+-- PostgreSQL --------------------------------------------------------+
|                                                                       |
|  MUTABLE STATE (query-optimized)    |  EVENT LOG (append-only)        |
|  ─────────────────────────────────  |  ─────────────────────────────  |
|  - Current portfolio state          |  - TradeRequested events        |
|  - Current positions                |  - TradeExecuted events         |
|  - Branch configurations            |  - TradeRejected events         |
|  - Instrument metadata              |  - AllocationDirective events   |
|  - User/fund settings               |  - RiskAlert events             |
|  - Broker credentials (encrypted)   |  - PortfolioSnapshot events     |
|                                     |  - SignalGenerated events       |
|  Fast queries for current state     |  - MarketRegimeChanged events   |
|  Efficient aggregations             |                                 |
|                                     |  Full audit trail, immutable,   |
|                                     |  queryable, replayable          |
+----------------------------------------------------------------------+
```

A single PostgreSQL database serves both roles:

- **Mutable state tables** (portfolios, positions, orders, trades): Query-optimized for current state. "What is my portfolio right now?"
- **`events` table** (append-only): Every decision, trade, and signal is logged as a JSON event row. Provides the same auditability as a Kafka event store, but with the added benefit of being SQL-queryable and requiring zero additional infrastructure.

The two stay in sync naturally: within a single database transaction, the service method both updates mutable state and inserts the event log entry. No distributed consistency problems.

**Source of truth:** The mutable state tables (`orders`, `trades`, `positions`, `portfolios`, `allocation_directives`, `risk_alerts`) are the source of truth for current state and are what application code queries. The `events` table is an audit copy -- it contains the same data as a denormalized JSON payload for auditability, replay, and debugging. If the two ever diverge, the mutable state tables are authoritative. Domain-specific tables like `allocation_directives` and `risk_alerts` exist because they have queryable columns (e.g., `resolved`, `fund_id`) that would be inefficient to filter from JSON payloads in the `events` table.

### 5.2 Core Event Flow (Trade Lifecycle)

In a modular monolith, the trade lifecycle is a chain of direct function calls within a single process, with each step logging to the event table:

```
BranchModule           TradeExecModule        PortfolioModule       EventLog (PG)
    |                        |                      |                    |
    |-- submit_order() ----->|                      |                    |
    |                        |-- INSERT event ------|-------------------->|
    |                        |   (TradeRequested)   |                    |
    |                        |                      |                    |
    |                        |-- validate --------->|                    |
    |                        |<-- cash ok ----------|                    |
    |                        |                      |                    |
    |                        |-- broker.submit() -->|                    |
    |                        |   (paper adapter)    |                    |
    |                        |                      |                    |
    |                        |-- update_position()->|                    |
    |                        |                      |-- INSERT event --->|
    |                        |                      |   (TradeExecuted)  |
    |                        |                      |                    |
    |<-- OrderResult --------|                      |                    |
```

### 5.3 Core Event Schemas

```
TradeRequested:
  branch_id: string
  instrument: string
  side: "buy" | "sell" | "short" | "cover"
  quantity: float                        # float to support fractional shares and crypto
  order_type: "market" | "limit"
  limit_price: float | null
  confidence: float
  reasoning: string
  agent_signals: dict
  timestamp: datetime

TradeExecuted:
  trade_id: string
  branch_id: string
  instrument: string
  side: string
  quantity: float
  fill_price: float
  commission: float
  slippage: float
  execution_mode: "paper" | "live"
  timestamp: datetime

AllocationDirective:
  fund_id: string
  allocations:
    - branch_id: string
      target_capital: float
      current_capital: float
      action: "increase" | "decrease" | "hold"
  regime: string
  reasoning: string
  timestamp: datetime

RiskAlert:
  level: "info" | "warning" | "critical"
  source: "global" | branch_id
  metric: string
  current_value: float
  threshold: float
  action_required: string
  timestamp: datetime
```

---

## 6. Central Orchestration Flow (Weekly Cycle)

```
1. COLLECT
   - Call Portfolio module for branch snapshots
   - Call Data Platform module for macro indicators
   - Query recent RiskAlert events from event log

2. ASSESS REGIME
   - Market Regime Analyzer classifies environment
   - Output: regime label + confidence + key drivers

3. EVALUATE RISK
   - Global Risk Manager computes:
     - Cross-branch correlation matrix
     - Aggregate exposure by sector, geography, factor
     - Fund-level drawdown and VaR
   - Flag any limit breaches

4. ALLOCATE
   - Fund Allocator runs allocation model:
     - Inputs: branch performance, regime, risk state, strategic targets
     - Outputs: capital budget per branch
   - Log AllocationDirective events to event log
   - Call each branch module's allocation handler directly

5. BRANCHES ADJUST
   - Each branch processes its directive
   - Branches with reduced allocation: sell down positions, return cash
   - Branches with increased allocation: deploy into new opportunities
   - Branches with "hold": no capital change, continue normal operations
```

---

## 7. Technology Stack

| Layer | Technology | Rationale | Upgrade path |
|-------|-----------|-----------|--------------|
| Framework | **FastAPI** (Python) | Already in repo, strong async support, good LLM ecosystem | — |
| Database | **PostgreSQL** | Battle-tested, good JSON support; serves as both state store and event log | — |
| ORM | **SQLAlchemy** (async) | Industry standard, Alembic migrations, async session support | — |
| Cache | **cachetools** (in-memory TTL) | Zero infrastructure, sufficient for single-process | Redis (when multi-instance) |
| Scheduling | **APScheduler** | In-process, lightweight; handles branch cadence + central orchestrator runs | Celery (when distributed workers needed) |
| Monitoring | **structlog** (structured logging) | JSON logs, easy to search; lightweight for development phase | Prometheus + Grafana (when dashboard needed) |
| Containerization | **Docker Compose** | Simple local dev: just PostgreSQL + the app | Kubernetes (when deploying to cloud) |
| Agent orchestration | **LangGraph** (within branches) | Good fit for parallel agent workflows inside a single branch | — |
| LLM providers | **OpenAI, Anthropic, etc.** | Multi-provider support via LangChain; model selection per agent | — |

**What we're intentionally NOT using yet** (and when to add them):

| Technology | Add when... |
|-----------|-------------|
| Kafka / RabbitMQ | Event throughput exceeds what PostgreSQL polling can handle (~10k+ events/day), or you need real-time streaming to external consumers |
| Redis | You run multiple app instances and need shared caching, or you need pub/sub across processes |
| Kubernetes | You deploy to cloud and need autoscaling, rolling deploys, or multi-region |
| Consul / etcd | You split into actual microservices and need service discovery (unlikely for this project) |

---

## 8. Build Order

Phase 1 through 6, designed so each phase delivers working, testable functionality.

### Phase 1: Shared Infrastructure

- Docker Compose with PostgreSQL
- PostgreSQL schema for portfolios, positions, instruments, events
- Shared library: models, enums, interfaces, event schemas
- Portfolio module (CRUD + position updates)
- Trade Execution module (paper mode only, with PaperTradingAdapter)
- Data Platform module (with YahooFinanceAdapter for equities/crypto, in-memory cache)
- Event log table + helper for appending events
- Single FastAPI app wiring all modules together

### Phase 2: Equities Branch Module

- Adapt existing repo agents into branch module structure
- Implement Growth and Value sub-branches with agent pools
- Instrument Classifier (growth vs. value)
- Strategy Aggregator
- Branch Risk Manager
- Branch Scheduler (daily cadence via APScheduler)
- End-to-end: agents analyze -> signals -> trade requests -> paper execution -> portfolio update

### Phase 3: Central Orchestration Layer (v1)

- Fund Allocator (simple: equal-weight or rule-based across active branches)
- Global Risk Manager (basic: aggregate exposure monitoring, concentration limits)
- Market Regime Analyzer (basic: VIX-based regime classification)
- Weekly batch execution via APScheduler

### Phase 4: Crypto Branch Module

- Second branch -- validates the multi-branch architecture
- Different cadence (hourly or continuous)
- CoinGecko / exchange API adapters in Data Platform module
- Crypto-specific research agents
- CoinbaseAdapter (or similar) in Trade Execution module

### Phase 5: Bonds and Commodities Branches

- Add as third and fourth branch modules
- FRED adapter for bonds/rates data
- Quandl/commodity-specific adapters
- Branch-specific agent pools and strategies

### Phase 6: Quant Branch

- Deferred until other branches are stable
- Likely non-LLM: traditional quantitative models (factor models, stat arb, etc.)
- LLM layer may sit on top for orchestration/interpretation, not signal generation

---

## 9. Future Enhancements (Post-MVP)

- **Request-based allocation**: Branches propose trades; central risk system approves/rejects before execution
- **Live trading toggle**: Add real broker adapters (Alpaca, IBKR, Coinbase) alongside paper trading
- **Advanced global risk**: VaR/CVaR calculations, stress testing, scenario analysis across branches
- **Performance attribution**: Decompose returns by branch, agent, strategy, and factor
- **Web dashboard**: Real-time fund overview, branch drill-down, trade history, P&L charts
- **Alerting**: Slack/email notifications for risk alerts, significant trades, allocation changes
- **Multi-fund support**: Run multiple fund configurations with different allocation strategies
- **Backtesting framework**: Replay historical events from PostgreSQL event log through the full architecture for strategy validation
- **Microservice extraction**: If a specific module becomes a bottleneck (e.g., Data Platform under heavy load), extract it into a standalone service. The adapter/repository interfaces make this a mechanical refactor, not an architectural change
- **Kafka migration**: If event throughput or real-time streaming to external consumers becomes a requirement, add Kafka as a transport layer alongside the PostgreSQL event log