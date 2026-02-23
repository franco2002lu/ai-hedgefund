# Phase 2: Equities Branch -- Growth & Value Branches

This document defines the architecture for the equities branch module, which is the first
branch implementation built on top of the Phase 1 shared infrastructure.

> **Prerequisites**: All Phase 1 shared modules (Portfolio, Trade Execution, Data Platform,
> Event Log) must be operational. See [PHASE1-SHARED-INFRASTRUCTURE.md](PHASE1-SHARED-INFRASTRUCTURE.md).

---

## 1. Overview

The equities module implements two **separate branches** -- **Growth** and **Value** -- each
registered as its own row in the `branches` table with its own portfolio, positions, orders,
and trades. This means each branch is a fully independent unit from Phase 1's perspective:

| Branch | `branches` row | Portfolio | Positions | Orders/Trades |
|--------|---------------|-----------|-----------|---------------|
| `equities-growth` | `BranchType.EQUITIES`, name="equities-growth" | Own portfolio | Own positions | Own orders/trades |
| `equities-value` | `BranchType.EQUITIES`, name="equities-value" | Own portfolio | Own positions | Own orders/trades |

This separation means:
- P&L is tracked independently per branch (growth vs. value performance is visible)
- No position co-mingling (if both branches hold AAPL, they are separate positions)
- The central orchestrator (Phase 3) can allocate capital to each independently
- All existing Phase 1 infrastructure works with zero modifications (keyed on `branch_id`)

Both branches share the same equities module **code** but are configured differently
(different ETF universe, screening filters, and agent prompts). Each branch runs a
quarterly agentic workflow:

```
Stock Universe (ETF holdings)
    -> Quantitative Screening (~230 -> ~40-50 candidates)
        -> Multi-Agent Deep Analysis (3 specialist analysts)
            -> Portfolio Manager (synthesis + rebalancing)
                -> Trade Execution (automated market orders)
```

---

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Branch architecture | Two separate branches (equities-growth, equities-value) | Each gets its own portfolio, positions, orders, and trades. All Phase 1 infrastructure works with zero modifications since it's keyed on `branch_id`. P&L tracked independently. Central orchestrator can allocate to each separately |
| Stock universe | VOOG (growth), VOOV (value) | S&P 500 Growth/Value ETFs provide systematic, rules-based universes with ~230 stocks each. Symmetric methodology from S&P |
| Cadence | Quarterly | Aligns with earnings cycles; balances analysis depth vs. cost |
| Screening | Purely quantitative, no LLM | Fast, cheap, deterministic; filters ~230 down to ~40-50 candidates |
| Screening thresholds | Externalized configuration | Expect frequent tuning; must not require code changes |
| Screening filters | Pluggable filter system | Easy to add/remove filters over time without restructuring |
| Agent architecture | Multi-agent pipeline (3 analysts -> 1 portfolio manager) | Specialization improves analysis quality; modular and independently testable |
| Analyst output | 1-10 bullish score + 1-10 confidence rating per stock | Simple, structured, easy for portfolio manager to synthesize |
| Portfolio manager synthesis | Deterministic weighted formula | Predictable, debuggable, auditable; LLM-based synthesis deferred |
| Holdings target | ~20 per branch (soft guidance, hard guardrails: min 10, max 30) | Concentrated enough for conviction, diversified enough for risk management |
| Position sizing | Conviction-weighted (composite score x confidence) | Higher conviction = larger position; mechanically derived from agent output |
| Position cap | 50% of branch portfolio per stock | Prevents extreme concentration in a single name |
| Rebalancing | Incremental (can add/remove symbols) | Reduces unnecessary turnover; agents assess thesis changes, not full reconstruction |
| Trade execution | Fully automated market orders, single batch | Simple for paper trading; approval gates added later via OpenClaw |
| Data sources | Yahoo Finance + SEC EDGAR | Yahoo for prices/fundamentals/news; EDGAR for detailed earnings data |
| Capital | Total fund capital until central orchestrator (Phase 3) | Equities is the only branch initially; no allocation logic needed yet |

---

## 3. Project Structure

New files and directories added in Phase 2. Existing Phase 1 structure unchanged.

```
app/
├── modules/
│   └── equities/                              # Equities Branch Module
│       ├── __init__.py
│       ├── api.py                             # FastAPI routes (trigger runs, view results)
│       ├── config.py                          # Branch-specific configuration
│       │
│       ├── universe/                          # Stock Universe Management
│       │   ├── __init__.py
│       │   ├── provider.py                    # ETF holdings fetcher (VOOG/VOOV)
│       │   └── screener.py                    # Quantitative screening engine
│       │
│       ├── agents/                            # Agentic Workflow
│       │   ├── __init__.py
│       │   ├── news_analyst.py                # News Analyst agent
│       │   ├── fundamentals_analyst.py        # Fundamentals Analyst agent
│       │   ├── technical_analyst.py           # Technical Analyst agent
│       │   ├── portfolio_manager.py           # Portfolio Manager agent
│       │   └── graph.py                       # LangGraph workflow definition
│       │
│       ├── models.py                          # Branch-specific Pydantic models
│       └── service.py                         # EquitiesBranchService (orchestrates the pipeline)
│
├── modules/
│   └── data_platform/
│       └── adapters/
│           └── sec_edgar.py                   # SEC EDGAR adapter (new)
```

---

## 4. Stock Universe Management

### 4.1 ETF Holdings Provider

Each branch derives its stock universe from an ETF's current holdings.

| Branch | ETF | Description |
|------------|-----|-------------|
| Growth | VOOG | Vanguard S&P 500 Growth ETF (~230 holdings) |
| Value | VOOV | Vanguard S&P 500 Value ETF (~230 holdings) |

```python
# universe/provider.py

class UniverseProvider:
    """Fetches and caches ETF holdings as the stock universe."""

    async def get_holdings(self, etf_symbol: str) -> list[UniverseStock]:
        """
        Returns current holdings for the given ETF.
        Each holding includes: symbol, company_name, weight, sector.
        """
        ...

    async def refresh(self, etf_symbol: str) -> list[UniverseStock]:
        """Force-refresh holdings (ETFs rebalance quarterly)."""
        ...
```

**Data source**: Yahoo Finance (`yfinance` exposes ETF holdings via the `Ticker.funds_data`
API). Falls back to cached holdings if the API is unavailable.

**Refresh cadence**: Once per quarter (aligned with S&P rebalancing). Cache holdings
locally to avoid redundant API calls within a quarter.

### 4.2 Universe Stock Model

```python
class UniverseStock(BaseModel):
    symbol: str                   # Ticker symbol (e.g., "AAPL")
    company_name: str             # Full company name
    weight: float                 # ETF weight (0.0 - 1.0)
    sector: str | None = None     # GICS sector
```

---

## 5. Quantitative Screening

The screener filters the full universe (~230 stocks) down to ~40-50 candidates for deep
agent analysis. All filters are purely quantitative -- no LLM involved.

### 5.1 Architecture

```python
# universe/screener.py

class ScreeningFilter(ABC):
    """Base class for all screening filters."""

    @abstractmethod
    async def apply(
        self,
        stocks: list[UniverseStock],
        data_service: DataPlatformService,
    ) -> list[UniverseStock]:
        """Returns the subset of stocks that pass this filter."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable filter name for logging."""
        ...


class Screener:
    """Runs a configurable pipeline of filters sequentially."""

    def __init__(self, filters: list[ScreeningFilter]):
        self.filters = filters

    async def screen(
        self,
        stocks: list[UniverseStock],
        data_service: DataPlatformService,
    ) -> list[UniverseStock]:
        """Apply all filters in sequence, returning passing stocks."""
        remaining = stocks
        for f in self.filters:
            remaining = await f.apply(remaining, data_service)
            # Log: f.name filtered {before} -> {after}
        return remaining
```

### 5.2 Shared Filters (Both Branches)

Applied to both growth and value branches.

| Filter | Parameter | Default | Description |
|--------|-----------|---------|-------------|
| `LiquidityFilter` | `min_avg_daily_volume` | 500,000 | Minimum 90-day average daily volume |
| `MarketCapFilter` | `min_market_cap` | 2,000,000,000 | Floor at $2B to avoid micro-caps |
| `EarningsRecencyFilter` | `max_days_since_earnings` | 120 | Must have reported earnings within ~1 quarter |
| `VolatilityFilter` | `max_volatility_percentile` | 95 | Exclude top 5% by 90-day realized volatility |
| `LeverageFilter` | `max_debt_to_equity` | 5.0 | Filter out excessively leveraged companies |

### 5.3 Growth-Specific Filters

Applied only to the growth branch (VOOG universe).

| Filter | Parameter | Default | Description |
|--------|-----------|---------|-------------|
| `RevenueGrowthFilter` | `min_revenue_growth_yoy` | 0.05 | Minimum 5% year-over-year revenue growth |
| `EarningsGrowthFilter` | `min_earnings_growth_yoy` | 0.0 | Positive earnings growth required |
| `GrossMarginTrendFilter` | `margin_declining_quarters` | 2 | Exclude if gross margin declined 2+ consecutive quarters |
| `EarningsSurpriseFilter` | `min_surprise_pct` | -0.05 | Exclude if last earnings missed by more than 5% |
| `MomentumFilter` | `min_return_6m` | -0.10 | Exclude stocks down more than 10% over 6 months |
| `PEGFilter` | `max_peg_ratio` | 3.0 | Filter out extreme valuations relative to growth |

### 5.4 Value-Specific Filters

Applied only to the value branch (VOOV universe).

| Filter | Parameter | Default | Description |
|--------|-----------|---------|-------------|
| `PEFilter` | `max_pe_percentile` | 60 | P/E must be below 60th percentile of universe |
| `PBFilter` | `max_pb_percentile` | 60 | P/B must be below 60th percentile of universe |
| `FCFYieldFilter` | `min_fcf_yield` | 0.02 | Minimum 2% free cash flow yield |
| `DividendYieldFilter` | `min_dividend_yield` | 0.005 | Minimum 0.5% dividend yield |
| `ROEFilter` | `min_roe` | 0.08 | Minimum 8% return on equity (quality gate) |
| `PriceRangeFilter` | `max_52w_range_percentile` | 70 | Trading in lower 70% of 52-week range |

### 5.5 Configuration

All thresholds are externalized via the branch config. Adding or removing a filter requires
only modifying the filter list in the branch configuration -- no changes to the screening
engine itself.

```python
# equities/config.py

class ScreeningConfig(BaseModel):
    """All screening thresholds. Adjust without code changes."""

    # Shared filters
    min_avg_daily_volume: int = 500_000
    min_market_cap: float = 2_000_000_000
    max_days_since_earnings: int = 120
    max_volatility_percentile: float = 95.0
    max_debt_to_equity: float = 5.0

    # Growth-specific
    min_revenue_growth_yoy: float = 0.05
    min_earnings_growth_yoy: float = 0.0
    margin_declining_quarters: int = 2
    min_surprise_pct: float = -0.05
    min_return_6m: float = -0.10
    max_peg_ratio: float = 3.0

    # Value-specific
    max_pe_percentile: float = 60.0
    max_pb_percentile: float = 60.0
    min_fcf_yield: float = 0.02
    min_dividend_yield: float = 0.005
    min_roe: float = 0.08
    max_52w_range_percentile: float = 70.0


class EquitiesConfig(BaseModel):
    """Top-level equities branch configuration."""

    growth_etf: str = "VOOG"
    value_etf: str = "VOOV"
    screening: ScreeningConfig = ScreeningConfig()
    portfolio: PortfolioConfig  # See Section 8
    agents: AgentsConfig        # See Section 7
```

---

## 6. Agent Architecture

### 6.1 Pipeline Overview

Each branch runs the same multi-agent pipeline on its screened candidates:

```
                    +------------------+
                    | Screened Stocks   |
                    | (~40-50 symbols)  |
                    +--------+---------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+  +------v------+  +----v---------+
     |   News      |  | Fundamentals|  |  Technical   |
     |   Analyst   |  |  Analyst    |  |  Analyst     |
     +--------+---+  +------+------+  +----+---------+
              |              |              |
              |  StockSignal |  StockSignal |  StockSignal
              |  per stock   |  per stock   |  per stock
              +--------------+--------------+
                             |
                    +--------v---------+
                    | Portfolio Manager |
                    | (synthesis +      |
                    |  rebalancing)     |
                    +--------+---------+
                             |
                    +--------v---------+
                    | Trade Execution   |
                    | (market orders,   |
                    |  single batch)    |
                    +------------------+
```

The three analyst agents run **in parallel** (independent analyses). The portfolio manager
runs after all analysts complete.

### 6.2 LangGraph Workflow

```python
# agents/graph.py

from langgraph.graph import StateGraph, END

def build_equities_graph(branch_name: str) -> StateGraph:
    """
    Builds the LangGraph workflow for one equities branch.

    Nodes:
      - fetch_data: Pull market data for all screened stocks
      - news_analysis: Run News Analyst on all stocks (parallel)
      - fundamentals_analysis: Run Fundamentals Analyst on all stocks (parallel)
      - technical_analysis: Run Technical Analyst on all stocks (parallel)
      - portfolio_decision: Portfolio Manager synthesizes and generates orders
      - execute_trades: Submit orders to Trade Execution module

    Edges:
      fetch_data -> [news_analysis, fundamentals_analysis, technical_analysis]
      [news_analysis, fundamentals_analysis, technical_analysis] -> portfolio_decision
      portfolio_decision -> execute_trades
      execute_trades -> END
    """
    ...
```

### 6.3 Analyst Output Schema

All three analyst agents produce the same structured output per stock:

```python
class StockSignal(BaseModel):
    """Output from a single analyst agent for a single stock."""

    symbol: str
    analyst_type: str              # "news" | "fundamentals" | "technical"
    bullish_score: int             # 1-10 (1 = very bearish, 10 = very bullish)
    confidence: int                # 1-10 (1 = very uncertain, 10 = very confident)
    summary: str                   # Brief reasoning (1-2 sentences)
```

**Score interpretation:**
- 1-3: Bearish signal
- 4-6: Neutral / mixed signal
- 7-10: Bullish signal

**Confidence interpretation:**
- 1-3: Low confidence (limited/conflicting data)
- 4-6: Moderate confidence
- 7-10: High confidence (clear signal with strong supporting data)

---

## 7. Analyst Agents

### 7.1 News Analyst

**Purpose**: Assess recent news sentiment, catalysts, and risks for each candidate stock.

**Input data** (per stock):
- Recent news articles from Yahoo Finance (already available via Data Platform)
- Company name and sector for context

**Analysis focus**:
- Positive/negative sentiment in recent coverage
- Material catalysts (product launches, partnerships, regulatory changes)
- Risk factors (lawsuits, executive departures, guidance cuts)
- Industry-level trends affecting the stock

**Output**: `StockSignal` with `analyst_type="news"`

### 7.2 Fundamentals Analyst

**Purpose**: Analyze financial health, earnings quality, and valuation for each candidate.

**Input data** (per stock):
- Key financial metrics from Yahoo Finance (P/E, P/B, margins, growth rates)
- Recent earnings data from SEC EDGAR (revenue, EPS, guidance)
- Line items from Data Platform (income statement, balance sheet)

**Analysis focus**:
- Earnings quality (beat/miss, guidance trajectory)
- Revenue and margin trends
- Valuation relative to peers and historical norms
- Balance sheet strength
- Growth-specific: Revenue acceleration, TAM expansion
- Value-specific: Asset undervaluation, catalyst for re-rating

**Output**: `StockSignal` with `analyst_type="fundamentals"`

### 7.3 Technical Analyst

**Purpose**: Identify price trends, momentum, and technical patterns for each candidate.

**Input data** (per stock):
- Historical price/volume data from Yahoo Finance (6-12 months)
- Current price relative to moving averages, 52-week range

**Analysis focus**:
- Trend direction (above/below key moving averages)
- Momentum (rate of change, RSI-like assessment)
- Volume patterns (accumulation/distribution)
- Support and resistance levels
- Relative strength vs. sector and index

**Output**: `StockSignal` with `analyst_type="technical"`

### 7.4 Agent Configuration

```python
class AnalystLLMConfig(BaseModel):
    """Per-analyst LLM configuration. Allows each analyst to use a different model."""

    model: str = "claude-sonnet-4-6"
    temperature: float = 0.3


class AgentsConfig(BaseModel):
    """Agent-level configuration."""

    # Per-analyst LLM settings (each analyst can use a different model)
    news_analyst: AnalystLLMConfig = AnalystLLMConfig()
    fundamentals_analyst: AnalystLLMConfig = AnalystLLMConfig()
    technical_analyst: AnalystLLMConfig = AnalystLLMConfig()
    portfolio_manager: AnalystLLMConfig = AnalystLLMConfig(temperature=0.2)

    max_concurrent_analyses: int = 10           # Parallel LLM calls per analyst

    # Composite score weights (must sum to 1.0)
    weight_fundamentals: float = 0.40
    weight_news: float = 0.35
    weight_technical: float = 0.25
```

---

## 8. Portfolio Manager

### 8.1 Synthesis: Composite Score

The portfolio manager computes a deterministic composite score per stock from the
three analyst signals:

```
composite_score = (
    weight_fundamentals * fundamentals_signal.bullish_score +
    weight_news * news_signal.bullish_score +
    weight_technical * technical_signal.bullish_score
)

composite_confidence = (
    weight_fundamentals * fundamentals_signal.confidence +
    weight_news * news_signal.confidence +
    weight_technical * technical_signal.confidence
)
```

Default weights: `fundamentals=0.40, news=0.35, technical=0.25`

### 8.2 Stock Selection

1. Rank all screened stocks by `composite_score * composite_confidence` (conviction-weighted)
2. Select top ~20 stocks (soft target)
3. Enforce hard guardrails: minimum 10, maximum 30 holdings

### 8.3 Position Sizing

Target weight for each selected stock is proportional to its conviction score:

```
raw_weight[i] = composite_score[i] * composite_confidence[i]
target_weight[i] = raw_weight[i] / sum(all raw_weights)
```

After normalization, enforce the 50% position cap:

```
if target_weight[i] > 0.50:
    target_weight[i] = 0.50
    redistribute excess pro-rata to remaining positions
```

### 8.4 Rebalancing Logic

The portfolio manager performs **incremental rebalancing**:

1. **Compare** target portfolio (from current analysis) to current holdings
2. **Identify changes**:
   - New positions: stocks in target but not in current portfolio -> BUY
   - Removed positions: stocks in current portfolio but not in target -> SELL
   - Weight adjustments: stocks in both but weight changed beyond threshold -> BUY/SELL to adjust
3. **Generate orders**: Calculate share quantities from target weights, current NAV, and current prices
4. **Minimum trade threshold**: Skip adjustments smaller than a configurable percentage
   of portfolio NAV (avoids excessive small trades)

### 8.5 Portfolio Configuration

```python
class PortfolioConfig(BaseModel):
    """Portfolio construction parameters."""

    target_holdings: int = 20              # Soft target
    min_holdings: int = 10                 # Hard floor
    max_holdings: int = 30                 # Hard ceiling
    max_position_weight: float = 0.50      # 50% cap per stock
    min_rebalance_threshold: float = 0.02  # Skip trades < 2% of NAV
    min_composite_score: float = 4.0       # Don't hold stocks below this score
```

---

## 9. Trade Execution Integration

### 9.1 Order Generation

The portfolio manager's rebalancing output is a list of `RebalanceOrder` objects:

```python
class RebalanceOrder(BaseModel):
    """A single rebalancing action."""

    symbol: str
    side: OrderSide            # BUY or SELL
    quantity: int              # Number of shares
    reason: str                # "new_position" | "removed_position" | "weight_adjustment"
```

### 9.2 Execution Flow

```python
# Simplified execution flow within EquitiesBranchService

async def execute_rebalance(
    self,
    orders: list[RebalanceOrder],
    branch_id: UUID,
) -> list[Trade]:
    """Submit all rebalance orders as a single batch."""
    trades = []
    for order in orders:
        result = await self.trade_execution_service.submit_order(
            OrderRequest(
                branch_id=branch_id,
                symbol=order.symbol,
                side=order.side,
                order_type=OrderType.MARKET,
                quantity=order.quantity,
            )
        )
        trades.append(result)
    return trades
```

All orders are market orders, submitted sequentially within a single batch.
The existing Trade Execution module handles validation, paper fills, portfolio updates,
and event logging.

---

## 10. SEC EDGAR Integration

### 10.1 Adapter

New data adapter added to the Data Platform module for earnings data.

```python
# data_platform/adapters/sec_edgar.py

class SECEdgarAdapter:
    """Fetches earnings and financial filing data from SEC EDGAR."""

    async def get_recent_filings(
        self,
        symbol: str,
        filing_types: list[str] = ["10-K", "10-Q"],
        limit: int = 4,
    ) -> list[Filing]:
        """Get recent SEC filings for a company."""
        ...

    async def get_earnings_data(
        self,
        symbol: str,
        quarters: int = 4,
    ) -> list[QuarterlyEarnings]:
        """Get quarterly earnings data (revenue, EPS, guidance)."""
        ...
```

### 10.2 Data Models

```python
class Filing(BaseModel):
    symbol: str
    filing_type: str           # "10-K", "10-Q"
    filing_date: date
    period_end: date
    url: str                   # EDGAR filing URL

class QuarterlyEarnings(BaseModel):
    symbol: str
    fiscal_quarter: str        # e.g., "Q3 2025"
    revenue: float | None
    eps: float | None
    revenue_estimate: float | None
    eps_estimate: float | None
    revenue_surprise_pct: float | None
    eps_surprise_pct: float | None
```

---

## 11. Database Schema Changes

Phase 2 adds tables for screening results and agent signals. All existing Phase 1 tables
remain unchanged.

```sql
-- Screening run results (audit trail for what passed/failed screening)
CREATE TABLE screening_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    branch_id       UUID NOT NULL REFERENCES branches(id),
    branch_name     VARCHAR(20) NOT NULL,    -- 'equities-growth' or 'equities-value'
    run_date        TIMESTAMPTZ NOT NULL DEFAULT now(),
    universe_count  INTEGER NOT NULL,         -- Total stocks in universe
    passed_count    INTEGER NOT NULL,         -- Stocks that passed screening
    config_snapshot JSONB NOT NULL,           -- Screening config at time of run
    passed_symbols  JSONB NOT NULL            -- List of symbols that passed
);

-- Agent signals (one row per analyst per stock per run)
CREATE TABLE agent_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    screening_run_id UUID NOT NULL REFERENCES screening_runs(id),
    symbol          VARCHAR(20) NOT NULL,
    analyst_type    VARCHAR(20) NOT NULL,     -- 'news', 'fundamentals', 'technical'
    bullish_score   INTEGER NOT NULL,         -- 1-10
    confidence      INTEGER NOT NULL,         -- 1-10
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Portfolio decisions (audit trail for what the PM decided)
CREATE TABLE portfolio_decisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    screening_run_id UUID NOT NULL REFERENCES screening_runs(id),
    branch_id       UUID NOT NULL REFERENCES branches(id),
    branch_name     VARCHAR(20) NOT NULL,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    target_holdings JSONB NOT NULL,           -- {symbol: target_weight, ...}
    current_holdings JSONB NOT NULL,          -- {symbol: current_weight, ...}
    orders_generated JSONB NOT NULL,          -- List of RebalanceOrder dicts
    composite_scores JSONB NOT NULL           -- {symbol: {score, confidence}, ...}
);

CREATE INDEX idx_screening_runs_branch ON screening_runs(branch_id, run_date DESC);
CREATE INDEX idx_agent_signals_run ON agent_signals(screening_run_id);
CREATE INDEX idx_portfolio_decisions_branch ON portfolio_decisions(branch_id, decided_at DESC);
```

---

## 12. API Endpoints

New endpoints for the equities branch. These are primarily for triggering runs and
inspecting results -- the core workflow runs autonomously on a quarterly schedule.

```
POST /api/v1/equities/{branch_name}/run
    Trigger a full pipeline run (screen -> analyze -> rebalance) for a branch.
    branch_name: "equities-growth" | "equities-value"
    Returns: RunResult (screening summary, signals, orders, trades)

GET  /api/v1/equities/{branch_name}/signals
    Get the latest agent signals for a branch.
    Query params: symbol (optional), analyst_type (optional)
    Returns: list[StockSignal]

GET  /api/v1/equities/{branch_name}/screening-runs
    Get screening run history.
    Returns: list[ScreeningRun] (paginated)

GET  /api/v1/equities/{branch_name}/decisions
    Get portfolio decision history.
    Returns: list[PortfolioDecision] (paginated)

GET  /api/v1/equities/config
    Get current equities branch configuration (screening thresholds, weights, etc.).
    Returns: EquitiesConfig

PUT  /api/v1/equities/config
    Update equities branch configuration.
    Body: Partial EquitiesConfig
    Returns: Updated EquitiesConfig
```

---

## 13. Event Integration

Phase 2 uses the existing event log infrastructure. New events logged during the pipeline:

| Event Type | When | Key Data |
|------------|------|----------|
| `SignalGenerated` | After each analyst completes | symbol, analyst_type, bullish_score, confidence |
| `AllocationDirective` | After portfolio manager decides | target_holdings, rebalance_orders |
| `TradeRequested` | For each rebalance order | symbol, side, quantity (existing event) |
| `TradeExecuted` | After each fill | symbol, price, quantity (existing event) |
| `PortfolioUpdated` | After all trades settle | new positions, cash, NAV (existing event) |

---

## 14. Build Order

Implementation should proceed in this order, with each step building on the previous:

### Step 1: Configuration & Models
- `equities/config.py` -- All configuration classes (ScreeningConfig, PortfolioConfig, AgentsConfig, EquitiesConfig)
- `equities/models.py` -- Pydantic models (UniverseStock, StockSignal, RebalanceOrder, RunResult)
- Database migration for new tables (screening_runs, agent_signals, portfolio_decisions)

### Step 2: Universe Provider
- `universe/provider.py` -- ETF holdings fetcher using yfinance
- Unit tests for holdings parsing and caching

### Step 3: Screening Engine
- `universe/screener.py` -- ScreeningFilter ABC + Screener pipeline
- Implement all shared + growth + value filters
- Unit tests for each filter and the pipeline

### Step 4: SEC EDGAR Adapter
- `data_platform/adapters/sec_edgar.py` -- Earnings data from EDGAR
- Register adapter in Data Platform service
- Unit tests for EDGAR data parsing

### Step 5: Analyst Agents
- `agents/news_analyst.py` -- News Analyst with LLM integration
- `agents/fundamentals_analyst.py` -- Fundamentals Analyst with LLM + EDGAR data
- `agents/technical_analyst.py` -- Technical Analyst with price data
- Unit tests with mocked LLM responses

### Step 6: Portfolio Manager
- `agents/portfolio_manager.py` -- Composite scoring, stock selection, position sizing, order generation
- Unit tests for scoring, sizing, and rebalancing logic

### Step 7: LangGraph Workflow
- `agents/graph.py` -- Wire up the full pipeline as a LangGraph state graph
- Integration tests for the complete workflow

### Step 8: Branch Service & API
- `equities/service.py` -- EquitiesBranchService (orchestrates everything)
- `equities/api.py` -- FastAPI routes
- Wire into `dependencies.py` and `main.py`

### Step 9: End-to-End Testing
- Full pipeline test: universe -> screening -> analysis -> rebalancing -> trade execution
- Verify event log captures all steps
- Verify portfolio state is correct after rebalancing

---

## 15. Testing Strategy

Phase 2 has two categories of components: deterministic logic (screening, scoring, sizing)
and LLM-based agents (analysts). Each requires a different testing approach.

### 15.1 Tier 1: Unit Tests (Mocked Dependencies, CI)

All deterministic logic and agent schema validation. Mocked LLM and data sources.
Runs on every commit in CI.

**Universe Provider:**
- Parses ETF holdings response correctly
- Returns empty list on API failure (graceful degradation)
- Caching behavior (returns cached holdings within quarter)

**Screening Filters (each filter individually):**
- `LiquidityFilter`: Passes stocks above volume threshold, rejects below
- `MarketCapFilter`: Passes stocks above cap floor, rejects below
- `EarningsRecencyFilter`: Passes stocks with recent earnings, rejects stale
- `VolatilityFilter`: Rejects top N percentile by realized vol
- `LeverageFilter`: Rejects stocks above debt-to-equity cap
- `RevenueGrowthFilter`: Passes stocks with sufficient YoY growth
- `EarningsGrowthFilter`: Passes stocks with positive earnings growth
- `GrossMarginTrendFilter`: Rejects stocks with declining margins
- `EarningsSurpriseFilter`: Rejects stocks that missed earnings
- `MomentumFilter`: Rejects stocks with poor 6-month returns
- `PEGFilter`: Rejects extreme PEG ratios
- `PEFilter` / `PBFilter`: Percentile-based filtering within universe
- `FCFYieldFilter` / `DividendYieldFilter` / `ROEFilter`: Threshold checks
- `PriceRangeFilter`: 52-week range percentile check

**Screener Pipeline:**
- Filters compose correctly (output of one feeds into next)
- Empty universe after filtering handled gracefully
- Filter order does not affect final result
- Logging captures before/after counts per filter

**Analyst Agents (mocked LLM):**
- Output conforms to `StockSignal` schema
- `bullish_score` is within 1-10 range
- `confidence` is within 1-10 range
- `analyst_type` is set correctly per agent
- Handles LLM returning malformed/unexpected output (error recovery)
- Handles LLM timeout or API failure

**Portfolio Manager:**
- Composite score calculation matches expected formula
- Composite confidence calculation matches expected formula
- Stock selection picks top N by conviction (score * confidence)
- Hard guardrails enforced (min 10, max 30 holdings)
- Position sizing normalizes weights to sum to 1.0
- 50% position cap enforced with pro-rata redistribution
- Minimum composite score threshold filters out low-conviction stocks

**Order Generation:**
- New position (not in current portfolio) generates BUY order
- Removed position (in current but not in target) generates SELL order
- Weight increase generates BUY order for correct share delta
- Weight decrease generates SELL order for correct share delta
- Minimum trade threshold skips small adjustments
- Correct share quantity calculation from weight, NAV, and price

**SEC EDGAR Adapter:**
- Parses filing responses correctly
- Handles missing fields gracefully
- Rate limiting behavior

### 15.2 Tier 2: Integration Tests (Real LLM, Manual)

Real LLM calls against a small stock set. Run manually before releases.
Not included in CI (cost and latency).

**Single Analyst Integration:**
- Run News Analyst on 3 known stocks with real market data
- Run Fundamentals Analyst on 3 known stocks with real market + EDGAR data
- Run Technical Analyst on 3 known stocks with real price history
- For each: verify output is valid `StockSignal`, scores are within range, summary is non-empty

**Portfolio Manager Integration:**
- Feed real analyst signals (from above) into portfolio manager
- Verify composite scoring produces reasonable rankings
- Verify position sizing produces valid weights (sum to 1.0, cap respected)
- Verify order generation produces correct BUY/SELL orders

**Full Pipeline Integration:**
- Run complete workflow for one branch on ~10 screened stocks (not full universe)
- Verify: screening -> analysis -> portfolio decision -> order generation
- Verify all intermediate state is persisted (screening_runs, agent_signals, portfolio_decisions)

### 15.3 Tier 3: E2E Smoke Test (Real LLM, Full Pipeline, Manual)

Full end-to-end workflow against real data. Run manually.

```
Test: Full Equities Branch Run
  1. Create fund and equities-growth branch in DB
  2. Create portfolio with initial cash ($1,000,000)
  3. Trigger full pipeline run via POST /api/v1/equities/equities-growth/run
  4. Verify: universe fetched (VOOG, ~230 stocks)
  5. Verify: screening reduced count to ~40-50
  6. Verify: 3 analysts produced signals for all screened stocks
  7. Verify: portfolio manager selected ~20 holdings with valid weights
  8. Verify: rebalance orders generated and submitted
  9. Verify: trades executed via paper trading adapter
 10. Verify: portfolio positions updated correctly
 11. Verify: event log contains SignalGenerated, AllocationDirective,
             TradeRequested, TradeExecuted, PortfolioUpdated events
 12. Verify: screening_runs, agent_signals, portfolio_decisions tables populated
 13. Verify: GET endpoints return correct data for signals, decisions, screening runs
```

### 15.4 Test File Organization

```
tests/
├── unit/
│   ├── equities/
│   │   ├── test_universe_provider.py
│   │   ├── test_screener.py
│   │   ├── test_screening_filters.py
│   │   ├── test_news_analyst.py
│   │   ├── test_fundamentals_analyst.py
│   │   ├── test_technical_analyst.py
│   │   ├── test_portfolio_manager.py
│   │   └── test_order_generation.py
│   └── data_platform/
│       └── test_sec_edgar_adapter.py
├── integration/
│   └── equities/
│       ├── test_analyst_integration.py      # Tier 2: real LLM, 3 stocks
│       ├── test_pipeline_integration.py     # Tier 2: real LLM, 10 stocks
│       └── test_e2e_equities.py             # Tier 3: full pipeline
```

---

## 16. Compatibility with Phase 1

The equities branch is a **consumer** of all Phase 1 shared modules:

| Phase 1 Module | How Equities Uses It |
|----------------|---------------------|
| **Data Platform** | Prices, fundamentals, news for screening and agent analysis |
| **Trade Execution** | Submitting rebalance orders (paper trading) |
| **Portfolio** | Reading current holdings, updating positions post-trade |
| **Event Log** | Logging signals, decisions, and trades |

No modifications to Phase 1 modules are required. The equities branch only calls
existing service interfaces.

---

## 17. Future Enhancements (Out of Scope for Phase 2)

- Analyst reasoning and confidence justification in signal output
- LLM-based portfolio manager synthesis (replace deterministic formula)
- Intra-quarter monitoring and alerts (earnings surprises, price drops)
- Live broker adapter (Alpaca) for real trade execution
- Human approval gates via OpenClaw integration
- Backtesting framework to evaluate strategy performance historically
- Additional data sources (Bloomberg, alternative data, social sentiment)
