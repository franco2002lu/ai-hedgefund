# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install and setup
pip install -e ".[dev]"
cp .env.example .env                              # defaults work with Docker Compose

# Database (PostgreSQL 16 on port 5433)
docker compose -f infrastructure/docker-compose.yml up -d
alembic upgrade head

# Run server
uvicorn app.main:app --port 8000

# Tests
pytest tests/unit/ -q                           # all unit tests (fast, no DB needed)
pytest tests/unit/equities/test_screener.py -q  # single file
pytest tests/unit/ -q -k "test_buy_order"       # single test by name
pytest tests/ --ignore=tests/integration -q     # unit + non-e2e tests

# Integration/E2E (require running server + DB — see "Running Integration Tests" below)
python tests/test_checkpoint2.py                # portfolio + event log
python tests/test_e2e_smoke.py                  # full 17-step trade lifecycle
pytest tests/integration/ -m integration -v     # analyst agent tests (real LLM calls)
pytest tests/integration/ -m e2e -v             # equities e2e pipeline

# Lint and format (ruff)
ruff check app/ tests/                          # lint
ruff check app/ tests/ --fix                    # lint with auto-fix
ruff format app/ tests/                         # format

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Environment

Required in `.env` (all prefixed `HEDGE_`, see `.env.example`):
- `HEDGE_DATABASE_URL` — defaults to local Docker Compose PostgreSQL on port 5433
- `HEDGE_EXECUTION_MODE` — `paper` (default) or `live`
- `ANTHROPIC_API_KEY` — required for equities LLM agents (news, fundamentals, technical analysts)

## Architecture

**Modular monolith** — single FastAPI process with clean module boundaries, designed for future microservice extraction.

### Dependency Injection

`app/dependencies.py` is the **single composition root**. Two lifecycle patterns:
- **Singletons** (stateless): `DataPlatformService`, `PaperTradingAdapter`, `EquitiesBranchService` — created once in `init_services()` during app lifespan startup
- **Per-request** (session-scoped): `PortfolioService`, `TradeExecutionService`, `EventLogService` — created per request via `Depends()`, sharing one `AsyncSession` for transactional consistency

Modules never instantiate their own dependencies. All wiring goes through `dependencies.py`.

### Module Layout

Each module in `app/modules/` follows: `api.py` (FastAPI router) → `service.py` (business logic) → `repository.py` (data access via SQLAlchemy). Interfaces live in `app/common/interfaces/`.

**Shared modules** (Phase 1):
- `portfolio/` — positions, cash, NAV, snapshots, P&L tracking
- `trade_execution/` — order lifecycle, paper trading adapter, fill simulation
- `data_platform/` — market data aggregation (Yahoo Finance, SEC Edgar) with TTL cache and rate limiting
- `event_log/` — append-only audit trail (all events are Pydantic models inheriting `BaseEvent`)

**Branch module** (Phase 2):
- `equities/` — LLM-powered screening + analysis pipeline (see below)

### Equities Branch Pipeline

`equities/service.py` orchestrates: **Universe** → **Screening** → **Multi-Agent Analysis** → **Portfolio Manager** → **Trade Execution**

- `universe/provider.py` fetches stock universe, `universe/screener.py` applies filter pipeline
- Screener builds branch-specific filters at runtime: 5 shared + 6 growth-specific or 6 value-specific filters via `_build_screener(branch_name)`
- 3 LLM analyst agents (`news_analyst.py`, `fundamentals_analyst.py`, `technical_analyst.py`) run as a LangGraph fan-out/fan-in in `agents/graph.py`
- `agents/portfolio_manager.py` aggregates signals into composite scores and generates rebalance orders
- Per-request services (trade execution, portfolio, event log, DB session) are injected at `run_pipeline()` call time from the API endpoint, not at singleton init

### Database

PostgreSQL 16 (async via `asyncpg`), SQLAlchemy 2.0 ORM. All models in `app/db/models.py`. Sessions auto-commit on success, rollback on exception (see `app/db/connection.py`).

### Configuration

`app/config.py` uses Pydantic Settings. All env vars prefixed with `HEDGE_` (e.g., `HEDGE_DATABASE_URL`). Defaults work with Docker Compose setup. Equities-specific config in `app/modules/equities/config.py`. Non-prefixed env vars (e.g., `ANTHROPIC_API_KEY`) are loaded into `os.environ` via `load_dotenv()` in `app/main.py` at startup.

### Architecture Docs

Design decisions and specs live in `plans/architecture/`:
- `HIGH-LEVEL-ARCH.md` — system overview, module responsibilities
- `PHASE1-SHARED-INFRASTRUCTURE.md` — DB schema, repo interfaces, service contracts
- `PHASE2-EQUITIES-BRANCH.md` — equities pipeline deep-dive, filter specs, agent orchestration

## Key Files

- `app/dependencies.py` — single composition root, all DI wiring
- `app/main.py` — FastAPI app, lifespan startup, router registration
- `app/db/models.py` — all SQLAlchemy ORM models (14 tables)
- `app/db/connection.py` — async engine, session factory, `get_session()`
- `app/config.py` — Pydantic Settings (`HEDGE_` prefix)
- `app/common/enums.py` — all shared enums
- `app/common/interfaces/repositories.py` — abstract repository contracts
- `app/modules/equities/service.py` — equities pipeline orchestrator
- `app/modules/equities/agents/graph.py` — LangGraph workflow definition
- `app/modules/equities/agents/llm_client.py` — Anthropic SDK wrapper for analyst agents
- `app/modules/equities/config.py` — screening, agent, and portfolio config
- `tests/conftest.py` — shared test fixtures (`_make_universe_stock`, `_make_stock_signal`, `_make_composite_score`)

## Conventions

- **ORM models**: `FooModel` in `app/db/models.py`. **Domain models**: `Foo` as Pydantic in `app/common/models/` or module-level `models.py`
- **Repositories**: `PostgresFooRepository` implementing abstract `FooRepository` from `app/common/interfaces/repositories.py`
- **Events**: every meaningful action (trade, portfolio update, signal) creates a `BaseEvent` subclass appended to event log
- **Async everywhere**: all DB ops, service methods, and route handlers are `async def`
- **Enums** use `StrEnum` (Python 3.12+) in `app/common/enums.py`: `OrderSide`, `OrderStatus`, `OrderType`, `SignalDirection`, `AssetClass`, `BranchType`
- **Linting**: ruff enforces `E/W/F/I/UP/B/SIM` rules. `B008` (Depends in defaults) is ignored for FastAPI. Alembic migrations are excluded from line-length checks
- **API routes**: static paths (e.g., `/config`) must be declared before dynamic `/{param}` paths in the same router to avoid FastAPI path conflicts
- Test markers: `@pytest.mark.integration` and `@pytest.mark.e2e` for tests requiring live services; `asyncio_mode = "auto"` in pytest config

## Running Integration Tests

Integration and E2E tests make real HTTP calls to `localhost:8000` and require a running server with a seeded database. Follow these steps exactly.

### Prerequisites

1. **Docker DB running**: `docker compose -f infrastructure/docker-compose.yml up -d`
2. **`.env` file exists** with `ANTHROPIC_API_KEY` set — `pytest-dotenv` auto-loads it via `env_files = [".env"]` in `pyproject.toml`, so no manual `export` needed
3. **`pip install -e ".[dev]"`** — ensures `pytest-dotenv` and other test deps are installed

### DB Reset Procedure (required before E2E tests)

E2E tests (`test_e2e_smoke.py`, `test_checkpoint2.py`, `test_e2e_equities.py`) create portfolios with fixed branch IDs. Re-running against a dirty DB causes `UniqueViolationError`. Reset with:

```bash
# 1. Connect and drop schema
psql -h localhost -p 5433 -U hedge_user -d hedge_fund -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# 2. Re-apply migrations
alembic upgrade head

# 3. Re-seed fund + branch rows (required for FK constraints)
psql -h localhost -p 5433 -U hedge_user -d hedge_fund <<'SQL'
INSERT INTO funds (id, name) VALUES ('11111111-1111-1111-1111-111111111111', 'Test Fund');
INSERT INTO branches (id, fund_id, name, branch_type)
VALUES
  ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111', 'US Equities', 'equities'),
  ('33333333-3333-3333-3333-333333333333', '11111111-1111-1111-1111-111111111111', 'Equities Growth', 'equities'),
  ('44444444-4444-4444-4444-444444444444', '11111111-1111-1111-1111-111111111111', 'Equities Value', 'equities');
SQL

# 4. Restart the server (picks up fresh DB state)
# Kill existing uvicorn, then:
uvicorn app.main:app --port 8000
```

### Test Suites and Run Order

These suites **cannot all share the same DB state** — `test_e2e_smoke.py` and `test_checkpoint2.py` both use branch `22222222...` and create a portfolio for it. Run them in separate DB cycles or pick one per cycle.

| Suite | Command | What it tests | DB state needed |
|-------|---------|---------------|-----------------|
| Unit tests | `pytest tests/unit/ -q` | All business logic (no DB/server) | None |
| Analyst integration | `pytest tests/integration/ -m integration -v` | Real LLM calls via Anthropic API | Server + seeded DB + `ANTHROPIC_API_KEY` |
| Equities E2E | `pytest tests/integration/ -m e2e -v` | Full pipeline: universe → screening → signals → trades | Server + clean seeded DB + `ANTHROPIC_API_KEY` |
| Checkpoint 2 | `python tests/test_checkpoint2.py` | Portfolio + event log CRUD | Server + clean seeded DB |
| E2E Smoke | `python tests/test_e2e_smoke.py` | Full 17-step trade lifecycle | Server + clean seeded DB |

**Recommended run order for a full verification cycle:**

```bash
# 1. Unit tests (always safe, no deps)
pytest tests/unit/ -q

# 2. Reset DB, start server, then run integration + equities E2E
#    (these use branch 33333333... so they don't conflict with each other)
pytest tests/integration/ -m integration -v
pytest tests/integration/ -m e2e -v

# 3. Then pick ONE of checkpoint2 or smoke test (both use branch 22222222...)
python tests/test_checkpoint2.py
# OR (requires DB reset between them)
python tests/test_e2e_smoke.py
```

### Known Integration Test Behaviors

- **`real_llm_client` fixture is function-scoped** (`tests/integration/conftest.py`) — `AsyncAnthropic` binds httpx connections to the active event loop. With `asyncio_mode = "auto"`, each test gets a new loop, so a session-scoped client crashes with `RuntimeError: Event loop is closed`. The function-scoped fixture creates a fresh client per test.
- **E2E equities test tolerates 0 orders** — Yahoo Finance rate-limiting can cause `get_current_price()` to return `None`, preventing the portfolio manager from computing share quantities. Steps 10-12 (trade execution, positions, cash) are conditional on `len(orders) > 0`. The pipeline itself still succeeds.
- **Analyst integration tests need `ANTHROPIC_API_KEY`** — without it, the `AnthropicAnalystClient.__init__` calls `pytest.skip()`, causing all 19 analyst tests to be skipped silently. Check `pytest -v` output for `SKIPPED` markers.

## Gotchas

- **E2E/integration tests need a live server + clean DB** — see "Running Integration Tests" section above for the full DB reset procedure, run order, and known behaviors.
- **Screening filters handle missing data with None** — `LeverageFilter` and `PEGFilter` default to `None` and reject stocks with missing data (not 0.0 default). New filters should follow this pattern.
- **EquitiesBranchService is a singleton but uses per-request DB deps** — the service is created once, but `run_pipeline()` accepts optional `trade_execution_service`, `portfolio_service`, `event_log_repo`, and `session` injected per-request from the API layer.
- **`alembic.ini` has a hardcoded DB URL** — it doesn't read from `.env`; update it manually if your connection string differs.
