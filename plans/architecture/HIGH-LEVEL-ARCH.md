# Agentic AI Hedge Fund -- High-Level Architecture

## 1. Design Principles

- **Microservices**: Each branch is an independent, deployable service
- **Event-driven**: Services communicate primarily through an event bus, enabling loose coupling and full auditability
- **Branch autonomy**: Each branch owns its portfolio and cadence; the central layer coordinates, not controls
- **Execution abstraction**: Paper/live trading is a configuration toggle at the execution layer, invisible to branch logic
- **Data-source agnostic**: Adapter pattern for all external data, so branches aren't coupled to specific providers

---

## 2. Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Execution model | Hybrid: central orchestrator is scheduled batch (weekly/monthly); branches can be long-running services with their own cadence | Different asset classes have different market hours and rebalancing needs |
| Trading mode | Paper trading from day one, clean toggle to live | De-risk development; validate strategies before real capital |
| Fund allocation | Top-down initially; evolve to request-based | Start simple, add complexity once the feedback loop is proven |
| Cross-branch risk | Global risk layer in central orchestrator | Correlations span asset classes (e.g., tech equities + BTC) |
| Persistence | Relational DB (PostgreSQL) + event store (Kafka) | Fast queries for current state; append-only log for auditability and replay |
| Inter-service comms | Event bus (Kafka) for async; REST/gRPC for sync queries | Loose coupling for trade flow; low-latency for state queries |
| Agent orchestration | LangGraph within branch services; not for inter-service orchestration | Good fit for parallel agent workflows; microservice orchestration handled by event bus + schedulers |
| Quant branch | Deferred; not LLM-based | Fundamentally different from other branches; build out other branches first |

---

## 3. System Architecture Overview

```
+-------------------------------------------------------------------------+
|                       CENTRAL ORCHESTRATION LAYER                       |
|  +------------------+  +------------------+  +----------------------+  |
|  |  Fund Allocator   |  |  Global Risk     |  |  Market Regime       |  |
|  |  (weekly batch)   |  |  Manager         |  |  Analyzer            |  |
|  |                   |  |  (cross-branch   |  |  (macro trends,      |  |
|  |  - capital budget |  |   correlations,  |  |   volatility regime, |  |
|  |    per branch     |  |   concentration, |  |   sector rotation)   |  |
|  |  - rebalancing    |  |   drawdown)      |  |                      |  |
|  |    directives     |  |                  |  |                      |  |
|  +------------------+  +------------------+  +----------------------+  |
+----------------------------------+--------------------------------------+
                                   |
                        +----------+----------+
                        |      EVENT BUS      |
                        |   (Kafka / RMQ)     |
                        +----------+----------+
                                   |
        +----------+-----------+---+-------+-----------+
        |          |           |           |           |
   +----v---+ +---v----+ +---v----+ +----v----+ +---v----+
   |Equities| | Crypto | | Bonds  | |Commod-  | | Quant  |
   |Branch  | | Branch | | Branch | |ities    | | Branch |
   |Service | |Service | |Service | |Branch   | |Service |
   |        | |        | |        | |Service  | |(future)|
   | +----+ | |        | |        | |         | |        |
   | |Grwt| | |  24/7  | |        | |         | |        |
   | |Sub | | | cadence| |        | |         | |        |
   | +----+ | |        | |        | |         | |        |
   | |Val | | |        | |        | |         | |        |
   | |Sub | | |        | |        | |         | |        |
   | +----+ | |        | |        | |         | |        |
   +----+---+ +---+----+ +---+----+ +----+----+ +---+----+
        |         |          |           |           |
        +---------+-----+----+-----------+-----------+
                        |
             +----------+----------+
             |   SHARED SERVICES   |
             |                     |
             |  +---------------+  |
             |  | Trade         |  |
             |  | Execution     |  |
             |  | Service       |  |
             |  | (paper/live)  |  |
             |  +---------------+  |
             |  +---------------+  |
             |  | Portfolio     |  |
             |  | Service       |  |
             |  | (state, P&L)  |  |
             |  +---------------+  |
             |  +---------------+  |
             |  | Data Platform |  |
             |  | Service       |  |
             |  | (mkt data,   |  |
             |  |  adapters)    |  |
             |  +---------------+  |
             +---------------------+
```

---

## 4. Service Definitions

### 4.1 Central Orchestration Layer

Runs on a scheduled basis (weekly/monthly). Not a long-running service -- it activates, executes, and goes idle.

#### Fund Allocator

- Collects portfolio snapshots from all active branches via the Portfolio Service
- Evaluates branch performance (returns, Sharpe, drawdown) over trailing windows
- Consults the Market Regime Analyzer for macro context
- Runs an allocation model (can be LLM-based, mean-variance, risk-parity, or rule-based)
- Emits `AllocationDirective` events to the event bus with capital budgets per branch
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

### 4.2 Branch Services

Each branch is an independently deployable microservice with a consistent internal structure but domain-specific strategy logic.

#### Branch Service Internal Architecture

```
+-- Branch Service (e.g., Equities) ----------------------------+
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
|  | Router       |                       | Emitter          |  |
|  | (equities:   |                       | (-> event bus)   |  |
|  |  growth/val) |                       +------------------+  |
|  +--------------+                                              |
|                                                                |
|  +------------------------------------------------------------+|
|  | Branch Scheduler                                            ||
|  | (defines cadence: daily, hourly, continuous)                ||
|  +------------------------------------------------------------+|
+----------------------------------------------------------------+
```

**Components:**

- **Research Agents**: The analyst agents. Run in parallel within the branch. Fetch data via the Data Platform Service, analyze, produce signals (bullish/bearish/neutral + confidence + reasoning). Orchestrated via LangGraph within the branch.
- **Strategy Engine**: Aggregates signals from all research agents into concrete trade decisions. Handles sub-branch routing (e.g., growth vs. value for equities). Resolves conflicts when the same instrument appears in multiple sub-branches.
- **Branch Risk Manager**: Local risk management scoped to the branch's portfolio. Position sizing (volatility-adjusted), single-instrument limits, sector concentration within the branch, correlation within the branch's holdings.
- **Branch Scheduler**: Controls execution cadence. Examples:
  - Equities: daily at market open
  - Crypto: hourly or continuous
  - Bonds: weekly
  - Commodities: daily during futures market hours
- **Trade Request Emitter**: Publishes `TradeRequested` events to the event bus. Does not execute trades directly.

#### Equities Branch: Growth / Value Sub-Branches

```
+-- Equities Branch Service ------------------------------------------+
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
|            v  TradeRequested events                                  |
+----------------------------------------------------------------------+
```

### 4.3 Trade Execution Service

The paper/live toggle point. Branches are completely unaware of which mode is active.

```
+-- Trade Execution Service ------------------------------------+
|                                                                |
|  Trade Request (from event bus)                                |
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
|  -> Emits: TradeExecuted / TradeRejected events                |
+----------------------------------------------------------------+
```

**Key design points:**

- `PaperTradingAdapter` simulates fills with realistic slippage and latency modeling
- Each broker adapter implements a common `BrokerAdapter` interface: `submit_order()`, `cancel_order()`, `get_order_status()`
- The Order Validator checks against global risk limits (from the Global Risk Manager) as a final safety net
- Every trade attempt (success or failure) is emitted as an event for full auditability
- Mode (paper/live) is configurable per-branch or globally via service configuration

### 4.4 Portfolio Service

Source of truth for all portfolio state across the fund.

**Responsibilities:**

- Maintains current portfolio state for each branch and the aggregate fund
- Listens to `TradeExecuted` events and updates positions atomically
- Computes real-time P&L (realized + unrealized), NAV, drawdown, exposure metrics
- Provides snapshot APIs for the central orchestrator (aggregate view) and branch services (branch-specific view)
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
  +- id, portfolio_id, instrument_id, side (long/short), quantity,
     cost_basis, current_price, unrealized_pnl, updated_at

instruments
  +- id, symbol, asset_class, exchange, currency

realized_gains
  +- id, portfolio_id, instrument_id, side, pnl, closed_at

portfolio_snapshots
  +- id, portfolio_id, nav, cash, total_exposure, timestamp
```

### 4.5 Data Platform Service

Unified data access layer. Branches request data through a single API; they don't know which provider is behind it.

```
+-- Data Platform Service -----------------------------------------+
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
|  |  +- FinancialDatasetsAdapter (equities)              |        |
|  |  +- YahooFinanceAdapter (equities fallback)          |        |
|  |  +- FREDAdapter (macro, bonds, interest rates)       |        |
|  |  +- CoinGeckoAdapter (crypto)                        |        |
|  |  +- QuandlAdapter (commodities, futures)             |        |
|  |  +- AlphaVantageAdapter (multi-asset)                |        |
|  |  +- ...                                               |        |
|  +------------------------------------------------------+        |
|                                                                   |
|  +---------------+  +---------------+  +------------------+      |
|  | Cache (Redis) |  | Rate Limiter  |  | Fallback Router  |     |
|  +---------------+  +---------------+  +------------------+      |
+-------------------------------------------------------------------+
```

**Key design points:**

- Source adapters implement a common interface per data type (e.g., `PriceDataAdapter`, `FundamentalsAdapter`)
- Fallback Router: If the primary adapter fails or rate-limits, automatically tries fallback adapters
- Cache layer (Redis) with configurable TTLs per data type (e.g., prices: 1 min, fundamentals: 1 hour, news: 5 min)
- Rate limiter prevents any single branch from exhausting API quotas
- New data sources are added by implementing an adapter -- no changes to branch services

---

## 5. Event Flow and Persistence

### 5.1 Dual Persistence Model

```
+-- PostgreSQL (Relational) -----------+  +-- Event Store (Kafka) ---------------+
|                                       |  |                                      |
|  - Current portfolio state            |  |  - TradeRequested events             |
|  - Current positions                  |  |  - TradeExecuted events              |
|  - Branch configurations              |  |  - TradeRejected events              |
|  - Instrument metadata                |  |  - AllocationDirective events        |
|  - User/fund settings                 |  |  - RiskAlert events                  |
|  - Broker credentials (encrypted)     |  |  - PortfolioSnapshot events          |
|                                       |  |  - SignalGenerated events            |
|  (query-optimized, mutable)           |  |  - MarketRegimeChanged events        |
+---------------------------------------+  |                                      |
                                           |  (append-only, immutable,            |
                                           |   full audit trail, replayable)      |
                                           +--------------------------------------+
```

The event store provides:
- Full audit trail for every decision and trade
- Ability to replay history for debugging or backtesting
- Event sourcing for reconstructing portfolio state at any point in time

The relational DB provides:
- Fast queries for current state (what is my portfolio right now?)
- Efficient aggregations (total NAV across all branches)
- Standard reporting queries

They stay in sync via event handlers: every `TradeExecuted` event triggers a portfolio state update in PostgreSQL.

### 5.2 Core Event Flow (Trade Lifecycle)

```
BranchService          EventBus           TradeExecService      PortfolioService
    |                     |                      |                     |
    |-- TradeRequested -->|                      |                     |
    |                     |--- TradeRequested -->|                     |
    |                     |                      |-- validate --+     |
    |                     |                      |<-------------+     |
    |                     |<-- TradeExecuted ----|                     |
    |                     |                      |                     |
    |<- TradeExecuted ----|                      |                     |
    |                     |--- TradeExecuted ----|-------------------->|
    |                     |                      |              update state
    |                     |<-- PortfolioUpdated ----------------------|
    |<- PortfolioUpdated -|                      |                     |
```

### 5.3 Core Event Schemas

```
TradeRequested:
  branch_id: string
  instrument: string
  side: "buy" | "sell" | "short" | "cover"
  quantity: int
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
  quantity: int
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
   - Query Portfolio Service for branch snapshots
   - Query Data Platform for macro indicators
   - Retrieve recent RiskAlert events

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
   - Emit AllocationDirective events

5. BRANCHES ADJUST
   - Each branch receives its directive
   - Branches with reduced allocation: sell down positions, return cash
   - Branches with increased allocation: deploy into new opportunities
   - Branches with "hold": no capital change, continue normal operations
```

---

## 7. Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Service framework | **FastAPI** (Python) | Already in repo, strong async support, good LLM ecosystem |
| Inter-service messaging | **Kafka** | Durable, ordered, replayable event streams; doubles as event store |
| Service discovery / config | **Consul** or **etcd** | Service registration, shared config (paper/live mode, allocation targets) |
| Container orchestration | **Docker + Kubernetes** | Each branch = independent deployment, independent scaling |
| Relational DB | **PostgreSQL** | Battle-tested, good JSON support for flexible schemas |
| Cache | **Redis** | Market data caching, rate limiting, session state |
| Event store | **Kafka** (dual-purpose) | Kafka topics as durable append-only event log; consider EventStoreDB for richer event sourcing later |
| Scheduling | **Celery + Redis** or **APScheduler** | Branch cadence scheduling, central orchestrator weekly runs |
| Monitoring | **Prometheus + Grafana** | Service health, portfolio metrics, agent performance dashboards |
| Agent orchestration | **LangGraph** (within branches) | Good fit for parallel agent workflows inside a single branch |
| LLM providers | **OpenAI, Anthropic, etc.** | Multi-provider support via LangChain; model selection per agent |

---

## 8. Build Order

Phase 1 through 6, designed so each phase delivers working, testable functionality.

### Phase 1: Shared Infrastructure

- Event bus (Kafka) setup with core topic schemas
- PostgreSQL schema for portfolios, positions, instruments
- Portfolio Service (CRUD + event listeners)
- Trade Execution Service (paper mode only, with PaperTradingAdapter)
- Data Platform Service (with FinancialDatasetsAdapter for equities)

### Phase 2: Equities Branch Service

- Adapt existing repo agents into branch service structure
- Implement Growth and Value sub-branches with agent pools
- Instrument Classifier (growth vs. value)
- Strategy Aggregator
- Branch Risk Manager
- Branch Scheduler (daily cadence)
- End-to-end: agents analyze -> signals -> trade requests -> paper execution -> portfolio update

### Phase 3: Central Orchestration Layer (v1)

- Fund Allocator (simple: equal-weight or rule-based across active branches)
- Global Risk Manager (basic: aggregate exposure monitoring, concentration limits)
- Market Regime Analyzer (basic: VIX-based regime classification)
- Weekly batch execution wired to event bus

### Phase 4: Crypto Branch Service

- Second branch -- validates the multi-branch architecture
- Different cadence (hourly or continuous)
- CoinGecko / exchange API adapters in Data Platform Service
- Crypto-specific research agents
- CoinbaseAdapter (or similar) in Trade Execution Service

### Phase 5: Bonds and Commodities Branches

- Add as third and fourth branches
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
- **Backtesting framework**: Replay historical data through the full architecture for strategy validation