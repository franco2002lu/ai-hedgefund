# Investor Reporting Foundation: Mark-to-Market, Snapshots, Inception Baseline, Cash Correctness

**Date:** 2026-07-15
**Status:** Approved
**Scope decisions (user):** include cash/execution fix; daily snapshot cron; backfill applied to prod after dry-run validation; weekly report auto-committed to the repo.

## Motivation

A 2026-07-15 audit of Neon prod (`small-firefly-14151124`) found the fund cannot report
"total +/− since the $1M start":

- `portfolio_snapshots` is empty — no NAV time series exists.
- NAV is never marked to market. `handle_trade_executed` sets
  `nav = cash + Σ long_cost_basis`, so `unrealized_pnl` is permanently 0.00 and open
  positions are carried at cost (growth NAV 1,002,125.91 = 1M + realized only).
- No inception baseline: `branches.allocated_capital` is 0.00; nothing records that each
  branch started with $1,000,000 (portfolios seeded 2026-06-10, first fills 2026-06-15).
- Both branches hold **negative cash** (growth −55,473.33, value −2,591.99 as of
  2026-07-13): `generate_orders` iterates symbols alphabetically so buys can execute
  before the sells that fund them, target weights sum to ~1.0 with no buffer, and
  `_validate_order` has no BUY cash check (`pass`). The paper broker fills anyway.
  This is silent leverage that overstates returns.
- The weekly digest shows counts only (universe/screened/orders/duration) — no NAV,
  returns, holdings, or trades.

## Components

### 1. `PortfolioService.mark_to_market` (app/modules/portfolio/service.py)

```python
async def mark_to_market(self, branch_id: str, prices: dict[str, float | None]) -> MarkToMarketResult
```

- Long-only path (live fund holds no shorts): per open position, market value =
  `price × long_quantity`.
- **Missing/None price ⇒ position keeps cost basis** (counted in `unpriced`, warning
  logged). Deliberate divergence from the backtest's zero-out rule: a live Yahoo outage
  must not crater reported NAV. The backtest's `_mark_to_market` is unchanged.
- Updates portfolio via `update_portfolio_fields`: `total_long_exposure` (= Σ MV),
  `unrealized_pnl` (= Σ MV − Σ cost basis), `nav` (= cash + Σ MV).
- Appends `PortfolioUpdatedEvent(trigger="mark_to_market")` with the marked figures.
- Returns `MarkToMarketResult`: `nav`, `cash`, `unrealized_pnl`, `realized_pnl`,
  `priced`/`unpriced` counts, and `positions_detail` — list of
  `{symbol, quantity, price, market_value, weight, cost_basis, unrealized_pnl}`
  sorted by market value desc (ties broken by symbol for determinism). Weights use
  the marked NAV.
- **Writer reconciliation** (found in review): `total_long_exposure` becomes
  market-valued after the first mark, so (a) `PostgresPortfolioRepository.update_cash`
  drops the `+ unrealized_pnl` term from its NAV recomputation (it would double-count),
  and (b) `handle_trade_executed` resets `unrealized_pnl` to 0 when it reverts
  exposure to cost basis — the portfolio is then consistently cost-based until the
  next mark (which the weekly/daily jobs run immediately after trading).

### 2. Snapshot `positions_detail` (repository + interface + model)

- `SnapshotRepository.create(portfolio_id, branch_id, positions_detail: list[dict] | None = None)`
  — optional kwarg added to the abstract interface
  (app/common/interfaces/repositories.py), `PostgresSnapshotRepository` (persists to the
  existing, currently-unused `portfolio_snapshots.positions_detail` JSONB column), and
  `InMemorySnapshotRepository` (app/modules/backtest/state.py — stores it).
- `PortfolioSnapshot` domain model gains `positions_detail: list[dict] | None = None`;
  Postgres `_to_domain` maps it.
- `PortfolioService.take_snapshot(branch_id, positions_detail=None)` passes it through.
  Existing callers are unaffected.

### 3. Weekly pipeline wiring (scripts/run_weekly_pipeline.py)

After `runner.execute(...)` returns a completed summary — in a dedicated session using
the exact attribution pattern (try/except, logged warning, **never fails the trading
run**):

1. Read open positions; build `prices` via `data_service.get_current_price(symbol)`
   (TTL-cached; ≤30 symbols/branch).
2. `mark_to_market(branch_id, prices)`.
3. `take_snapshot(branch_id, positions_detail=result.positions_detail)`.
4. Build `PortfolioReport` (see §6) and attach to `summary.portfolio_report`.

Runs for `completed` runs; also for `skipped` runs (idempotent re-invocations still
refresh marks; snapshot skipped if one already exists for today — see §5 idempotency
helper). Failed runs are left alone (session already rolling back).

### 4. Inception baseline

- **Prod data fix (one-time SQL, reversible):**
  `UPDATE branches SET allocated_capital = 1000000 WHERE id IN ('33333333-3333-3333-3333-333333333333', '44444444-4444-4444-4444-444444444444');`
  `UPDATE funds SET total_aum = 2000000 WHERE id = '11111111-1111-1111-1111-111111111111';`
  CLAUDE.md's seed SQL is updated to seed these values for fresh environments.
- **API:** the repository's `get_fund_summary` adds raw `inception_date`
  (= portfolio `created_at` date) per branch; `PortfolioService.get_fund_summary`
  computes the derived metrics (testable without a DB): per-branch
  `initial_capital` (= allocated_capital), `total_pnl` (= nav − initial),
  `total_return_pct` (= pnl/initial; `None` when initial == 0), and fund-level
  `total_initial_capital`, `total_pnl`, `total_return_pct`.
- The CLI/digest computes branch returns from `PortfolioSummary.allocated_capital`
  (already populated by `_to_summary`).

### 5. Daily snapshot job

- `scripts/take_daily_snapshot.py`: for each branch that has a portfolio — build prices,
  `mark_to_market`, `take_snapshot`. **Idempotent:** skips a branch if a snapshot with
  `snapshot_at` on today's NY date already exists. New
  `SnapshotRepository.latest_by_branch(branch_id, before: date | None = None)` serves
  both this check (`before=None` → latest overall, compare its NY date to today) and
  the digest's WoW math (`before=today` → latest snapshot strictly before today).
  Exit 0 on success/skip; nonzero only on infrastructure errors.
- `.github/workflows/daily-snapshot.yml`: cron `30 21 * * 1-5` (~5:30pm ET; DST wobble
  acceptable), checkout + Python + `pip install -e ".[dev]"` + run script. Secrets:
  `HEDGE_DATABASE_URL` only (no LLM calls). `workflow_dispatch` enabled.
- Monday semantics (corrected in review): the daily job's per-NY-date skip means
  Mondays get exactly ONE snapshot — the weekly pipeline's ~10am ET post-rebalance
  mark — while Tue–Fri points are EOD. Acceptable artifact (documented for report
  consumers); if the weekly run fails before snapshotting, the daily job still lands
  a Monday EOD point. Report readers dedupe to **last snapshot per NY date**.

### 6. Digest + auto-committed weekly report

- `WeeklyRunSummary` gains `portfolio_report: PortfolioReport | None = None`
  (dataclass in weekly_runner.py): `nav`, `cash`, `cash_pct`, `unrealized_pnl`,
  `realized_pnl`, `initial_capital`, `inception_return_pct`, `wow_return_pct`
  (vs last-per-day snapshot ≥1 day old; `None` when no prior snapshot),
  `top_holdings` (top 5 of positions_detail), `trades` (this run's fills via
  `TradeRepository.list_trades(branch_id, since=run start)`: symbol/side/qty/price/notional),
  `unpriced` count.
- `render_digest` per completed branch adds: NAV with WoW% and since-inception %/$,
  cash ($ and % NAV), unrealized/realized P&L, top-5 holdings `SYM w%`, trades table,
  and a ⚠️ line when `unpriced > 0` or cash < 0.
- Report files: `run_weekly_pipeline --report-dir scheduled_run_results` writes the
  digest to `scheduled_run_results/{run_date}.md` (identical to the job summary), and
  `scripts/build_report_json.py` (invoked by the workflow after the pipeline, also
  runnable manually) regenerates `scheduled_run_results/report.json` **from the DB** —
  full deduped NAV series per branch (date, nav, cash, unrealized/realized), current
  holdings, trade history, attribution history, inception metrics. Self-healing
  (regenerated wholesale each run) and the future GH Pages data source.
- Weekly workflow: add `permissions: contents: write`; after the pipeline step, run the
  report script, then `git add scheduled_run_results && git commit -m "chore(report): weekly report {date} [skip ci]" && git push`
  (bot identity; `git pull --rebase` first; commit only if there are changes).
  The daily job commits nothing.

### 7. Cash/execution fix

- **Sells before buys:** `PortfolioManager.generate_orders` returns sells first, then
  buys, alphabetical within each side (explicit sort at return; preserves the
  determinism the existing comment requires). Downstream consumes orders in list order.
- **Cash buffer:** `PortfolioConfig.cash_buffer_pct: float = 0.01`. `size_positions`
  scales every final weight by `(1 − cash_buffer_pct)` after the cap loop
  (Σ targets ≈ 0.99). Covers 5 bps slippage and decision-to-fill drift.
- **Overdraft rejection:** in `TradeExecutionService.submit_order`, after the broker
  returns a priced fill and before persisting: for BUY, compute
  `cost = price × qty + commission`; if `cost > portfolio.cash`, mark the order
  REJECTED (`rejection_reason="Insufficient cash: cost X > cash Y"`), append
  `TradeRejectedEvent`, return the standard rejection dict. Uses the exact fill price;
  no broker-interface change; applies to paper and backtest brokers alike.
  SELL validation is unchanged. (SHORT/COVER unused by the live fund; not gated.)
- **Self-healing (corrected during review):** pro-rata target shrinkage (~0.33%/name)
  falls below `min_rebalance_threshold` (2%), so the buffer alone does NOT force a
  net-sell in a no-churn week. The healing mechanism is the fill-time rejection: while
  cash is negative, sells (which execute first) fill and buys are rejected until cash
  covers them, so any week with normal churn ratchets cash back toward the buffer.
  Every prod week so far has had churn; expect healing within one or two rebalances,
  no manual intervention. Do not deploy the buffer/ordering change without the
  rejection gate.
- **Alternatives rejected:** scaling buy qty to available cash (hides sizing bugs);
  pre-broker cash check on a stale quote (inaccurate); check inside the paper adapter
  (broker is stateless; would lose the rejected-order audit row).

### 8. Backfill script (`scripts/backfill_snapshots.py`)

- Replays `trades` per branch in `executed_at` order with the same avg-cost math as
  `handle_trade_executed` (long-only asserted; commission included), starting from
  `initial_capital` cash at the first trade date.
- For each NY trading day from first trade to yesterday: apply that day's trades, value
  positions at daily closes (`data_service.get_prices`, one call per symbol for the full
  window; carry forward the last known close on gap days), insert a snapshot
  (`snapshot_at` = 16:00 America/New_York on that date, stored as UTC) **unless that
  date already has one**. Fills `positions_detail`.
- **Validation gate:** reconstructed final cash must match live `portfolios.cash`
  within $0.50 (the column is Numeric(18,2): each run boundary contributes ±half-cent
  rounding residue, so cent-exact matching is noise-prone; real failure modes are
  dollars), and per-symbol quantities within 1e-6. Printed as a comparison table;
  on mismatch, `--apply` refuses to write. Additionally, if ANY ever-held symbol
  returns zero price bars (partial Yahoo rate-limiting), the script warns loudly and
  refuses `--apply` — a cost-basis-flat symbol would silently corrupt the NAV series.
- `--dry-run` default; `--apply` writes. Applied to prod once after validation.

## Explicit behavior changes

- Backtests share `TradeExecutionService` and `size_positions`, so they inherit the
  buffer (0.99 deployment) and overdraft rejection — more realistic; affected backtest
  unit tests updated deliberately.
- Next live run trades differently by design: sells-first, 99% target deployment,
  possible one-time net-sell to restore positive cash.
- `portfolios.nav`/`unrealized_pnl` become market-valued after the first MTM; the
  equities pipeline's `current_positions` weights (already price-based) now divide by a
  marked NAV — consistent rather than mixed-basis.

## Testing

Unit (all offline, no DB): MTM math incl. unpriced fallback and empty-portfolio no-op;
snapshot positions_detail passthrough (both repos); sells-first ordering; buffer × cap
interplay (Σ = 0.99, cap still respected); insufficient-cash rejection (boundary: cost
== cash passes, cost > cash rejects; sell path untouched); fund-summary return fields
(incl. initial 0 → None); digest rendering with and without portfolio_report; backfill
reconstruction known-answer (synthetic trades + closes), gap-day carry-forward, and
validation-mismatch refusal; daily-job same-day idempotency; `latest_by_branch`.
Full `pytest tests/unit/ -q` and `ruff check` must pass.

## Rollout

1. Land code (staged for user review/commit — no commits to main by Claude).
2. `backfill_snapshots.py --dry-run` against prod → review validation table → `--apply`.
3. Prod SQL baseline (allocated_capital / total_aum).
4. Verify: snapshots count, fund summary returns, digest render from a local
   `render_weekly_report` run.
5. Next Monday's run exercises MTM/snapshot/digest/commit natively; daily cron starts
   populating EOD snapshots immediately after the workflow file lands on main.
