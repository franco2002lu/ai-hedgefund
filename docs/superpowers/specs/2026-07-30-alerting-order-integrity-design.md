# Alerting + Order-Path Integrity (D1 + S2) — Design

**Date:** 2026-07-30
**Roadmap items:** D1 (failure alerting + staleness watchdog) and S2 (order-path integrity counters, risk checks, persisted validation rejections) from `plans/2026-07-30-improvement-roadmap.md` §4/§7 — the two co-#1 items (score 45 each).
**Branch:** `feat/alerting-order-integrity` off `35fc1b2`. Built in the healthy clone at `~/dev/ai-hedgefund-final` (Desktop checkout is iCloud-degraded).
**Deploy path:** user merges to `main` and pushes before Sunday 2026-08-02 night so everything is live for the Theme A debut run (cron Monday 13:00 UTC, observed start ~15:00). Nothing in this change alters which trades happen — selection, sizing, and execution order are untouched.

## Motivation (evidence)

- Production has **zero push alerting**: no `if: failure()` steps, no notify of any kind (confirmed by grep across `.github/`, `app/`, `scripts/`). A failed or never-fired Monday run is discovered only by looking.
- A CRITICAL `risk_alerts` row is written on a **green** workflow run (`run_weekly_pipeline.py:179-226`) — today nobody would be notified at all.
- On 07-20 + 07-27, **8 sell orders (~$530k intended proceeds) were silently dropped**: `_validate_order` failures return from `submit_order` at `trade_execution/service.py:52-54` **before** any Order row or event exists (oversell check at `:260-261`). This starved all 10 recorded buy rejections. The digest's "Orders placed: 13" counts *generated* orders (`weekly_runner.py:375`) while the DB held 9 — the mismatch is visible nowhere.
- Value branch sat at **3.49% cash** on 07-27, under the 5% `CASH_PCT_WARN` (`risk_checks.py:17`) — sizing targets 1% cash, so a 3.5% drift deserved a WARNING.

## Components

### 1. Workflow failure alerts (first-party `gh`, no marketplace actions)

Both `.github/workflows/weekly-rebalance.yml` and `daily-snapshot.yml`:

- Add `issues: write` to `permissions` (daily-snapshot currently has no permissions block; weekly has `contents: write`).
- Append a final step:

```yaml
- name: Alert on failure
  if: failure()
  env:
    GH_TOKEN: ${{ github.token }}
  run: bash .github/scripts/ops_alert.sh "Weekly rebalance failed" "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

`.github/scripts/ops_alert.sh` (shared, ~15 lines):
- `gh label create ops-alert --force` (idempotent; `issues: write` covers labels).
- Title = `[ops-alert] <subject> — <UTC date>`.
- Dedup: `gh issue list --state open --label ops-alert --json number,title` filtered on exact title → if found, `gh issue comment <n>` ("recurred: <run url>"); else `gh issue create --label ops-alert`.
- Never fails the job it reports on (`|| true` around gh calls is NOT used — the step already runs only on failure; a broken alert step turning the job's conclusion from failure to failure is a no-op).

Notification reaches the user through normal GitHub issue notifications (email/mobile).

### 2. Staleness watchdog — `ops-watchdog.yml` + `scripts/ops_watchdog.py`

New scheduled workflow (separate from daily-snapshot so a watchdog bug can't redden the snapshot job):

- `cron: "0 17 * * 1"` (fast Monday coverage) and `cron: "30 23 * * 1-5"` (evenings, after the 21:30 snapshot + slack).
- `permissions: issues: write`; env: `HEDGE_DATABASE_URL` (existing secret), `HEDGE_EQUITIES_ENABLED_BRANCHES` (existing var), `GH_TOKEN`.
- Python 3.12, `pip install -e ".[dev]"`, then `python -m scripts.ops_watchdog`.
- Its own `if: failure()` alert step (subject "Ops watchdog crashed") — a broken watchdog can't fail silent.

`scripts/ops_watchdog.py` (async, reuses `scripts/common.py` init; DB access read-only):

| # | Check | Trigger | Alert subject |
|---|---|---|---|
| 1 | Stuck run | `pipeline_runs.status='running'` and `started_at < now() − 2h` | "Pipeline run stuck in running" |
| 2 | Monday run missing | NY-Monday and `now ≥ 16:30 UTC` and any enabled branch lacks a `completed` run with `run_date = today(NY)` | "Weekly run missing for <branch>" |
| 3 | Stale snapshots | latest `portfolio_snapshots.snapshot_at` per branch older than 2 business days (weekend-aware) | "No fresh snapshot for <branch>" |
| 4 | Unnotified CRITICAL | any `risk_alerts` row `level='CRITICAL' AND resolved=false AND created_at > now() − 25h` | "CRITICAL risk alert: <metric>" |

Behavior contract:
- Raising alerts is **success**: exit 0 whether or not issues were opened. Exit 1 only on internal error (unreachable DB, bug) → workflow red → its own failure alert.
- Issue creation goes through a small `ensure_issue(title, body)` helper that shells to `gh` via an injected runner function (unit-testable with a fake); when `GH_TOKEN` is absent (local runs), it prints the would-be alert and returns — dry-run by default on dev machines.
- Check 2 depends on NY dates: reuse the existing `today_ny()` helper used by the runner (`run_weekly_pipeline.py:81`) — no new timezone logic.

### 3. Order-flow accounting through the pipeline

Today the execute node receives a result dict per order — including the invisible validation drops (`{"success": False, "order_id": None, ...}`) — and discards everything but fills.

- `PortfolioManager.generate_orders` additionally returns skip metadata: for each symbol it declines to order, `{symbol, reason, is_exit}` where reason ∈ {`unpriced`, `below_entry_threshold`}. Unpriced skips currently vanish (`portfolio_manager.py:187-188, 212-216`); sub-threshold entries are logged only (`:205`). Return shape becomes `(orders, skipped)`; single call site updated (`equities/service.py:317`), so backtests inherit through the shared path with no engine changes.
- The execute loop tallies result dicts into an `order_flow` dict: `generated`, `persisted` (result has an `order_id`), `filled`, `rejected`, `dropped` (`order_id is None` — structurally impossible after component 5, kept as the tripwire), `skipped_unpriced`, `skipped_below_entry`, plus `rejections: [{symbol, side, reason}]` and `skips: [{symbol, reason, is_exit}]`.
- `order_flow` flows into the pipeline result → `pipeline_runs.summary_json` → digest.
- Digest (`weekly_runner.render_digest`): replace `- Orders placed: N` with:
  - `- Orders: 13 generated / 13 persisted / 8 filled / 5 rejected` (counts must reconcile: persisted = filled + rejected; generated = persisted once component 5 lands)
  - When any rejected: `  - rejected: BKNG, CSCO, JPM, MA, MU — Insufficient cash …` (symbol list + first reason, truncated sanely)
  - When any skipped: `  - skipped: XYZ (unpriced, exit)` — a separate line, exits called out.
  - Old-format summaries (pre-change rows) render the legacy line — the renderer falls back when `order_flow` is absent.

### 4. Risk-check additions (`risk_checks.py`)

`evaluate_post_run_invariants(report, config, order_flow: dict | None = None)` — new optional arg, `None` ⇒ new checks skip (backward compatible with all existing call sites/tests).

| Check | Condition | Level | Metric |
|---|---|---|---|
| Lost orders | `generated − persisted > 0` | CRITICAL | `orders_lost` |
| Rejected orders | `rejected > 0` | WARNING | `orders_rejected` |
| Unpriced skips | `skipped_unpriced > 0` | WARNING | `orders_skipped_unpriced` (message flags exits: a skipped exit is a stuck position) |
| Cash balloon (tighten) | `cash/nav > CASH_PCT_WARN` with **0.05 → 0.03** | WARNING (existing) | `cash_pct` |

Wiring: `run_weekly_pipeline._evaluate_and_persist_alerts` passes the run's `order_flow` (from the summary) into the evaluator. Everything else (persist to `risk_alerts` + event log, savepoint isolation, digest rendering, unknown-level escalation) is untouched and reused.

Note: `below_entry_threshold` skips are by-design behavior (Theme A3) — counted in the digest, **not** alerted.

### 5. Hot-path: persist validation rejections (ships now, per user decision)

In `TradeExecutionService.submit_order`, a `_validate_order` failure currently returns a bare dict (`service.py:52-54`). New behavior: create the Order row with `status=REJECTED` and `rejection_reason=<validation error>`, append `TradeRejectedEvent`, return `{"success": False, "order_id": <id>, "status": "rejected", "message": ...}` — mirroring the existing fill-time rejection path (`service.py:123-135`) exactly. No broker call is made (unchanged).

- Had this existed in July, the 8 dropped sells would be 8 visible REJECTED rows with reason `Insufficient position: hold X, tried to sell Y`.
- Post-Theme-A, exits are sized at held quantity, so this path should stay quiet; it is the audit trail for whenever it doesn't.
- Backtests inherit via `InMemoryOrderRepository` — harmless (rejected rows in memory).
- The order-flow `dropped` counter should now always read 0; if it ever doesn't, the CRITICAL `orders_lost` check fires.

## Testing

TDD throughout; every test listed fails before its implementation lands.

- `tests/unit/test_ops_alert_script.py` — the shell helper is ~15 lines of `gh` calls with no test harness of its own; a subprocess test asserts `bash -n .github/scripts/ops_alert.sh` exits 0 (syntax), and the dedup/create logic is reviewed line-by-line (the Python twin of that logic in the watchdog IS unit-tested).
- `tests/unit/test_ops_watchdog.py` — each check true/false with a stub session (stuck run, Monday-missing incl. not-Monday and pre-16:30 no-ops, stale snapshot with weekend arithmetic, CRITICAL row); quiet path exits 0 with no issues; internal error exits 1; `ensure_issue` create-vs-comment dedup via fake `gh` runner; GH_TOKEN-absent dry-run.
- `tests/unit/equities/test_order_flow.py` — tally from result dicts (filled/rejected/dropped/persisted); `generate_orders` returns skip metadata for unpriced (incl. `is_exit=True` for a target-0 name with no price) and below-entry cases; summary lands in pipeline result.
- `tests/unit/equities/test_risk_checks.py` (extend) — each new check fires/holds at boundary; `order_flow=None` keeps old behavior; `CASH_PCT_WARN` boundary tests move 0.05 → 0.03.
- `tests/unit/test_digest_portfolio_report.py` (extend) — new orders line, rejected/skipped sublines, legacy fallback when `order_flow` absent.
- `tests/unit/test_trade_execution_service.py` / `test_trade_execution_sequence.py` (extend) — **BLK replay:** sell 74.0814 vs held 74.0804 → REJECTED row persisted with `Insufficient position` reason + `TradeRejectedEvent` appended + broker never called; same for BUY with missing portfolio; existing clamp tests unchanged (a within-1e-6 oversell still clamps and fills).
- Full suite green (baseline 1,248 + new) and `ruff check` clean before handoff.

Workflows have no unit-test harness: YAML kept minimal, mirroring the two existing files' proven structure; reviewed line-by-line.

## Out of scope

ntfy/Slack channels; Yahoo retry/backoff and daily-path unpriced marks (D2); conviction-ordered funding (S3, next cycle); retrying rejected buys; alert resolution workflow (`resolved` stays manual); DB idempotency constraints (D4); lockfile (D6, separate commit-able chore).

## Rollout / verification

1. User merges branch → `main`, pushes before Sunday night.
2. Sunday: manually dispatch `ops-watchdog.yml` once (workflow_dispatch enabled) → expect green, no issues (or a real one).
3. Monday post-debut checklist (roadmap §3) gains: "no `ops-alert` issues open; digest orders line reconciles generated = persisted = filled + rejected (+ skips accounted)".
