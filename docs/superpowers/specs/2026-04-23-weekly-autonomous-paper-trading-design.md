# Weekly Autonomous Paper Trading — Design

**Date:** 2026-04-23
**Status:** Draft, awaiting review
**Sprint goal:** Run the equities paper-trading pipeline on a weekly cadence without human intervention.

## Context

The codebase today has a working equities pipeline (universe → screening → 3 LLM analyst agents → portfolio manager → paper fills) exposed via `POST /api/v1/equities/{branch_name}/run`. Paper trading is wired end-to-end. Portfolio state persists correctly between runs via Postgres.

What's missing: any mechanism to trigger the pipeline on a schedule, any idempotency guard against replaying a run, and any notification when runs succeed or fail. See `plans/architecture/PHASE2-EQUITIES-BRANCH.md` for pipeline details.

## Operating model: hybrid

- The weekly run executes **independently** of any long-running server process. It must not depend on FastAPI being up.
- The FastAPI server runs **on-demand** from the user's Mac when they want to inspect state, trigger ad-hoc runs, or query the event log. It is not required for the weekly job to function.
- Both the weekly job and the on-demand server connect to the **same hosted Postgres** — a single source of truth.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ GitHub repo                                                 │
│   .github/workflows/weekly-rebalance.yml                    │
│     schedule: cron "0 13 * * 1"  (Mon 13:00 UTC = 8am ET)   │
│     workflow_dispatch: manual trigger with inputs           │
└──────────────┬──────────────────────────────────────────────┘
               │ fires on schedule or manual dispatch
               ▼
┌─────────────────────────────────────────────────────────────┐
│ GitHub Actions runner (ephemeral Ubuntu)                    │
│   1. Checkout code                                          │
│   2. pip install -e .                                       │
│   3. alembic upgrade head (idempotent)                      │
│   4. python -m scripts.run_weekly_pipeline                  │
│        └─► imports EquitiesBranchService                    │
│        └─► runs pipeline in-process, one branch at a time   │
│        └─► writes events + snapshot to Neon                 │
│        └─► emits markdown digest to $GITHUB_STEP_SUMMARY    │
│   5. On any failure → GH's built-in email to repo owner     │
└──────────────┬──────────────────────────────────────────────┘
               │ all DB traffic
               ▼
┌─────────────────────────────────────────────────────────────┐
│ Neon Postgres (hosted, serverless, always reachable)        │
│   portfolio, positions, events, branches, orders,           │
│   pipeline_runs (new)                                       │
└──────────────┬──────────────────────────────────────────────┘
               ▲
               │ (hybrid: connects when user starts it)
┌──────────────┴──────────────────────────────────────────────┐
│ User's Mac                                                  │
│   uvicorn app.main:app --port 8000                          │
│   (HEDGE_DATABASE_URL points at Neon for inspection)        │
└─────────────────────────────────────────────────────────────┘
```

### Key design decisions

- **In-process CLI, not HTTP.** The weekly script imports `EquitiesBranchService` and calls `run_pipeline()` directly. No need to start a FastAPI server in the GH runner. Mirrors the pattern already used by `scripts/run_backtest.py`.
- **Neon as single source of truth.** Postgres on Neon (free tier ~0.5GB is ample for event log + portfolio history). Local `.env` can point at either local Docker Postgres (current default) or Neon by changing `HEDGE_DATABASE_URL`.
- **Schedule configurable without code changes.** Cron expression in the workflow YAML, but top-N, enabled branches, and manual overrides go through env vars and `workflow_dispatch` inputs.
- **Manual recovery on failure for sprint 1.** See "Idempotency & recovery" below for why.

## Components and files

### New files

| File | Purpose |
|---|---|
| `scripts/run_weekly_pipeline.py` | CLI entrypoint. Loads settings, opens DB session, resolves enabled branches, calls `EquitiesBranchService.run_pipeline()` for each, writes summary to `$GITHUB_STEP_SUMMARY`. |
| `.github/workflows/weekly-rebalance.yml` | GH Actions workflow. Cron + `workflow_dispatch`. Inputs: `branches` (default `growth,value`), `top_n` (default `50`), `dry_run` (default `false`), `force_retry` (default `false`). |
| `app/modules/equities/weekly_runner.py` | Thin orchestration layer. Performs idempotency check against `pipeline_runs`, invokes `run_pipeline()`, builds `WeeklyRunSummary` dataclass, returns markdown digest. |
| `app/db/models.py` — `PipelineRunModel` | New table. Columns: `run_id` (PK, text), `branch_id` (FK), `run_date` (date, America/New_York), `attempt` (int, default 1), `status` (enum: `running` / `completed` / `failed`), `started_at`, `completed_at`, `summary_json`, `error_msg`. Indexed on `(branch_id, run_date)` for history queries. No unique constraint on `(branch_id, run_date)` — that would block retry rows; idempotency is enforced in the `weekly_runner` check, not at the DB level. |
| `alembic/versions/XXXX_add_pipeline_runs.py` | Migration for the new table. |
| `app/modules/equities/api.py` — `GET /api/v1/pipeline-runs` | New endpoint to read `pipeline_runs` from the local server when inspecting history. Supports `?branch=growth&limit=10` filters. |
| `tests/unit/equities/test_weekly_runner.py` | Unit tests for idempotency, summary formatting, config parsing. |
| `tests/integration/test_weekly_pipeline_e2e.py` | E2E test of the script against a seeded DB. |

### Modified files

| File | Change |
|---|---|
| `app/modules/equities/config.py` | Add `top_n: int = 50` and `enabled_branches: list[str] = ["growth", "value"]` as env-overridable settings (`HEDGE_EQUITIES_TOP_N`, `HEDGE_EQUITIES_ENABLED_BRANCHES`). |
| `app/modules/equities/service.py` | `run_pipeline()` accepts optional `run_id: str`. No behavioral change if `run_id` is `None` (preserves current callers). |
| `.env.example` | Add `HEDGE_EQUITIES_TOP_N`, `HEDGE_EQUITIES_ENABLED_BRANCHES`. Document Neon `HEDGE_DATABASE_URL` format in a comment. |
| `CLAUDE.md` | New section: "Running the Weekly Pipeline" — documents the script, workflow, Neon setup, and manual dispatch. |

### Not built this sprint (YAGNI)

- Slack/Discord webhooks — GH email + Job Summary are sufficient
- Retry-on-failure logic inside the pipeline — failures are manual-review events
- Automatic crash recovery — see "Idempotency & recovery" for rationale
- Multi-region DB, read replicas, connection pooling tuning — Neon defaults handle this load
- Backfill script for past weekly runs — the first scheduled run is the start of history

## Data flow

Single weekly invocation, step-by-step:

```
GH Actions fires at Mon 13:00 UTC (8am ET)
  │
  ├─► run_date = today in America/New_York (NOT UTC — avoids Sunday/Monday drift)
  │
  ├─► For each enabled branch:
  │     │
  │     ├─► weekly_runner.execute(branch_name, run_date, force_retry)
  │     │     │
  │     │     ├─► SELECT * FROM pipeline_runs
  │     │     │     WHERE branch_id = ? AND run_date = ?
  │     │     │     ORDER BY attempt DESC LIMIT 1
  │     │     │       ├─ completed           → skip, log "already ran"
  │     │     │       ├─ running             → abort (RunInFlightError)
  │     │     │       ├─ failed + retry=True → new row, attempt=N+1,
  │     │     │       │                        run_id=f"{date}-{branch}-attempt{N+1}"
  │     │     │       ├─ failed + retry=False → abort (ManualInterventionRequired)
  │     │     │       └─ not found           → new row, attempt=1,
  │     │     │                                 run_id=f"{date}-{branch}"
  │     │     │
  │     │     ├─► EquitiesBranchService.run_pipeline(branch_id, run_id=run_id)
  │     │     │     ├─ Universe (top_n from config)
  │     │     │     ├─ Screener
  │     │     │     ├─ LLM agents (news + fundamentals + technical)
  │     │     │     ├─ Portfolio manager
  │     │     │     ├─ Trade execution (paper fills)
  │     │     │     └─ Portfolio snapshot
  │     │     │
  │     │     ├─► On success: UPDATE pipeline_runs SET status='completed',
  │     │     │                  completed_at=now(), summary_json=<result>
  │     │     │
  │     │     └─► On exception: UPDATE pipeline_runs SET status='failed',
  │     │                         error_msg=repr(exc), completed_at=now()
  │     │                         RE-RAISE so GH Actions fails loudly
  │     │
  │     └─► Collect WeeklyRunSummary → append section to $GITHUB_STEP_SUMMARY
  │
  └─► Exit 0 on all-branches-success, non-zero on any-branch-failure
```

## Idempotency & recovery

Idempotency is enforced by `weekly_runner` via a lookup against `pipeline_runs` before invoking the pipeline — NOT by a DB-level unique constraint (a DB constraint would block legitimate retry attempts). Three cases:

The lookup: find the latest row where `branch_id = ? AND run_date = ?` (ordered by `attempt` DESC). Decide based on its status:

**1. Accidental double-trigger same day** (e.g., scheduled run succeeds, user manually dispatches again).
Latest row is `status=completed` → skip, log "already ran". No double trades.

**2. Crash mid-pipeline** (runner OOM, network blip, Anthropic 500).
Latest row is `status=running` → abort with `RunInFlightError` ("previous run in-flight — inspect manually"). Recovery is manual: user inspects event log, decides whether trades went through, either flips status to `failed` in the DB or leaves state as-is.

**3. Intentional retry after failure.**
User re-triggers via `workflow_dispatch` with `force_retry=true`. Latest row is `status=failed` → insert new row with `attempt = previous.attempt + 1` and `run_id = f"{run_date}-{branch}-attempt{N}"`, status=running. Portfolio state picks up wherever it was left — `run_pipeline()` already loads current positions/cash from DB at start, so a partially-filled prior run just becomes the new starting state.

### Why no automatic recovery for sprint 1

Automatic recovery is high-risk/low-value at this stage because:

1. **"Replay" is not actually replay.** The pipeline's decisions come from 3 LLM agents. Running them again even 10 minutes later can produce different scores, signals, and orders. A second attempt is a fresh analysis, not a continuation. If the first attempt executed 4 of 10 orders before crashing, the retry's portfolio manager sees {4 new positions + leftover cash} as its starting state and plans fresh — resulting in a portfolio that neither attempt intended.

2. **Can't reliably tell what already happened.** Proper recovery needs: "intended target portfolio → trades actually submitted → delta." That requires splitting "plan" and "execute" into separate persisted phases with broker-level idempotency keys. The current pipeline does them in one pass.

3. **Auto-recovery hides failures.** A crash at 8:15 that auto-retries successfully at 8:30 means the user never learns about the underlying bug. For a system that will eventually trade real money, loud failures + manual review + learning from each one is the right posture.

Automatic recovery is a feature to add **after** (a) splitting plan/execute phases, (b) adding broker-level idempotency keys, and (c) seeing enough real failure modes to know what retry semantics are actually needed.

### What idempotency explicitly does NOT protect against

- A bug producing wrong orders on the first run — idempotency replays *the same wrong orders*. Correctness is separate.
- Yahoo Finance returning stale/null data — if rate-limited, cached prices from prior runs may be used. Tolerable for paper trading; flagged in the summary when 0 orders are produced.

## Error handling & observability

| Failure | Where caught | User sees |
|---|---|---|
| GH Actions can't start (YAML, missing secret) | GH platform | Email + red X in Actions tab |
| `alembic upgrade head` fails | Step fails | Email + job log |
| DB unreachable (Neon down/quota) | Script raises on connect | Email + job log with connection error |
| `run_pipeline()` raises mid-branch | `weekly_runner` catches, marks `pipeline_runs.status='failed'`, re-raises | Email + job log + `pipeline_runs` row with `error_msg` |
| One branch fails, the other succeeds | Each branch wrapped in try/except; failures collected | Job exits non-zero so GH alerts, but summary shows which branch(es) succeeded |
| Silent data issue (Yahoo returns None prices, 0 orders generated) | Not a hard failure | Summary flags "Growth: 0 orders (check data freshness)" — warning, not failure |

### Observability surface

**1. GitHub Actions Job Summary** — primary human-readable artifact, shown in the Actions run UI.

Example:

```markdown
## Weekly Rebalance — 2026-04-27

### Growth branch ✅
- Universe: 50 stocks (top 50 VOOG)
- Signals generated: 50 (fundamentals: 50, news: 48, technical: 50)
- Orders placed: 12 buys, 3 sells
- Top 5 holdings: NVDA (8.2%), MSFT (7.1%), AAPL (6.4%), GOOGL (5.8%), META (4.9%)
- NAV: $1,024,318 (+2.4% WoW)
- Pipeline duration: 6m 42s

### Value branch ✅
- ...

### Run metadata
- run_ids: 2026-04-27-growth, 2026-04-27-value
- DB: neon (prod)
- Commit: abc1234
```

**2. `pipeline_runs` DB table** — machine-queryable history. `GET /api/v1/pipeline-runs` (new endpoint) surfaces it when the local server is running.

**3. Existing event log** — untouched. Continues to accumulate `SignalGeneratedEvent`, `TradeExecutedEvent`, `PortfolioUpdatedEvent` as it does today.

### Not built (YAGNI)

- Metrics backend (Prometheus/Grafana)
- Distributed tracing
- Log aggregation service

## Testing strategy

### Unit tests (no DB, no network — run in <1s)

| File | Coverage |
|---|---|
| `tests/unit/equities/test_weekly_runner.py` | Idempotency (completed skip, running abort, failed+retry, new-row insert). `run_id` uses America/New_York date. Summary markdown formatting given a mock `RunResult`. |
| `tests/unit/equities/test_weekly_config.py` | `HEDGE_EQUITIES_TOP_N` parsing, `HEDGE_EQUITIES_ENABLED_BRANCHES` comma-split parsing, defaults apply when env missing. |

### Integration tests (`@pytest.mark.e2e`)

| File | Coverage |
|---|---|
| `tests/integration/test_weekly_pipeline_e2e.py` | Script runs end-to-end against seeded DB with `ANTHROPIC_API_KEY`. `pipeline_runs` row exists with `status=completed`. At least one `TradeExecutedEvent` logged (tolerant of 0 orders when Yahoo returns nulls — matches existing e2e pattern). Second invocation with same `run_id` is a no-op. |

### Pre-merge validation (manual, once)

- Trigger workflow via `workflow_dispatch` against Neon with `dry_run=true` (skips actual trade execution, validates plumbing).
- Verify Job Summary renders correctly.
- Verify email delivers on an intentionally-failed run.

### Not tested

- Cron timing (GH's responsibility)
- Neon uptime (Neon's responsibility)
- LLM output quality (out of scope — backtesting owns that)

## Open questions

None — all design decisions resolved in brainstorming. Implementation plan to follow.
