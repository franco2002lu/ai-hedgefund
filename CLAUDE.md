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

# Integration/E2E (require running server + DB)
python tests/test_checkpoint2.py                # portfolio + event log
python tests/test_e2e_smoke.py                  # full 17-step trade lifecycle
pytest tests/integration/ -m e2e                # equities e2e

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

`app/config.py` uses Pydantic Settings. All env vars prefixed with `HEDGE_` (e.g., `HEDGE_DATABASE_URL`). Defaults work with Docker Compose setup. Equities-specific config in `app/modules/equities/config.py`.

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

## Gotchas

- **E2E/integration tests need a live server** — `tests/test_e2e_smoke.py`, `tests/test_checkpoint2.py`, and `tests/integration/` make real HTTP calls to `localhost:8000`. Unit tests (`tests/unit/`) run standalone.
- **E2E smoke test requires a clean DB** — must `DROP SCHEMA public CASCADE`, re-migrate, and re-seed before running `test_e2e_smoke.py` (see README for seed script)
- **DB FKs require seeded data** — `ScreeningRunModel.branch_id` and `PortfolioDecisionModel.branch_id` FK to `branches` table. Integration tests need fund + branch rows seeded first.
- **Screening filters handle missing data with None** — `LeverageFilter` and `PEGFilter` default to `None` and reject stocks with missing data (not 0.0 default). New filters should follow this pattern.
- **EquitiesBranchService is a singleton but uses per-request DB deps** — the service is created once, but `run_pipeline()` accepts optional `trade_execution_service`, `portfolio_service`, `event_log_repo`, and `session` injected per-request from the API layer.
- **`alembic.ini` has a hardcoded DB URL** — it doesn't read from `.env`; update it manually if your connection string differs.
