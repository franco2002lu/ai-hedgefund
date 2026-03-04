# Phase 3: Backtesting Infrastructure

This document defines the architecture for the backtesting module, which enables historical
strategy evaluation using the existing equities pipeline built in Phase 2.

> **Prerequisites**: All Phase 1 shared modules (Portfolio, Trade Execution, Data Platform,
> Event Log) and Phase 2 equities branch must be operational. See
> [PHASE1-SHARED-INFRASTRUCTURE.md](PHASE1-SHARED-INFRASTRUCTURE.md) and
> [PHASE2-EQUITIES-BRANCH.md](PHASE2-EQUITIES-BRANCH.md).

---

## 1. Overview

The backtesting module provides two complementary engines for evaluating trading strategies
against historical data:

| Engine | Purpose | Speed | Reuses Pipeline? |
|--------|---------|-------|-----------------|
| **Event-driven** | Full simulation: screen → analyze → rebalance → execute → track P&L | Minutes (depends on date range + rebalance frequency) | Yes — runs the complete LangGraph DAG per rebalance |
| **Vectorized screening** | Rapid screening parameter research across time | Seconds | Partial — runs filters only, no trade execution |

Both engines share the same foundational components (historical data store, time management,
configuration) but differ in what they execute per time step.

### Core Design Principle: Adapter Substitution

The existing codebase was built with adapter abstractions (`BrokerAdapter`, `PriceDataAdapter`,
`FundamentalsAdapter`, repository ABCs). Backtesting works by **substituting adapter
implementations** while keeping the pipeline logic untouched:

```
Live System                          Backtest System
─────────────                        ──────────────
YahooFinanceAdapter                  HistoricalDataAdapter
  → live API calls                     → pre-loaded in-memory store
PaperTradingAdapter                  BacktestBrokerAdapter
  → fetches live current price         → uses historical close price
PostgresPortfolioRepository          InMemoryPortfolioRepository
  → writes to PostgreSQL               → writes to dict in memory
PostgresOrderRepository              InMemoryOrderRepository
  → writes to PostgreSQL               → writes to list in memory
```

The `PortfolioService`, `TradeExecutionService`, `PortfolioManager`, `Screener`, and the entire
LangGraph DAG run **unchanged** — they call the same interface methods, unaware that the
underlying data source is historical rather than live.

---

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Engine architecture | Hybrid: event-driven + vectorized | Event-driven for realistic simulation; vectorized for rapid filter research. Different use cases need different performance trade-offs |
| Historical data source | Yahoo Finance (yfinance) pre-downloaded | Already integrated in Phase 1. Free, supports 20+ years of daily OHLCV. Downloaded once, served from memory |
| Data storage | In-memory dicts during backtest | Speed: no DB round-trips. A 2-year daily backtest with 20 positions involves ~500 days × ~40 trades per rebalance = ~20K trade operations. In-memory is orders of magnitude faster |
| Results persistence | PostgreSQL (4 new tables) after backtest completes | Permanent record for comparison. Only written once at the end, not during simulation. Includes per-rebalance signal/score traceability |
| Time management | `TimeProvider` protocol injected into adapters | Clean abstraction; `BacktestTimeProvider` controls simulated date, `LiveTimeProvider` for production. No global mutable state |
| Point-in-time enforcement | `HistoricalDataAdapter` clamps all queries to `≤ time_provider.today()` | Prevents look-ahead bias. Data adapter layer enforces this, not the caller |
| LLM agents in backtests | Optional: quantitative analysts by default, opt-in for live LLM | LLM calls are expensive ($0.01-0.03 per stock per analyst) and non-deterministic. Quantitative analysts provide fast, free, reproducible signals. Live LLM available for prompt validation |
| Rebalance scheduling | Trading-day-based intervals (weekly=5, biweekly=10, monthly=21) | Aligns with market reality (no weekends/holidays). First trading day always rebalances |
| Fundamentals handling | Pre-cache current fundamentals, use throughout backtest | **Known limitation**: yfinance doesn't provide historical fundamental snapshots. Documented as look-ahead bias for fundamental-based filters. Future: integrate SEC EDGAR XBRL for true point-in-time fundamentals |
| Pipeline reuse | Create new `EquitiesBranchService` instance per backtest | Live singleton must not be modified. Backtest instance wired to historical `DataPlatformService` and quantitative analysts. Same config, same pipeline code |
| LLM analyst isolation | Always create NEW analyst instances for backtests, never reuse live singletons | LLM analysts bind `data_service` at construction time (in `dependencies.py`). Reusing the singleton would route data calls through the live `DataPlatformService`, leaking future data and bypassing the `HistoricalDataAdapter`. When `use_llm_agents=True`, new analyst instances are created with the backtest's `DataPlatformService` |
| Null Object over None guards | `NoOpCache` and `NoOpRateLimiter` instead of `if self.cache:` checks | Avoids scattering None checks across every method in `DataPlatformService`. Cleaner, more testable, and follows the existing adapter substitution pattern |
| TimeProvider injection into analysts | `TechnicalAnalyst` and `FundamentalsAnalyst` accept `TimeProvider` for date references | `TechnicalAnalyst` currently hardcodes `date.today()` for lookback windows. In backtests this produces inverted date ranges (future start > historical end) yielding zero price bars. Must use `time_provider.today()` instead |
| Repository ABC alignment | Add missing methods to ABCs; change service constructors to accept ABCs | `PortfolioService` currently takes concrete `PostgresXxxRepository` types. `handle_trade_executed()` calls `update_portfolio_fields()` which is not on the `PortfolioRepository` ABC. In-memory repos would crash at runtime. Fix by adding the missing method to the ABC and updating constructor type hints |
| Strategy parameter overrides | `BacktestConfig` includes optional `EquitiesConfig` override | Enables testing variations of screening thresholds, portfolio weights, and position limits without modifying global config. Essential for strategy optimization |
| LLM signal caching | Cache LLM responses keyed by `(date, symbol, analyst_type)` when `use_llm_agents=True` | LLM calls are expensive and non-deterministic. Caching makes re-runs with the same date range reproducible and avoids redundant API costs |
| Backtest run status tracking | `status` enum column on `backtest_runs` table | Enables crash recovery and progress monitoring. Without it, a crashed backtest stays in limbo with no `completed_at` but no error either |
| DI factory pattern | `BacktestContext` factory encapsulates all backtest DI wiring | Extracts the 15-line initialization sequence from `BacktestEngine.run()` into a testable, reusable factory. Reduces engine complexity and makes unit testing straightforward |
| Background execution | FastAPI `BackgroundTasks` for API-triggered backtests | Backtests take minutes. API returns immediately with `backtest_id`; caller polls for results |
| Benchmark comparison | SPY by default, configurable | SPY is the standard equity benchmark. Loaded alongside universe data in the same preload step |

---

## 3. Project Structure

New files and directories. Existing Phase 1/2 structure unchanged except for noted modifications.

```
app/
├── common/
│   └── interfaces/
│       └── time.py                              # TimeProvider ABC (shared by backtest + analysts)
├── modules/
│   ├── backtest/                                # Backtesting Module
│   │   ├── __init__.py
│   │   ├── config.py                            # BacktestConfig, RebalanceFrequency, LLMBacktestConfig
│   │   ├── time_provider.py                     # LiveTimeProvider, BacktestTimeProvider
│   │   ├── models.py                            # DailySnapshot, BacktestTrade, PerformanceMetrics,
│   │   │                                        #   BenchmarkComparison, BacktestResult, ScreeningSnapshot
│   │   ├── adapters/
│   │   │   ├── __init__.py
│   │   │   ├── historical_data.py               # HistoricalPriceStore + HistoricalDataAdapter
│   │   │   └── backtest_broker.py               # BacktestBrokerAdapter
│   │   ├── context.py                           # BacktestContext factory (DI wiring)
│   │   ├── state.py                             # 6 in-memory repository implementations
│   │   ├── quantitative_analysts.py             # Deterministic signal generators (no LLM)
│   │   ├── engine.py                            # BacktestEngine (event-driven orchestrator)
│   │   ├── vectorized.py                        # VectorizedScreeningEngine
│   │   ├── analytics.py                         # PerformanceCalculator
│   │   ├── repository.py                        # PostgresBacktestRepository (results persistence)
│   │   └── api.py                               # FastAPI routes
│   └── data_platform/
│       ├── noop.py                              # NoOpCache, NoOpRateLimiter (Null Object pattern)

tests/
├── unit/
│   └── backtest/
│       ├── conftest.py                          # Shared builders (_make_price_bar, _make_price_series,
│       │                                        #   _make_backtest_config, _make_daily_snapshot, _make_backtest_trade)
│       ├── test_config.py                       # 14 tests — config validation, enums, defaults
│       ├── test_time_provider.py                # 11 tests — ABC, LiveTimeProvider, BacktestTimeProvider
│       ├── test_models.py                       # 12 tests — domain model creation, defaults, optionals
│       ├── test_noop.py                         #  7 tests — NoOpCache, NoOpRateLimiter
│       ├── test_historical_data.py              # 19 tests — store CRUD, adapter point-in-time enforcement
│       ├── test_backtest_broker.py              # 14 tests — slippage, limit orders, rejections
│       ├── test_in_memory_state.py              # 32 tests — 6 repo impls, ABC compliance, CRUD
│       ├── test_quantitative_analysts.py        # 27 tests — deterministic scoring, linear interpolation
│       ├── test_analytics.py                    # 25 tests — performance metrics, benchmarks, edge cases
│       ├── test_engine.py                       # 16 tests — rebalance schedule, simulation loop, errors
│       ├── test_vectorized.py                   #  8 tests — screening intervals, forward returns
│       ├── test_context.py                      #  8 tests — DI wiring, in-memory repos, uuid5 IDs
│       ├── test_repository.py                   #  5 tests — save/get/list with mocked session
│       ├── test_api.py                          # 14 tests — all endpoints via TestClient
│       └── test_equities_service_fix.py         #  4 tests — instrument_ids parameter
└── integration/
    └── backtest/
        └── test_backtest_integration.py         #  4 tests — real yfinance data, @pytest.mark.integration
```

### Existing Files Modified

| File | Change | Why |
|------|--------|-----|
| `app/modules/data_platform/service.py` | Accept `NoOpCache` / `NoOpRateLimiter` via Null Object pattern (see Section 7.1) | Currently `None or DataCache()` creates a default. Backtest passes Null Objects that implement the interface but do nothing |
| `app/modules/equities/service.py` | Add optional `instrument_ids` parameter to `run_pipeline()` | **Critical**: without a DB session, `instrument_ids` stays empty and all trades are silently skipped at line 203 |
| `app/common/interfaces/repositories.py` | Add `update_portfolio_fields()` to `PortfolioRepository` ABC | Method exists on `PostgresPortfolioRepository` but not the ABC. In-memory repo would crash when `PortfolioService.handle_trade_executed()` calls it |
| `app/modules/portfolio/service.py` | Change constructor type hints from concrete repos to ABCs | Enables in-memory repos to be injected without type-checker warnings. No runtime behavior change |
| `app/modules/trade_execution/service.py` | Change `order_repo` and `trade_repo` constructor type hints from concrete repos to `OrderRepository` and `TradeRepository` ABCs | Same issue as `PortfolioService` — concrete `PostgresOrderRepository` and `PostgresTradeRepository` types prevent clean in-memory repo injection. `broker` and `event_log` already use ABCs |
| `app/modules/equities/agents/technical_analyst.py` | Accept `TimeProvider` for date references instead of `date.today()` | Hardcoded `date.today()` produces inverted date ranges in backtests (future > historical), yielding zero price bars |
| `app/modules/equities/agents/fundamentals_analyst.py` | Accept `TimeProvider` for date references; pass `time_provider.today()` as `end_date` to `data_service.get_metrics()` | The analyst itself doesn't call `date.today()` directly, but `DataPlatformService.get_metrics()` defaults `end_date` to `date.today()` when callers pass `None`. Must pass explicit date for point-in-time correctness |
| `app/modules/equities/agents/news_analyst.py`, `fundamentals_analyst.py`, `technical_analyst.py` | Add error isolation to `analyze_batch()`: wrap per-stock `analyze()` calls in try/except, return neutral signal `(score=5, confidence=1)` on failure | Currently `asyncio.gather()` has no `return_exceptions=True` — one failed LLM call or data fetch kills the entire batch. Critical for robustness when `use_llm_agents=True` |
| `app/modules/equities/universe/screener.py` | Pass explicit `end_date` parameter to `_get_metric()` → `data_service.get_metrics()` calls | Screening filters call `get_metrics(symbol)` without `end_date`, causing `DataPlatformService` to default to `date.today()`. In backtests, the `HistoricalDataAdapter` pre-caches fundamentals so this is functionally harmless, but passing the explicit date is more robust and consistent |
| `app/main.py` | Register backtest router | Route registration |
| `app/db/models.py` | Add 4 ORM models: `BacktestRunModel`, `BacktestSnapshotModel`, `BacktestTradeModel`, `BacktestRebalanceDetailModel` | Results + rebalance traceability persistence |

---

## 4. Component Specifications

### 4.1 Configuration (`config.py`)

```python
class RebalanceFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"        # Every 5 trading days
    BIWEEKLY = "biweekly"    # Every 10 trading days
    MONTHLY = "monthly"      # Every 21 trading days

class BacktestStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class LLMBacktestConfig(BaseModel):
    """Configuration for LLM agent usage during backtests."""
    cache_signals: bool = True           # Cache LLM responses for reproducibility
    max_llm_calls_per_rebalance: int = 60  # Safety cap to prevent runaway costs
    # Signal cache key: (date, symbol, analyst_type) → StockSignal

class BacktestConfig(BaseModel):
    start_date: date
    end_date: date
    initial_capital: float = 1_000_000.0
    rebalance_frequency: RebalanceFrequency = RebalanceFrequency.WEEKLY
    branch_name: str = "growth"          # "growth" or "value"
    use_llm_agents: bool = False         # False = quantitative-only signals
    llm_config: LLMBacktestConfig = LLMBacktestConfig()  # Only used when use_llm_agents=True
    slippage_bps: float = 5.0
    commission_per_trade: float = 0.0
    benchmark_symbol: str = "SPY"
    equities_config_override: EquitiesConfig | None = None  # Override strategy params for optimization
```

**Validation rules**:
- `end_date > start_date`
- `initial_capital > 0`
- `slippage_bps >= 0`
- All params have sensible defaults for quick iteration

**Strategy overrides**: `equities_config_override` allows testing variations of screening thresholds
(e.g., `min_market_cap`, `max_peg_ratio`), portfolio weights, and position limits without
modifying global config. When `None`, the live `EquitiesConfig` is used as-is.

### 4.2 Time Provider (`app/common/interfaces/time.py` + `backtest/time_provider.py`)

The ABC lives in `app/common/interfaces/time.py` since it's shared by both backtest components
and existing analyst agents. Implementations live in the backtest module.

```python
# app/common/interfaces/time.py
class TimeProvider(ABC):
    def now(self) -> datetime: ...       # Full datetime (for event timestamps)
    def today(self) -> date: ...         # Date only (for price lookups)

# app/modules/backtest/time_provider.py
class LiveTimeProvider(TimeProvider):
    # Returns datetime.now(UTC) / date.today()
    # Default for production; injected into analysts to replace date.today() calls

class BacktestTimeProvider(TimeProvider):
    def __init__(self, initial_date: date): ...
    def advance_to(self, new_date: date): ...
    # now() returns datetime at 16:00 UTC (market close) on simulated date
    # today() returns the simulated date
```

The `BacktestTimeProvider` is the "clock" that the engine advances day by day. All
time-dependent components (broker, data adapter, analysts, events) use this instead of
`datetime.now()` or `date.today()`.

**Critical**: `TechnicalAnalyst` currently hardcodes `date.today()` to compute lookback windows
(e.g., 6-month return). In a backtest simulating 2024-03-15, `date.today()` returns 2026-03-02
(actual today), producing an inverted date range where `start > end` and yielding zero price bars.
The `TimeProvider` injection fixes this — see Section 7.3 for the analyst modification details.

### 4.3 Historical Data Adapter (`adapters/historical_data.py`)

**Two classes with distinct responsibilities:**

#### `HistoricalPriceStore`
In-memory store of all OHLCV data for the backtest window. Pre-loaded once, then queried
thousands of times with O(1) dict lookups. Uses **adjusted close** prices to correctly account
for splits and dividends.

```python
class HistoricalPriceStore:
    # Internal: {symbol: {date: PriceBar}} for O(1) point lookups
    # Internal: {symbol: [PriceBar]} ordered for range queries

    async def preload(symbols, start_date, end_date, lookback_days=252):
        """Download all OHLCV from yfinance in batches of 20.
        Includes lookback buffer for technical indicators.
        Uses adjusted close (auto_adjust=True) for split/dividend correctness."""

    def get_bar(symbol, date) -> PriceBar | None
    def get_bars(symbol, start, end) -> list[PriceBar]
    def get_close(symbol, date) -> float | None
    def get_latest_close(symbol, as_of) -> float | None    # Most recent close ≤ as_of
    def get_trading_days(start, end) -> list[date]          # Union across all symbols
```

**Preload error handling**: `preload()` must handle partial failures gracefully since individual
symbols may fail (delisted, bad ticker, yfinance timeout):

```
preload() behavior:
  - Downloads in batches of 20 symbols via yfinance.download(batch, ...)
  - Per-batch timeout: 30 seconds
  - Per-batch retry: 1 retry with exponential backoff on network errors
  - Logs progress: "Preloading batch 3/12 (symbols 41-60)"
  - On per-symbol failure: logs warning, continues with remaining symbols
  - After all batches: logs summary "Loaded {N}/{M} symbols successfully"
  - Raises BacktestDataError only if < 50% of symbols loaded (catastrophic failure)
  - Individual missing symbols are tracked in store.failed_symbols: set[str]
```

#### `HistoricalDataAdapter`
Implements `PriceDataAdapter` + `FundamentalsAdapter`. Wraps the store and enforces
point-in-time correctness via `BacktestTimeProvider`.

```python
class HistoricalDataAdapter(PriceDataAdapter, FundamentalsAdapter):
    name = "backtest_historical"

    async def get_prices(symbol, start_date, end_date, interval="1d") -> list[PriceBar]:
        # Clamps end_date to min(end_date, time_provider.today())
        # Returns bars from store within the clamped range

    async def get_current_price(symbol) -> float | None:
        # Returns store.get_latest_close(symbol, time_provider.today())
        # NOTE: get_current_price() is NOT on the PriceDataAdapter ABC — it's a
        # concrete method on YahooFinanceAdapter. DataPlatformService.get_current_price()
        # uses hasattr() to check for it. HistoricalDataAdapter adds it as a concrete
        # method matching YahooFinanceAdapter's convention. This works at runtime via
        # duck typing. Adding it to the PriceDataAdapter ABC is a future cleanup.

    async def get_metrics(symbol, ...) -> list[dict]:
        # Returns from pre-cached fundamentals dict
        # Known limitation: uses current fundamentals for all dates

    async def get_company_facts(symbol) -> dict:
        # Returns from pre-cached facts dict
```

**Point-in-time guarantee**: The adapter layer ensures no data from the future leaks into the
simulation. Callers (screener, analysts, portfolio manager) don't need to think about time —
they call the same interfaces they always do.

### 4.4 Backtest Broker Adapter (`adapters/backtest_broker.py`)

Implements `BrokerAdapter`. Identical slippage/commission model to `PaperTradingAdapter`,
but uses historical close prices instead of live market data.

```python
class BacktestBrokerAdapter(BrokerAdapter):
    def __init__(store, time_provider, slippage_bps=5.0, commission_per_trade=0.0): ...

    async def submit_order(order: OrderRequest) -> OrderResult:
        # 1. Get historical close price: store.get_latest_close(symbol, time_provider.today())
        # 2. Apply slippage (same as PaperTradingAdapter):
        #    BUY/COVER: fill_price = close * (1 + slippage_bps/10000)
        #    SELL/SHORT: fill_price = close * (1 - slippage_bps/10000)
        # 3. Check limit order conditions
        # 4. Return OrderResult with Trade (execution_mode=PAPER, executed_at=time_provider.now())
        # 5. If no price available for date: return rejection
```

### 4.5 In-Memory State (`state.py`)

Six repository implementations matching the ABCs in `app/common/interfaces/repositories.py`:

| Class | Implements ABC | Backing Store | Notes |
|-------|---------------|--------------|-------|
| `InMemoryPortfolioRepository` | `PortfolioRepository` | `dict[branch_id, PortfolioSummary]` | Implements `update_portfolio_fields()` via `model_copy(update=...)` — this method is added to the ABC as part of this work (see Section 7.4) |
| `InMemoryPositionRepository` | `PositionRepository` | `dict[portfolio_id, dict[symbol, Position]]` | Flat positions auto-deleted |
| `InMemorySnapshotRepository` | `SnapshotRepository` | `list[PortfolioSnapshot]` | Direct `add_snapshot()` method for backtest use |
| `InMemoryOrderRepository` | `OrderRepository` | `dict[order_id, Order]` | Status updates via `model_copy()` |
| `InMemoryTradeRepository` | `TradeRepository` | `list[Trade]` | Exposes `all_trades` property for post-run analysis |
| `InMemoryEventLogRepository` | `EventLogRepository` | `list[dict]` | Events stored as dicts (`.model_dump()`) |

These implement the same ABCs that the Postgres repositories implement. After the ABC alignment
changes (Section 7.4), `PortfolioService` and `TradeExecutionService` accept ABC types in their
constructors, so the in-memory repos are injected cleanly without relying on duck typing.

### 4.6 Quantitative Analysts (`quantitative_analysts.py`)

Deterministic signal generators that replace LLM analysts when `use_llm_agents=False`.
Each implements the same interface as the LLM analysts (`analyze(stock)` and
`analyze_batch(stocks, max_concurrent)`). All accept a `TimeProvider` to use
`time_provider.today()` for date-dependent calculations.

```python
class QuantitativeNewsAnalyst:
    # Returns neutral-biased signal with slight variation based on recent momentum:
    #   If 1-month price change > 3%: score=6 (slight positive sentiment proxy)
    #   If 1-month price change < -3%: score=4 (slight negative)
    #   Otherwise: score=5
    #   Confidence=3 (low — this is a crude proxy, not real news analysis)
    # Rationale: A constant (5, 3) would make news weight (0.35) a dead multiplier,
    # diluting differentiation. Using momentum as a proxy adds signal without LLM cost.

class QuantitativeFundamentalsAnalyst:
    # Continuous scoring based on metrics from DataPlatformService:
    #   Revenue growth:  linear scale [−10%, +30%] → [−2, +2]
    #   ROE:             linear scale [0%, 25%] → [−1, +2]
    #   Debt/Equity:     linear scale [2.0, 0.3] → [−1, +1]
    #   Earnings growth: linear scale [−10%, +30%] → [−1, +1]
    #   FCF yield:       > 5%: +1, > 10%: +2
    # Base score = 5, add components, clamp to [1, 10]
    # Confidence = 6 (higher than news — fundamentals are more reliable quantitatively)

class QuantitativeTechnicalAnalyst:
    # Continuous scoring based on price data:
    #   6-month return:  linear scale [−20%, +30%] → [−2, +3]
    #   RSI (14-day):    > 70: −1 (overbought), < 30: +1 (oversold)
    #   Volatility:      linear scale [20%, 50%] → [0, −2]
    #   50/200 SMA cross: above → +1, below → −1
    # Base score = 5, add components, clamp to [1, 10]
    # Confidence = 5 (medium — purely backward-looking)
```

**Design rationale**: Continuous (linear interpolation) scoring produces differentiated signals
across the universe, enabling the portfolio manager to actually rank and select stocks.
Step-function scoring (e.g., `> 10%: +1`) clusters stocks into few buckets, making conviction
scores near-identical and reducing portfolio construction quality.

**Missing-signal behavior in `PortfolioManager.compute_composite_scores()`**: If any analyst
fails to produce a signal for a stock, the portfolio manager silently skips that analyst's
weight — the composite score is computed from fewer than 3 signals without renormalizing weights.
For example, a stock with only fundamentals (0.40) and news (0.35) signals has a max composite
score of 7.5 instead of 10.0, systematically penalizing it. With `analyze_batch()` error
isolation (Section 7.5), most failures now produce neutral fallback signals `(score=5,
confidence=1)` instead of missing entries, which mitigates this issue. However, quantitative
analysts should also use try/except internally to handle missing price/metric data gracefully
and return a neutral fallback rather than raising an exception.

When `use_llm_agents=True`, fresh analyst instances are created with the backtest's
`DataPlatformService` and `TimeProvider`. LLM responses are cached in an in-memory dict
scoped to the `BacktestContext` instance, keyed by `(date, symbol, analyst_type)` when
`llm_config.cache_signals=True`. The cache is not shared across backtest runs. Cross-run
cache sharing (e.g., via DB or file) is a future improvement — it would require including
model version and prompt hash in the cache key to avoid stale results after prompt changes.

### 4.7 Performance Analytics (`analytics.py`)

```python
class PerformanceCalculator:
    TRADING_DAYS_PER_YEAR = 252
    RISK_FREE_RATE = 0.04  # Annual (~current T-bill rate)

    def compute_metrics(snapshots, trades) -> PerformanceMetrics:
        # Portfolio metrics:
        #   total_return, annualized_return, volatility (annualized)
        #   sharpe_ratio = (mean_excess_return / daily_vol) * sqrt(252)
        #   sortino_ratio = (mean_excess_return / downside_dev) * sqrt(252)
        #   max_drawdown (% from peak), max_drawdown_duration_days
        #   calmar_ratio = annualized_return / max_drawdown
        #
        # Trade metrics (from round-trip BUY→SELL pairs):
        #   total_trades, win_rate, profit_factor, avg_win, avg_loss
        #
        # Exposure metrics:
        #   avg_position_count, max_position_count, avg_long_exposure

    def compute_benchmark_comparison(snapshots, store, benchmark_symbol, start, end) -> BenchmarkComparison:
        # Computes for benchmark: total_return, annualized_return, sharpe, max_drawdown
        # Relative metrics: alpha, beta, information_ratio, tracking_error
        # Beta = cov(strategy, benchmark) / var(benchmark)
        # Alpha = annualized excess return adjusted for beta
```

**Sanity checks**: `compute_metrics()` validates results before returning:
- Sharpe/Sortino/Calmar ratios clamped to `[-10, 10]` — values outside this range indicate
  data issues (e.g., near-zero volatility producing `inf`)
- Max drawdown ∈ `[0%, 100%]`
- Win rate ∈ `[0%, 100%]`
- If `len(snapshots) < 2`, returns zeroed metrics with a `warnings: ["Insufficient data"]` field
- Logs warning if annualized return exceeds `±500%` (likely data quality issue)

### 4.8 Event-Driven Engine (`engine.py`) and Context Factory (`context.py`)

#### `BacktestContext` (DI Factory)

The 15-step initialization sequence is extracted into a dedicated factory class. This keeps
`BacktestEngine.run()` focused on the simulation loop, and makes unit testing straightforward
(mock the context, not 15 individual components).

```python
class BacktestContext:
    """Encapsulates all DI wiring for a single backtest run."""

    # Public attributes (set during build):
    time_provider: BacktestTimeProvider
    store: HistoricalPriceStore
    data_service: DataPlatformService
    portfolio_service: PortfolioService
    trade_execution_service: TradeExecutionService
    equities_service: EquitiesBranchService
    event_log: InMemoryEventLogRepository
    instrument_ids: dict[str, str]
    trading_days: list[date]
    rebalance_days: set[date]
    cancelled: asyncio.Event              # Set by cancel endpoint to stop simulation

    @classmethod
    async def build(cls, config: BacktestConfig, live_config: EquitiesConfig) -> BacktestContext:
        """Construct all backtest components from config.

        Steps:
          1. Create BacktestTimeProvider
          2. Load universe, preload OHLCV + benchmark
          3. Pre-cache fundamentals (one-time API burst)
          4. Create HistoricalDataAdapter → DataPlatformService(NoOpCache, NoOpRateLimiter)
          5. Create BacktestBrokerAdapter
          6. Create 6 in-memory repositories
          7. Create PortfolioService + TradeExecutionService
          8. Create analysts (quantitative or fresh LLM instances)
          9. Create EquitiesBranchService (new instance, NOT the singleton)
         10. Generate deterministic instrument_ids
         11. Create initial portfolio
         12. Compute trading days + rebalance schedule
        """
```

#### `BacktestEngine`

The main orchestrator. Uses `BacktestContext` for initialization.

```
BacktestEngine.run(config) lifecycle:
│
├─ 1. INITIALIZATION
│   ├─ Set status = RUNNING in DB (if persisting)
│   └─ ctx = await BacktestContext.build(config, live_equities_config)
│       ├─ Creates BacktestTimeProvider(start_date)
│       ├─ Loads universe, preloads OHLCV (see store error handling in Section 4.3)
│       ├─ Pre-caches fundamentals from live YahooFinanceAdapter
│       ├─ Wires HistoricalDataAdapter → DataPlatformService(NoOpCache, NoOpRateLimiter)
│       ├─ Creates BacktestBrokerAdapter (wraps store + time provider)
│       ├─ Creates all in-memory repositories
│       ├─ Creates PortfolioService + TradeExecutionService (wired to in-memory repos)
│       ├─ Creates analysts:
│       │   ├─ If use_llm_agents=False: QuantitativeAnalysts(time_provider, data_service)
│       │   └─ If use_llm_agents=True: NEW analyst instances with backtest DataPlatformService
│       │       (NEVER reuse live singletons — they bind data_service at construction)
│       ├─ Creates NEW UniverseProvider with backtest DataPlatformService
│       │   (live singleton binds data_service at construction for get_company_facts();
│       │   reusing it would make network calls to YahooFinance during backtests.
│       │   The metadata is static so this isn't a look-ahead issue, but it's an
│       │   unnecessary network dependency and inconsistent with the isolation principle)
│       ├─ Creates EquitiesBranchService (new instance, NOT the singleton)
│       ├─ Generates deterministic instrument_ids: uuid5(NAMESPACE_DNS, symbol)
│       ├─ Creates portfolio with initial_capital
│       └─ Computes rebalance schedule from trading days + frequency
│
├─ 2. SIMULATION LOOP (for each trading day)
│   ├─ time_provider.advance_to(day)
│   ├─ Mark-to-market: iterate positions, update NAV with day's close prices
│   ├─ If rebalance day:
│   │   └─ equities_service.run_pipeline(branch_name, branch_id,
│   │        trade_execution_service, portfolio_service, event_log_repo,
│   │        session=None, instrument_ids=pre_generated_ids)
│   │      → LangGraph DAG: fetch_universe → screen → analyze → rebalance → execute
│   └─ Record DailySnapshot(date, nav, cash, positions, daily_return, cumulative_return)
│
├─ 3. POST-SIMULATION
│   ├─ PerformanceCalculator.compute_metrics(snapshots, trades)
│   ├─ PerformanceCalculator.compute_benchmark_comparison(snapshots, store, SPY, ...)
│   └─ Build BacktestResult with all metrics, snapshots, trade log
│
├─ 4. PERSISTENCE (optional)
│   ├─ PostgresBacktestRepository.save_result(result)
│   └─ Set status = COMPLETED (or FAILED with error_message on exception)
│
└─ ERROR HANDLING
    ├─ On exception in simulation loop: set status = FAILED, store error_message,
    │   save partial results (snapshots collected so far)
    └─ On BacktestDataError from preload: fail fast, set status = FAILED
```

**Analyst error isolation**: When `use_llm_agents=True`, the LangGraph DAG runs 3 analysts
concurrently via `asyncio.gather()`. The analyst `analyze_batch()` methods must use
`return_exceptions=True` (or equivalent per-stock try/except) so that one failed LLM call
doesn't kill the entire batch. Failed analyses return a neutral signal `(score=5, confidence=1)`
with a logged warning.

**Rebalance schedule computation**:
- First trading day always triggers a rebalance
- Then every N trading days: `weekly=5`, `biweekly=10`, `monthly=21`
- Based on actual trading days (weekends/holidays skipped automatically since they
  have no price data in the store)

### 4.9 Vectorized Screening Engine (`vectorized.py`)

```python
class VectorizedScreeningEngine:
    """Fast screening-only backtester. No trade execution or portfolio tracking."""

    async def run(branch_name, start_date, end_date, step_days=5,
                  compute_forward_returns=False) -> list[ScreeningSnapshot]:
        # For every Nth trading day:
        #   1. Advance BacktestTimeProvider to that date
        #   2. Run each screening filter individually against the full universe
        #   3. Track per-filter pass rates: filter_results = {filter_name: [passed_symbols]}
        #   4. Record ScreeningSnapshot(date, passed_symbols, filter_results)
        #   5. If compute_forward_returns: compute N-day forward returns for passed stocks
        #      forward_returns = {symbol: {5d: float, 10d: float, 21d: float}}
```

**Forward returns**: When `compute_forward_returns=True`, the engine computes 5/10/21-day
forward returns for stocks that pass screening on each date. This enables evaluating
"do stocks that pass my screen actually outperform?" without running full trade execution.
Only uses data already in the `HistoricalPriceStore` — no look-ahead bias since forward
returns are a post-hoc analysis metric, not used during screening.

**Use cases**:
- "How stable is my growth screener over time?"
- "Which filter is the most/least restrictive?"
- "If I relax the PEG filter from 3.0 to 4.0, how many more stocks pass?"
- "Do stocks passing my screen outperform the universe?" (forward returns)
- Parameter sensitivity analysis across hundreds of dates in seconds

### 4.10 API Endpoints (`api.py`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/backtest/run` | Trigger backtest. Body: `BacktestConfig` JSON. Returns `{backtest_id, status: "running"}` |
| `POST` | `/api/v1/backtest/estimate` | Dry-run cost/time estimation. Returns `{estimated_rebalances, estimated_llm_calls, estimated_cost_usd, estimated_duration_minutes}` based on config. No data downloaded |
| `GET` | `/api/v1/backtest/` | List backtest runs with filtering. Query params: `status`, `branch_name`, `limit` (default 20), `offset`. Returns summary fields (id, status, config snippet, total_return, created_at) — not full results |
| `GET` | `/api/v1/backtest/{backtest_id}` | Get results + progress. Returns full `BacktestResult` when completed, or `{status, progress: {current_day, total_days, pct_complete}, elapsed_seconds}` while running |
| `GET` | `/api/v1/backtest/{backtest_id}/trades` | Trade log with pagination (`limit`, `offset`) |
| `GET` | `/api/v1/backtest/{backtest_id}/snapshots` | Daily snapshots with pagination |
| `POST` | `/api/v1/backtest/{backtest_id}/cancel` | Cancel a running backtest. Sets status to `CANCELLED`, stops simulation loop at next day boundary |

**Progress tracking**: The engine updates a shared progress dict on each simulated day:
```python
progress = {
    "current_day": "2024-06-15",
    "total_days": 252,
    "days_completed": 120,
    "pct_complete": 47.6,
    "rebalances_completed": 5,
    "trades_executed": 87,
}
```

**Cost estimation** (`/estimate`): Computes without downloading data:
- `estimated_rebalances` = `trading_days_in_range / rebalance_interval`
- `estimated_llm_calls` = `rebalances × avg_passed_screening × 3 analysts` (only if `use_llm_agents=True`). Uses `avg_passed_screening ≈ 30` as a conservative estimate (universe of ~230 stocks typically screens down to 20-40)
- `estimated_cost_usd` = `llm_calls × avg_cost_per_call` (0 if quantitative-only). Note: with `cache_signals=True`, repeated runs over overlapping date ranges will cost less than estimated
- `estimated_duration_minutes` = heuristic based on date range and rebalance frequency

**Cancellation mechanism**: The `BacktestContext` holds an `asyncio.Event` named `cancelled`.
The cancel endpoint sets this event. The engine checks `if ctx.cancelled.is_set(): break` at
the top of each day iteration in the simulation loop. On cancellation, the engine saves partial
results (snapshots collected so far) and sets `status = CANCELLED`.

**Background execution**: Backtests run as FastAPI `BackgroundTasks`. In-memory dict stores
running/completed results for fast polling. Results also persisted to DB for long-term storage.

**Known constraints of `BackgroundTasks` (acceptable for Phase 3 MVP)**:
- **Single-worker only**: Progress dict is per-process. Multiple uvicorn workers won't share it.
  Production deployments should use `--workers 1` or switch to a task queue (ARQ, Celery) in a
  future phase.
- **No crash recovery**: If the process restarts, running backtests are lost and the in-memory
  dict is cleared. The app startup hook should query for `status = 'running'` rows with
  `started_at` older than a threshold (e.g., 2× estimated duration) and mark them as `failed`
  with `error_message = "Process terminated unexpectedly"`.
- **Memory management**: Completed results should be evicted from the in-memory dict after a
  configurable TTL (e.g., 1 hour) since they're also persisted to the DB. A simple
  `{backtest_id: (result, completed_at)}` dict with periodic cleanup is sufficient.

---

## 5. Database Schema

Four new tables for persisting backtest results.

### `backtest_runs`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID (PK) | |
| `status` | varchar(20) | `pending` / `running` / `completed` / `failed` / `cancelled` (see `BacktestStatus` enum) |
| `config` | JSONB | Full `BacktestConfig` snapshot |
| `equities_config` | JSONB (nullable) | `EquitiesConfig` override used for this run (null = used live defaults) |
| `started_at` | timestamptz | |
| `completed_at` | timestamptz | Null while running |
| `duration_seconds` | numeric(12,2) | Wall-clock time |
| `error_message` | text (nullable) | Stack trace / error details when status = `failed` |
| `metrics` | JSONB (nullable) | Full `PerformanceMetrics` — null if failed before completion |
| `benchmark` | JSONB (nullable) | `BenchmarkComparison` |
| `rebalance_count` | int | |
| `total_pipeline_runs` | int | |
| `progress` | JSONB (nullable) | Latest progress snapshot `{current_day, total_days, pct_complete}` |
| `created_at` | timestamptz | Index |

**Status transitions**: `pending` → `running` → `completed` / `failed` / `cancelled`.
On crash recovery: query for `status = 'running'` rows with `started_at` older than a threshold
and mark them as `failed` with `error_message = "Process terminated unexpectedly"`.

### `backtest_snapshots`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID (PK) | |
| `backtest_run_id` | UUID (FK → backtest_runs, ON DELETE CASCADE) | Index |
| `snapshot_date` | date | Index |
| `nav` | numeric(18,2) | |
| `cash` | numeric(18,2) | |
| `total_long_exposure` | numeric(18,2) | |
| `total_short_exposure` | numeric(18,2) | |
| `unrealized_pnl` | numeric(18,2) | |
| `realized_pnl` | numeric(18,2) | |
| `position_count` | int | |
| `positions` | JSONB | `{symbol: market_value}` |
| `daily_return` | numeric(12,8) | |
| `cumulative_return` | numeric(12,8) | |

**Persistence strategy**: Snapshots and trades are batch-inserted after the simulation loop
completes (not one-by-one during simulation). A 1-year daily backtest produces ~252 snapshots
and ~500 trades — a single `session.add_all()` + `flush()` is efficient. The entire
post-simulation persistence (update `backtest_runs` status + insert all snapshots + insert all
trades + insert all rebalance details) is wrapped in a single transaction. On failure, everything
rolls back and the run status is set to `failed` with the error details.

### `backtest_trades`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID (PK) | |
| `backtest_run_id` | UUID (FK → backtest_runs, ON DELETE CASCADE) | Index |
| `trade_date` | date | Index |
| `symbol` | varchar(50) | Index |
| `side` | varchar(20) | buy/sell/short/cover |
| `quantity` | numeric(18,8) | |
| `price` | numeric(18,6) | |
| `commission` | numeric(12,4) | |
| `slippage` | numeric(12,6) | |
| `reason` | varchar(100) | new_position/removed_position/weight_adjustment |

### `backtest_rebalance_details`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID (PK) | |
| `backtest_run_id` | UUID (FK → backtest_runs, ON DELETE CASCADE) | Index |
| `rebalance_date` | date | Index |
| `screened_symbols` | JSONB | `[symbol, ...]` — stocks that passed screening |
| `signals` | JSONB | `[{symbol, analyst_type, score, confidence}, ...]` |
| `composite_scores` | JSONB | `[{symbol, score, confidence, target_weight}, ...]` |
| `orders_generated` | JSONB | `[{symbol, side, quantity, reason}, ...]` |

**Purpose**: Full signal/score traceability per rebalance. Enables debugging "why did the
backtest buy AAPL on 2024-06-15?" without re-running. Essential for strategy iteration.

---

## 6. Data Flow Diagrams

### 6.1 Event-Driven Backtest Flow

```
                        ┌─────────────────────┐
                        │   BacktestConfig     │
                        │ start: 2024-01-01    │
                        │ end:   2025-01-01    │
                        │ freq:  weekly        │
                        │ branch: growth       │
                        └─────────┬───────────┘
                                  │
                          ┌───────▼──────────┐
                          │  BacktestEngine   │
                          │    .run()          │
                          └───────┬──────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │ 1. PRELOAD                 │
                    │  a. Universe: 230 symbols  │
                    │  b. OHLCV: yfinance batch  │
                    │  c. Fundamentals: cache    │
                    │  d. Benchmark: SPY         │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │ 2. WIRE DI                 │
                    │  HistoricalDataAdapter     │
                    │  → DataPlatformService     │
                    │  BacktestBrokerAdapter     │
                    │  → TradeExecutionService   │
                    │  InMemory repos            │
                    │  → PortfolioService        │
                    │  QuantitativeAnalysts      │
                    │  → EquitiesBranchService   │
                    └─────────────┬─────────────┘
                                  │
               ┌──────────────────▼──────────────────┐
               │ 3. SIMULATION LOOP                   │
               │                                      │
               │  for day in trading_days:            │
               │    ├─ advance clock                  │
               │    ├─ mark positions to market       │
               │    ├─ if rebalance_day:              │
               │    │    └─ run_pipeline()            │
               │    │        ├─ fetch_universe        │
               │    │        ├─ screen_stocks         │
               │    │        ├─ 3 analysts (parallel) │
               │    │        ├─ portfolio_decision    │
               │    │        └─ execute_trades        │
               │    └─ record DailySnapshot           │
               └──────────────────┬──────────────────┘
                                  │
               ┌──────────────────▼──────────────────┐
               │ 4. ANALYTICS                         │
               │  PerformanceMetrics + Benchmark      │
               │  → BacktestResult                    │
               │  → PostgresBacktestRepository        │
               └─────────────────────────────────────┘
```

### 6.2 Mark-to-Market (Daily)

```
For each position in portfolio:
  close_price = store.get_latest_close(symbol, today)
  market_value = close_price × long_quantity

total_long_exposure = sum(market_values)
nav = cash + total_long_exposure

daily_return = (today_nav - yesterday_nav) / yesterday_nav
cumulative_return = (today_nav - initial_capital) / initial_capital
```

### 6.3 Rebalance (Periodic)

```
run_pipeline(branch_name, branch_id, services, instrument_ids)
  │
  ├─ LangGraph: fetch_universe → screen_stocks → [3 analysts] → portfolio_decision → execute_trades
  │
  │  Inside portfolio_decision:
  │    1. compute_composite_scores(all_signals)
  │       composite = 0.40 × fundamental + 0.35 × news + 0.25 × technical
  │    2. select_stocks(scores) → top 20 by conviction (score × confidence)
  │    3. size_positions(selected) → conviction-weighted, 50% cap
  │    4. generate_orders(target, current, nav, prices)
  │       → BUY/SELL where |target_weight - current_weight| > 2%
  │
  │  Inside execute_trades:
  │    For each order:
  │      → TradeExecutionService.submit_order()
  │        → BacktestBrokerAdapter.submit_order()
  │          → fill at historical close + slippage
  │        → PortfolioService.handle_trade_executed()
  │          → update positions, cash, NAV, P&L (all in memory)
```

---

## 7. Existing Code Modifications

### 7.1 `app/modules/data_platform/service.py` + `noop.py` — Null Object Pattern

Instead of scattering `if self.cache:` / `if self.rate_limiter:` checks across every method
(7 methods × 2-3 checks each = ~20 guard clauses), we use the Null Object pattern:

**New file** `app/modules/data_platform/noop.py`:
```python
class NoOpCache:
    """Cache that always misses. Implements the same interface as DataCache."""
    def get(self, category: str, key: str) -> None:
        return None
    def set(self, category: str, key: str, value) -> None:
        pass

class NoOpRateLimiter:
    """Rate limiter that always permits. Implements the same interface as RateLimiter."""
    async def acquire(self, adapter_name: str) -> None:
        pass
```

**Change to** `DataPlatformService.__init__()` (line 21-24):
```python
# Before:
self.cache = cache or DataCache()
self.rate_limiter = rate_limiter or RateLimiter()

# After:
self.cache = cache or DataCache()              # Unchanged for production callers
self.rate_limiter = rate_limiter or RateLimiter()  # Unchanged for production callers
```

**No changes to DataPlatformService itself**. The constructor stays the same. The difference is
that backtest code passes `NoOpCache()` and `NoOpRateLimiter()` instead of `None`:

```python
# In BacktestContext.build():
data_service = DataPlatformService(
    adapter_registry=historical_registry,
    cache=NoOpCache(),           # No caching for in-memory data
    rate_limiter=NoOpRateLimiter(),  # No rate limiting for local data
)
```

**Impact**: Zero changes to `DataPlatformService` method bodies. Production callers unchanged.
Backtest callers pass Null Objects that implement the interface but do nothing. Cleaner than
20 `if self.cache:` guards, and follows the same adapter substitution pattern used everywhere.

### 7.2 `app/modules/equities/service.py` — Instrument IDs Parameter

**Current** `run_pipeline()` signature (line 105):
```python
async def run_pipeline(
    self, branch_name, branch_id,
    trade_execution_service=None, portfolio_service=None,
    event_log_repo=None, session=None,
) -> RunResult:
```

**Changed to**:
```python
async def run_pipeline(
    self, branch_name, branch_id,
    trade_execution_service=None, portfolio_service=None,
    event_log_repo=None, session=None,
    instrument_ids: dict[str, str] | None = None,  # NEW
) -> RunResult:
```

**Inside the method** (replacing lines 141-195):
```python
_instrument_ids: dict[str, str] = instrument_ids or {}
if not _instrument_ids and session and self.data_service:
    # ... existing upsert logic, populating _instrument_ids ...
```

Then use `_instrument_ids` instead of `instrument_ids` in the `_execute_trade` closure.

**Why this is critical**: Without this, when `session=None` (all backtest runs), the local
`instrument_ids` dict stays empty. The `_execute_trade` closure at line 203 checks
`instrument_ids.get(order.symbol)`, gets `None`, and skips every trade with a warning log.
The backtest would appear to run successfully but execute zero trades.

### 7.3 `app/modules/equities/agents/` — TimeProvider Injection

**Problem**: `TechnicalAnalyst` hardcodes `date.today()` to compute lookback windows. In a
backtest simulating 2024-03-15, `date.today()` returns the actual current date (2026-03-02),
producing a start date of 2025-09-02 for a 6-month lookback. Since the `HistoricalDataAdapter`
clamps to `≤ time_provider.today()` (2024-03-15), the query returns zero bars and all technical
signals are garbage.

**Fix for `technical_analyst.py`**:
```python
# Before:
class TechnicalAnalyst:
    def __init__(self, data_service, ...):
        self.data_service = data_service

    async def analyze(self, stock):
        end = date.today()                    # BUG: hardcoded
        start = end - timedelta(days=365)
        prices = await self.data_service.get_prices(stock.symbol, start, end)

# After:
class TechnicalAnalyst:
    def __init__(self, data_service, ..., time_provider: TimeProvider | None = None):
        self.data_service = data_service
        self.time_provider = time_provider or LiveTimeProvider()

    async def analyze(self, stock):
        end = self.time_provider.today()      # FIX: uses injected time
        start = end - timedelta(days=365)
        prices = await self.data_service.get_prices(stock.symbol, start, end)
```

**Fix for `fundamentals_analyst.py`** — the `date.today()` is **not** in the analyst itself,
but in `DataPlatformService.get_metrics()` (line 88: `end = end_date or date.today()`). The
analyst calls `get_metrics(stock.symbol)` without passing `end_date`, so it silently falls back
to the current real date. The fix is to pass the explicit date:
```python
# Before:
class FundamentalsAnalyst:
    def __init__(self, config, data_service=None, sec_edgar=None, llm_client=None):
        ...

    async def analyze(self, stock):
        metrics_data = await self.data_service.get_metrics(stock.symbol)  # end_date defaults to date.today()

# After:
class FundamentalsAnalyst:
    def __init__(self, config, data_service=None, sec_edgar=None, llm_client=None,
                 time_provider: TimeProvider | None = None):
        self.time_provider = time_provider or LiveTimeProvider()
        ...

    async def analyze(self, stock):
        metrics_data = await self.data_service.get_metrics(
            stock.symbol, end_date=self.time_provider.today()
        )
```

**Same `date.today()` issue in screening filters**: The screener's `_get_metric()` helper calls
`data_service.get_metrics(symbol)` without `end_date` for every filter. In backtests, the
`HistoricalDataAdapter` pre-caches fundamentals and ignores the date parameter (known limitation),
so this is functionally harmless. However, for future-proofing (e.g., when SEC EDGAR historical
fundamentals are added), the screener should accept an optional `end_date` parameter and pass it
through. This is a low-priority enhancement — document as a follow-up, not a Phase A blocker.

**Backward compatibility**: The `time_provider` parameter defaults to `LiveTimeProvider()`,
so existing production code in `dependencies.py` doesn't need changes — analysts work
identically to before. Only backtest code passes `BacktestTimeProvider`.

### 7.5 `app/modules/equities/agents/` — `analyze_batch()` Error Isolation

**Problem**: All three analysts (`news_analyst.py`, `fundamentals_analyst.py`,
`technical_analyst.py`) use bare `asyncio.gather()` in `analyze_batch()` without
`return_exceptions=True`. If one stock's `analyze()` call fails (LLM timeout, data fetch
error, JSON parse failure), the entire batch is killed and zero signals are returned for
that analyst type.

**Current code** (identical pattern in all three analysts):
```python
async def analyze_batch(self, stocks, max_concurrent=10):
    sem = asyncio.Semaphore(max_concurrent)
    async def _limited(s):
        async with sem:
            return await self.analyze(s)
    return list(await asyncio.gather(*(_limited(s) for s in stocks)))
```

**Fix** — wrap per-stock calls in try/except with neutral fallback:
```python
async def analyze_batch(self, stocks, max_concurrent=10):
    sem = asyncio.Semaphore(max_concurrent)
    async def _limited(s):
        async with sem:
            try:
                return await self.analyze(s)
            except Exception:
                logger.warning(f"{self.__class__.__name__}: analyze failed for {s.symbol}", exc_info=True)
                return StockSignal(
                    symbol=s.symbol,
                    analyst_type=self.ANALYST_TYPE,
                    bullish_score=5,
                    confidence=1,
                    summary="Analysis failed — neutral fallback signal.",
                )
    return list(await asyncio.gather(*(_limited(s) for s in stocks)))
```

**Why confidence=1**: A failed analysis should have minimal weight in the composite score.
The neutral `bullish_score=5` avoids biasing the signal, and `confidence=1` ensures the
portfolio manager largely ignores this data point when computing conviction scores.

### 7.4 `app/common/interfaces/repositories.py` + `app/modules/portfolio/service.py` — ABC Alignment

**Problem**: `PortfolioService.handle_trade_executed()` calls
`self.portfolio_repo.update_portfolio_fields()`, but this method is NOT defined on the
`PortfolioRepository` ABC. The in-memory repo would crash at runtime with `AttributeError`.

Additionally, `PortfolioService.__init__` takes concrete types (`PostgresPortfolioRepository`,
`PostgresPositionRepository`, `PostgresSnapshotRepository`) instead of ABCs, which prevents
clean injection of in-memory repos without type-checker warnings.

**Fix 1** — Add to `app/common/interfaces/repositories.py`:
```python
class PortfolioRepository(ABC):
    # ... existing methods ...

    @abstractmethod
    async def update_portfolio_fields(
        self, branch_id: str, **fields
    ) -> None: ...
```

**Fix 2** — Change `app/modules/portfolio/service.py` constructor:
```python
# Before:
from app.modules.portfolio.repository import PostgresPortfolioRepository, ...

class PortfolioService:
    def __init__(self,
        portfolio_repo: PostgresPortfolioRepository,
        position_repo: PostgresPositionRepository,
        snapshot_repo: PostgresSnapshotRepository,
        event_log: EventLogRepository):

# After:
from app.common.interfaces.repositories import (
    PortfolioRepository, PositionRepository, SnapshotRepository, EventLogRepository
)

class PortfolioService:
    def __init__(self,
        portfolio_repo: PortfolioRepository,
        position_repo: PositionRepository,
        snapshot_repo: SnapshotRepository,
        event_log: EventLogRepository):
```

**Fix 3** — Change `app/modules/trade_execution/service.py` constructor:
```python
# Before:
from app.modules.trade_execution.repository import PostgresOrderRepository, PostgresTradeRepository

class TradeExecutionService:
    def __init__(self,
        order_repo: PostgresOrderRepository,
        trade_repo: PostgresTradeRepository,
        broker: BrokerAdapter,
        event_log: EventLogRepository,
        portfolio_service: PortfolioService):

# After:
from app.common.interfaces.repositories import OrderRepository, TradeRepository, EventLogRepository

class TradeExecutionService:
    def __init__(self,
        order_repo: OrderRepository,
        trade_repo: TradeRepository,
        broker: BrokerAdapter,
        event_log: EventLogRepository,
        portfolio_service: PortfolioService):
```

**Impact**: Production code passes `PostgresXxxRepository` instances which implement the ABCs,
so no runtime behavior change. The in-memory repos now satisfy both the type checker and
runtime method resolution. `broker` and `event_log` already use ABCs and require no changes.

---

## 8. Known Limitations and Future Improvements

| Limitation | Impact | Severity | Future Fix |
|------------|--------|----------|------------|
| No historical fundamentals | Look-ahead bias for fundamental-based screening filters (PE, PB, ROE, etc.). Pre-cached current fundamentals used for all dates | **High** — affects screening accuracy for value branch | Integrate SEC EDGAR XBRL parser for quarterly fundamental snapshots (already have SEC Edgar adapter skeleton in data platform) |
| No historical news | Quantitative news analyst uses price momentum as proxy; LLM news analyst can only analyze articles available at API call time, not historical headlines | **Medium** — news weight (0.35) uses a crude proxy in quant mode | Build a news archive adapter or integrate a financial news API with historical access |
| Universe is static | Same stocks screened on all dates; ignores IPOs, delistings, index reconstitutions | **Medium** — survivorship bias (stocks in current ETF may not have been there historically) | Build historical constituent lists from ETF holdings snapshots |
| LLM signals are non-deterministic | Even with caching, first-run signals vary across runs due to LLM temperature. Different seeds produce different backtest results | **Low** — mitigated by signal caching (`LLMBacktestConfig.cache_signals=True`) | Add optional `seed` parameter to LLM calls (when supported by API) |
| No short selling in backtests | Only long positions supported. Short signals from analysts are ignored | **Low** — matches current production pipeline which is long-only | Add short-selling support to `BacktestBrokerAdapter` + `InMemoryPositionRepository` |
| No transaction costs model | Commission is flat per trade; no market impact model. Large orders filled at same price as small ones | **Low** — acceptable for initial version | Add volume-dependent slippage, spread-based cost estimation |
| Corporate actions handled via adjusted close | Using `auto_adjust=True` in yfinance corrects for splits/dividends in price data, but doesn't model dividend income explicitly | **Low** — total return is approximately correct | Add explicit dividend tracking in `HistoricalPriceStore` |
| Per-filter screening visibility | Event-driven engine uses the full screener (all filters applied together), not individual filter tracking like the vectorized engine. Can't see which filter rejected which stock during rebalances | **Low** — `backtest_rebalance_details` table stores passed symbols but not per-filter breakdown | Add optional per-filter tracking to the event-driven screener (performance trade-off) |

---

## 9. Performance Metrics Glossary

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| **Sharpe Ratio** | `(mean_daily_excess / daily_vol) × √252` | Risk-adjusted return; > 1.0 is good, > 2.0 is excellent |
| **Sortino Ratio** | `(mean_daily_excess / downside_dev) × √252` | Like Sharpe but only penalizes downside volatility |
| **Max Drawdown** | `max((peak - trough) / peak)` across all peaks | Worst peak-to-trough decline; lower is better |
| **Calmar Ratio** | `annualized_return / max_drawdown` | Return per unit of max drawdown risk |
| **Alpha** | `annualized(strategy_excess - beta × benchmark_excess)` | Excess return unexplained by market exposure |
| **Beta** | `cov(strategy, benchmark) / var(benchmark)` | Sensitivity to market movements; 1.0 = market-neutral |
| **Information Ratio** | `mean(tracking_diff) / std(tracking_diff) × √252` | Active return per unit of tracking error |
| **Win Rate** | `winning_trades / total_trades` | Fraction of round-trips that were profitable |
| **Profit Factor** | `sum(wins) / abs(sum(losses))` | Gross profit / gross loss; > 1.0 means net profitable |

---

## 10. Testing Strategy

**Approach**: Strict TDD — all 216 unit tests and 4 integration tests were written before any
implementation code. Tests import from intended module paths and currently fail with
`ModuleNotFoundError`. As each implementation phase lands, the corresponding test group
transitions from `ImportError` → logic failures → green.

**Conventions** (matching existing codebase exactly):
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorators needed
- `_make_<object>(**overrides)` factory functions (not `@pytest.fixture`) for test data
- `Test<Concept>` classes, `test_<verb>_<condition>_<expected>` methods
- `AsyncMock()` for async deps, `MagicMock()` for sync, zero mocks for pure math
- `pytest.approx` for floats, `pytest.raises(match=...)` for errors
- Inline math comments documenting formulas next to assertions
- No DB in unit tests — all repo access via `AsyncMock`

### Test File Structure

```
tests/unit/backtest/
├── conftest.py                    # Shared builders: _make_price_bar, _make_price_series,
│                                  #   _make_backtest_config, _make_daily_snapshot, _make_backtest_trade
├── test_time_provider.py          # 11 tests — Phase A
├── test_config.py                 # 14 tests — Phase A
├── test_models.py                 # 12 tests — Phase A
├── test_noop.py                   #  7 tests — Phase A
├── test_equities_service_fix.py   #  4 tests — Phase H (independent)
├── test_historical_data.py        # 19 tests — Phase B
├── test_backtest_broker.py        # 14 tests — Phase B
├── test_in_memory_state.py        # 32 tests — Phase C
├── test_quantitative_analysts.py  # 27 tests — Phase D
├── test_analytics.py              # 25 tests — Phase E
├── test_engine.py                 # 16 tests — Phase F
├── test_vectorized.py             #  8 tests — Phase F
├── test_context.py                #  8 tests — Phase F
├── test_repository.py             #  5 tests — Phase G
└── test_api.py                    # 14 tests — Phase G

tests/integration/backtest/
└── test_backtest_integration.py   #  4 tests — Phase I (@pytest.mark.integration)
```

### Unit Tests — Detailed Coverage

| Test File | Tests | Mocking | What It Validates |
|-----------|-------|---------|------------------|
| `test_config.py` | 14 | None | `RebalanceFrequency` enum values + StrEnum, `BacktestStatus` enum, `LLMBacktestConfig` defaults, `BacktestConfig` defaults/validation (end > start, capital > 0, slippage >= 0, config override) |
| `test_time_provider.py` | 11 | None | `TimeProvider` ABC instantiation, `LiveTimeProvider` returns current date/UTC, `BacktestTimeProvider` initial date, advance_to updates date/now, backwards raises, now() returns 16:00 UTC |
| `test_models.py` | 12 | None | `DailySnapshot`, `BacktestTrade`, `PerformanceMetrics` (warnings default), `BenchmarkComparison`, `BacktestResult` (completed + failed), `ScreeningSnapshot` (forward_returns optional) |
| `test_noop.py` | 7 | None | `NoOpCache` get→None, set no-op, set-then-get→None, delete no-op. `NoOpRateLimiter` acquire no-block, 100 acquires < 100ms |
| `test_historical_data.py` | 19 | None (real store) | `HistoricalPriceStore`: get_bar/bars/close correct + missing, get_latest_close weekend handling, get_trading_days sorted + no weekends. `HistoricalDataAdapter`: **key test** — get_prices clamps end to simulated today (no future data), get_current_price, get_metrics/facts from cache, implements PriceDataAdapter + FundamentalsAdapter ABCs |
| `test_backtest_broker.py` | 14 | None (real store) | BUY slippage increases price (`100*(1+10/10000)=100.10`), SELL decreases, slippage/commission recorded, timestamp from TimeProvider. Limit orders: rejected above limit, accepted at/below. No price → rejection. Zero slippage/commission config |
| `test_in_memory_state.py` | 32 | None | All 6 repos implement their ABC (`isinstance` check). `PortfolioRepository`: create, get_by_branch, update_cash, **update_portfolio_fields** (Section 7.4). `PositionRepository`: upsert create + update, get_by_portfolio/symbol, delete_if_flat. `SnapshotRepository`: create, list pagination. `OrderRepository`: create, get_by_id, update_status, list filter. `TradeRepository`: create, get_by_id, list pagination, **all_trades property**. `EventLogRepository`: append, query by type/branch, pagination |
| `test_quantitative_analysts.py` | 27 | AsyncMock data_service | `NewsAnalyst`: momentum > 3% → 6, < -3% → 4, flat → 5, confidence=3, missing → neutral. `FundamentalsAnalyst`: strong metrics → clamped 10, weak → clamped 1, average → ~5, confidence=6, **get_metrics called with end_date=time_provider.today()**, FCF tiered scoring, score clamped [1,10]. `TechnicalAnalyst`: uptrend → high, downtrend → low, confidence=5, **uses time_provider not date.today()**, high volatility penalty, score clamped |
| `test_analytics.py` | 25 | None (pure math) | `compute_metrics`: total_return, annualized_return, volatility, Sharpe/Sortino/Calmar with inline formulas, max_drawdown (100→120→90→110 = 25%), ratios clamped [-10,10], drawdown clamped [0,1], win_rate, profit_factor, exposure metrics. Edge cases: insufficient data → zeroed + warning, flat → zero vol, all losses → zero profit_factor, no trades → zero, excessive return. `compute_benchmark_comparison`: beta = cov/var, alpha, info ratio, tracking error, perfect correlation → beta≈1 alpha≈0 |
| `test_engine.py` | 16 | Mocked context | `RebalanceSchedule`: first day always, daily=every day, weekly=every 5, biweekly=every 10, monthly=every 21. `SimulationLoop`: advances time each day, pipeline on rebalance days only, snapshot each day, mark-to-market updates NAV, produces BacktestResult. `Cancellation`: stops early, partial result with status=cancelled. `ErrorHandling`: pipeline error → FAILED status, error message captured, partial snapshots preserved |
| `test_vectorized.py` | 8 | Mocked screener | Runs on step_days intervals, records per-filter results, advances time_provider, forward returns computed when flag set / not by default, passed symbols tracked, snapshot dates match intervals, empty range → empty |
| `test_context.py` | 8 | Patched externals | BacktestTimeProvider created with start_date, in-memory repos (not Postgres), new equities_service instance, trading_days from store, rebalance schedule includes first day, cancelled Event initialized + not set, instrument_ids deterministic (uuid5), store.preload() called |
| `test_repository.py` | 5 | AsyncMock session | save_result calls session.add + add_all, get_result → None for missing / BacktestResult for found, list_runs paginates + filters by status |
| `test_api.py` | 14 | Patched endpoints | POST /run → 202 + backtest_id, invalid → 422. POST /estimate → cost breakdown, 0 LLM calls quantitative. GET /{id} → full result completed / progress running / 404 missing. GET / → paginated list + status filter. POST /{id}/cancel → 200 / 404 / 409. GET /{id}/trades + /snapshots → paginated |
| `test_equities_service_fix.py` | 4 | AsyncMock pipeline | run_pipeline accepts instrument_ids param, trades execute with provided dict, trades skipped without dict+session, default is None |

### Integration Tests

| Test File | Tests | What It Validates |
|-----------|-------|------------------|
| `test_backtest_integration.py` | 4 | 1-month real yfinance data, weekly rebalance, quantitative mode. Verifies: `BacktestResult` with status=completed, non-empty snapshots, non-None metrics, benchmark comparison with alpha/beta/tracking_error. Tolerates 0 trades (Yahoo rate limiting — consistent with existing E2E pattern). Marked `@pytest.mark.integration` |

### Test Summary

| Group | Implementation Phase | Tests | Can Parallelize With |
|-------|---------------------|-------|---------------------|
| 1: Config/models/noop | A | 48 | Nothing (start here) |
| 2: Adapters | B | 33 | Group 3, 5 |
| 3: In-memory state | C | 32 | Group 2, 5 |
| 4: Quant analysts | D | 27 | Group 5 |
| 5: Analytics | E | 25 | Groups 2, 3, 4 |
| 6: Engines + context | F | 32 | — |
| 7: Repo + API | G | 19 | — |
| H: Equities fix | H | 4 | Any group |
| I: Integration | I | 4 | Post all unit tests |
| **Total** | | **220** | |

### Verification Commands

```bash
# Collect all tests (currently all fail with ModuleNotFoundError — expected)
pytest tests/unit/backtest/ --collect-only -q

# Run backtest unit tests (run after each implementation phase)
pytest tests/unit/backtest/ -q

# Verify no regressions in existing tests
pytest tests/unit/ --ignore=tests/unit/backtest -q

# Run specific group (e.g., Phase A tests only)
pytest tests/unit/backtest/test_config.py tests/unit/backtest/test_time_provider.py \
       tests/unit/backtest/test_models.py tests/unit/backtest/test_noop.py -q

# Integration test (requires yfinance network access)
pytest tests/integration/backtest/ -m integration -v

# Lint
ruff check tests/unit/backtest/ tests/integration/backtest/
```

---

## 11. Implementation Sequence

| Phase | Files | Dependencies | Notes |
|-------|-------|-------------|-------|
| **A: Foundation + Existing Fixes** | `common/interfaces/time.py`, `backtest/config.py`, `backtest/time_provider.py`, `backtest/models.py`, `data_platform/noop.py` | None | Also apply ABC alignment (Section 7.4), analyst TimeProvider injection (Section 7.3), and `analyze_batch()` error isolation (Section 7.5) since these are prerequisites |
| **B: Adapters** | `adapters/historical_data.py`, `adapters/backtest_broker.py` | Phase A | Includes `HistoricalPriceStore` error handling. `HistoricalDataAdapter` adds `get_current_price()` as concrete method (not on ABC — see Section 4.3 note) |
| **C: State** | `state.py` (6 in-memory repos) | Phase A (ABC alignment) | Must implement the updated ABCs including `update_portfolio_fields()` |
| **D: Analysts** | `quantitative_analysts.py` | Phase A (TimeProvider) | Continuous scoring models. Quantitative analysts should handle missing data gracefully (try/except with neutral fallback) |
| **E: Analytics** | `analytics.py` | Phase A (models) | Include sanity checks |
| **F: Context + Engines** | `context.py`, `engine.py`, `vectorized.py` | Phases A-E | `BacktestContext` factory (including `cancelled` event) + both engines. Creates new `UniverseProvider` instance with backtest `DataPlatformService` |
| **G: Persistence + API** | DB models, migration, `repository.py`, `api.py`, router registration | Phase F | 4 new tables, batch inserts in single transaction, progress tracking, cost estimation endpoint, list endpoint, startup hook for stale run cleanup |
| **H: Equities Service Fix** | `equities/service.py` (instrument_ids param) | Can be done anytime | Standalone change, doesn't depend on other phases |
| **I: Integration Tests** | `tests/integration/backtest/test_backtest_integration.py` | All unit phases green | Real yfinance data, end-to-end quantitative backtest |

**TDD workflow**: All 216 unit tests and 4 integration tests are **already written** in
`tests/unit/backtest/` and `tests/integration/backtest/` (see Section 10). They currently fail
with `ModuleNotFoundError` since no implementation exists yet. Each implementation phase should:
1. Run the corresponding test group — confirm tests fail with `ImportError` (not yet implemented)
2. Write implementation code
3. Run the test group again — tests transition to logic failures, then green
4. Run `pytest tests/unit/ --ignore=tests/unit/backtest -q` — verify no regressions in existing code

**Phase A is the critical path** — it creates the shared interfaces (`TimeProvider`, updated ABCs)
and Null Object implementations that all subsequent phases depend on. The existing code modifications
(Sections 7.3, 7.4, and 7.5) are bundled into Phase A because downstream phases (C, D, F) depend on them.

Phase H (equities service `instrument_ids` fix) is independent and can be done at any time, even
before Phase A. It's a single parameter addition with no dependencies on backtest code.
