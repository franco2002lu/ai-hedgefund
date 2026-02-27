# AI Hedge Fund

Agentic AI Hedge Fund — modular monolith built with FastAPI, PostgreSQL, and SQLAlchemy async.

## Prerequisites

- Python 3.12+
- Docker Desktop (for PostgreSQL)

## Setup

### 1. Clone and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Start PostgreSQL

```bash
docker compose -f infrastructure/docker-compose.yml up -d
```

This starts PostgreSQL 16 on **port 5433** (to avoid conflicts with any local Postgres).

### 3. Configure environment

```bash
cp .env.example .env
```

Default values work out of the box with the Docker Compose setup.

### 4. Run database migrations

```bash
alembic upgrade head
```

### 5. Seed test data

The smoke tests and equities pipeline expect a fund and branches with specific UUIDs:

```bash
python -c "
import asyncio
from sqlalchemy import text
from app.db.connection import engine

async def seed():
    async with engine.begin() as conn:
        await conn.execute(text(\"\"\"
            INSERT INTO funds (id, name, description, total_aum, execution_mode, created_at)
            VALUES ('11111111-1111-1111-1111-111111111111', 'Test Fund', 'Smoke test fund', 0, 'paper', NOW())
            ON CONFLICT (id) DO NOTHING
        \"\"\"))
        await conn.execute(text(\"\"\"
            INSERT INTO branches (id, fund_id, name, branch_type, status, allocated_capital, execution_cadence, created_at)
            VALUES
              ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111',
               'Equities Branch', 'equities', 'active', 0, 'daily', NOW()),
              ('33333333-3333-3333-3333-333333333333', '11111111-1111-1111-1111-111111111111',
               'Equities Growth', 'equities', 'active', 0, 'daily', NOW()),
              ('44444444-4444-4444-4444-444444444444', '11111111-1111-1111-1111-111111111111',
               'Equities Value', 'equities', 'active', 0, 'daily', NOW())
            ON CONFLICT (id) DO NOTHING
        \"\"\"))
    await engine.dispose()

asyncio.run(seed())
"
```

### 6. Start the server

```bash
uvicorn app.main:app --port 8000
```

Health check: `curl http://localhost:8000/health`

## Verification

Run the tests below with the server running on `localhost:8000`.

### Checkpoint 1 — Scaffold and DB

Verify the health endpoint returns successfully:

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"AI Hedge Fund","version":"0.1.0"}
```

### Checkpoint 2 — Portfolio + Event Log

Tests portfolio CRUD, cash management, snapshots, fund summary, and event logging:

```bash
python tests/test_checkpoint2.py
```

Expected output:

```
1. Health check OK
2. Portfolio created: <uuid>
3. Portfolio retrieved: cash=100000.0, nav=100000.0
4. Cash adjusted: cash=105000.0
5. Overdraft correctly rejected
6. Snapshot taken: nav=105000.0
7. Snapshots listed: total=1
8. Fund summary: total_nav=105000.0, branches=1
9. Event log: N events, types=[...]

=== ALL CHECKPOINT 2 TESTS PASSED ===
```

### Checkpoint 3 — Data Platform

The Data Platform endpoints are exercised as part of the e2e smoke test (steps 4-5). You can also test individually:

```bash
# Upsert an instrument
curl -X PUT http://localhost:8000/api/v1/instruments \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAPL","name":"Apple Inc.","asset_class":"equity","exchange":"NASDAQ"}'

# Fetch prices
curl "http://localhost:8000/api/v1/prices/AAPL?start_date=2025-01-01&end_date=2025-01-10"

# Fetch fundamentals
curl http://localhost:8000/api/v1/fundamentals/AAPL/metrics
curl http://localhost:8000/api/v1/fundamentals/AAPL/facts
```

### Checkpoint 4 — Full E2E Smoke Test

Runs a 17-step trade lifecycle: create portfolio, fetch prices, submit BUY order, verify fill, check position/cash/events, take snapshot, submit SELL order, verify partial close with realized P&L, and validate fund summary.

**Important:** This test requires a clean database. Reset before running:

```bash
# Reset DB (drops all data)
python -c "
import asyncio
from sqlalchemy import text
from app.db.connection import engine

async def reset():
    async with engine.begin() as conn:
        await conn.execute(text('DROP SCHEMA public CASCADE'))
        await conn.execute(text('CREATE SCHEMA public'))
    await engine.dispose()

asyncio.run(reset())
"

# Re-apply migrations and seed
alembic upgrade head
# (run the seed script from step 5 above)

# Restart the server, then run:
python tests/test_e2e_smoke.py
```

Expected output:

```
1.  App healthy
2-3. Portfolio created with $100K cash
4.  Instrument AAPL upserted: <uuid>
5.  AAPL price fetched: N bars, latest close=$XXX.XX
6.  BUY 10 AAPL filled @ $XXX.XX
7.  Order status = filled
8.  Position: qty=10.0, cost_basis=$XXXX.XX
9.  Portfolio: cash=$XXXXX.XX, nav=$100000.00
10. Events: [portfolio.updated, trade.executed, trade.requested]
11. Snapshot taken: nav=$100000.00
12. SELL 5 AAPL filled @ $XXX.XX
13. Position: qty=5.0, realized_pnl=$X.XX
14. Cash after sell: $XXXXX.XX
15. Portfolio summary: long_exp=$XXXX.XX, realized_pnl=$X.XX
16. Fund: total_nav=$XXXXX.XX, branches=1
17. Orders: 2 total
    Trades: 2 total

============================================================
  ALL END-TO-END SMOKE TESTS PASSED
  Phase 1 infrastructure is ready for branch modules!
============================================================
```

### Checkpoint 5 — Equities Branch Pipeline

Runs the LLM-powered screening + analysis pipeline. Requires `ANTHROPIC_API_KEY` in `.env` and the growth/value branches seeded (step 5).

**1. Create a portfolio for the growth branch:**

```bash
curl -X POST http://localhost:8000/api/v1/portfolios \
  -H "Content-Type: application/json" \
  -d '{"branch_id":"33333333-3333-3333-3333-333333333333","branch_type":"equities","initial_cash":1000000.0,"margin_requirement":0.5}'
```

**2. Trigger the growth pipeline:**

```bash
curl -X POST http://localhost:8000/api/v1/equities/growth/run
```

This will: fetch the VOOG ETF universe, screen stocks through 11 filters, fan out to 3 LLM analyst agents (news, fundamentals, technical), aggregate scores via the portfolio manager, and execute paper trades. It may take 1-2 minutes depending on how many stocks pass screening.

**3. Inspect results:**

```bash
# Screening runs (universe size, how many passed)
curl http://localhost:8000/api/v1/equities/growth/screening-runs

# Agent signals (each analyst's score per stock)
curl http://localhost:8000/api/v1/equities/growth/signals

# Portfolio decisions (composite scores + generated orders)
curl http://localhost:8000/api/v1/equities/growth/decisions

# Check the portfolio for positions created by the pipeline
curl http://localhost:8000/api/v1/portfolios/33333333-3333-3333-3333-333333333333/positions
```

**4. (Optional) Run the value pipeline:**

```bash
curl -X POST http://localhost:8000/api/v1/portfolios \
  -H "Content-Type: application/json" \
  -d '{"branch_id":"44444444-4444-4444-4444-444444444444","branch_type":"equities","initial_cash":1000000.0,"margin_requirement":0.5}'

curl -X POST http://localhost:8000/api/v1/equities/value/run
```

**5. View/update pipeline configuration:**

```bash
# View current config (screening thresholds, LLM models, portfolio weights)
curl http://localhost:8000/api/v1/equities/config

# Update config (e.g., use a cheaper model, reduce holdings)
curl -X PUT http://localhost:8000/api/v1/equities/config \
  -H "Content-Type: application/json" \
  -d '{"portfolio":{"target_holdings":10},"agents":{"news_analyst":{"model":"claude-haiku-4-5-20251001"}}}'
```

## Project Structure

```
app/
  config.py                          # pydantic-settings configuration
  main.py                            # FastAPI app, lifespan, router registration
  db/
    connection.py                    # async engine + session factory
    models.py                        # SQLAlchemy ORM models (all tables)
    migrations/                      # Alembic migrations
  common/
    enums.py                         # shared enumerations
    events/                          # event schemas (trade, portfolio, risk, etc.)
    models/                          # Pydantic domain models
    interfaces/                      # abstract base classes (broker, data adapters, repos)
    schemas/                         # API response/pagination schemas
  modules/
    portfolio/                       # Portfolio management (positions, cash, snapshots)
    data_platform/                   # Market data (Yahoo Finance adapter, cache, rate limiter)
    event_log/                       # Append-only event log
    trade_execution/                 # Order lifecycle, paper trading adapter
infrastructure/
  docker-compose.yml                 # PostgreSQL 16
tests/
  test_checkpoint2.py                # Portfolio + event log integration test
  test_e2e_smoke.py                  # Full 17-step trade lifecycle test
plans/
  architecture/                      # Architecture design docs
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/v1/portfolios` | Create portfolio |
| GET | `/api/v1/portfolios/{branch_id}` | Get portfolio |
| PUT | `/api/v1/portfolios/{branch_id}/cash` | Adjust cash |
| GET | `/api/v1/portfolios/{branch_id}/positions` | List positions |
| GET | `/api/v1/portfolios/{branch_id}/positions/{symbol}` | Get position |
| POST | `/api/v1/portfolios/{branch_id}/snapshots` | Take snapshot |
| GET | `/api/v1/portfolios/{branch_id}/snapshots` | List snapshots |
| GET | `/api/v1/fund/{fund_id}/summary` | Fund summary |
| GET | `/api/v1/events` | Query event log |
| GET | `/api/v1/prices/{symbol}` | Fetch price bars |
| GET | `/api/v1/fundamentals/{symbol}/metrics` | Financial metrics |
| GET | `/api/v1/fundamentals/{symbol}/line-items` | Financial line items |
| GET | `/api/v1/fundamentals/{symbol}/facts` | Company facts |
| GET | `/api/v1/news` | Fetch news |
| PUT | `/api/v1/instruments` | Upsert instrument |
| GET | `/api/v1/instruments/{symbol}` | Get instrument |
| POST | `/api/v1/orders` | Submit order |
| GET | `/api/v1/orders/{order_id}` | Get order |
| GET | `/api/v1/orders` | List orders |
| GET | `/api/v1/trades/{trade_id}` | Get trade |
| GET | `/api/v1/trades` | List trades |
| POST | `/api/v1/equities/{branch_name}/run` | Trigger equities pipeline |
| GET | `/api/v1/equities/{branch_name}/signals` | List agent signals |
| GET | `/api/v1/equities/{branch_name}/screening-runs` | List screening runs |
| GET | `/api/v1/equities/{branch_name}/decisions` | List portfolio decisions |
| GET | `/api/v1/equities/config` | Get equities config |
| PUT | `/api/v1/equities/config` | Update equities config |
