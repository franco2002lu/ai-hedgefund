# Theme A — P&L Integrity: Design Spec

**Date:** 2026-07-19
**Source:** `plans/2026-07-19-improvement-roadmap.md` §3 (Theme A), approved by user.
**Scope:** A1 invariant checks + regression tests + investor note; A2 sell-all semantics; A3 entry threshold; A4 NAV hard error. No changes to sizing weights, screening, or ranking (Phase 2).

## Context and evidence

- Production ran unintentionally levered: value cash hit **-$236,553 (~24%) the week of 06-22**; growth cash is -$55,473 today. Root cause: pre-07-16 execution filled orders in alphabetical symbol order with no fill-time cash check (all 89 orders ever placed filled; zero rejections). The gate + sells-first landed in 32e66cf (07-16) and first run in prod 07-20.
- 7 dust positions ($1–$106: KLAC, CAT, CSCO, MU, DIS, T, TMO) exist because `generate_orders` blocks residual sells below the 2% `min_rebalance_threshold`.
- The same threshold silently blocks sub-2% *new* entries.
- On portfolio-read failure the pipeline silently sizes against `nav = 1_000_000.0` with an **empty holdings map** — which would regenerate the entire book as buys.
- `risk_alerts` table, `RiskAlertLevel` enum, and `RiskAlertEvent` all exist but have **no writer**; the table has 0 rows ever.

## User decisions (2026-07-19)

1. **A3:** entries trade down to a **0.5%** target weight (new `min_entry_weight`); weight adjustments keep the 2% band; entries below 0.5% are skipped with a log line.
2. **A1:** invariant breach → **alert only** (CRITICAL `risk_alerts` row + ❌ digest line); the run stays `completed`.
3. **A2:** **no standalone sweep script** — the fixed sell-all semantics clears existing dust on the first Monday run after deployment.

## A1 — Post-run invariant checks

### Check module (pure logic)

New `app/modules/equities/risk_checks.py`:

```
evaluate_post_run_invariants(*, cash, nav, position_weights, portfolio_config,
                             branch_id, branch_name) -> list[RiskAlert]
```

Checks (module constants, not strategy config):

| Condition | Level | Metric | Threshold |
|---|---|---|---|
| `cash < 0` | CRITICAL | `cash` | 0.0 |
| `nav > 0 and cash / nav > 0.05` | WARNING (underinvested — the signature of mass buy rejections) | `cash_pct` | `CASH_PCT_WARN = 0.05` |
| any weight > `max_position_weight + 0.005` | CRITICAL, one alert per offending symbol (message names it) | `position_weight` | configured cap (tolerance constant `POSITION_WEIGHT_TOLERANCE = 0.005` absorbs fill slippage; the check runs right after rebalance, so drift is not a factor) |

The position check reads `PortfolioConfig.max_position_weight`, so when Phase-2 B2 tightens the cap the check tightens automatically.

### Persistence

- `RiskAlert` Pydantic domain model in `app/common/models/risk.py`, mirroring `RiskAlertModel` columns (level, source, metric, current_value, threshold, message, action_required, affected_branches, resolved, timestamps).
- Abstract `RiskAlertRepository` (create-only) in `app/common/interfaces/repositories.py`; `PostgresRiskAlertRepository` in `app/modules/portfolio/repository.py` — the first writer to the `risk_alerts` table. No migration needed.
- Each alert also appends the existing `RiskAlertEvent` to the event log (first use).

### Weekly-runner integration

- Runs where `PortfolioReport` is assembled (cash/positions/prices already loaded there), per branch, after trading is committed.
- `PortfolioReport` gains a `risk_alerts` field; the digest renders each as `❌ CRITICAL: …` / `⚠️ WARNING: …` lines. The existing hardcoded `⚠️ Negative cash balance` line is replaced by the alert-driven rendering.
- The whole check-persist-render step is wrapped in its own try/except (mirroring the attribution isolation pattern): a failure inside risk checks logs a warning, adds a `⚠️ risk checks failed to run` digest line, and never affects run status. Persistence uses a session that is independent of the trading transaction.
- Run status is never changed by alerts (user decision 2).

### Regression tests — the 06-22 sequence

New `tests/unit/test_trade_execution_sequence.py` (reusing the fixture pattern of `test_trade_execution_cash_check.py`), reconstructing the real 06-22 value-branch order set (cash -$499.75; SCHW held; buys ACN/BLK/CRM/DIS/T ≈ $368k; sell SCHW ≈ $132k):

1. **Alphabetical submission (historical behavior):** the four buys ahead of SCHW are rejected by the gate; the SCHW sell fills; cash never goes below its starting value. Proves the gate alone prevents 06-22.
2. **Sells-first submission (current behavior):** SCHW fills first; buys fill in order until cash is exhausted; the remainder reject; cash ≥ 0 throughout after the sell.
3. **Deleverage scenario (the 07-20 situation):** negative starting cash with a net-selling order set → all orders fill, final cash ≥ 0.

### Investor-reporting note

`scripts/build_report_json.py` gains a fund-level `"notes": [...]` array with one static entry (constant in code, so every rebuild carries it): period `2026-06-15 → 2026-07-20`, disclosing that execution could fill buys before sells, branches ran negative cash (value peak ≈ -24% of NAV, week of 06-22), fixed 2026-07-16, and returns in the window reflect >100% exposure. No restatement.

## A2 — Sell-all semantics for full exits

- `PortfolioManager.generate_orders` gains `current_quantities: dict[str, float] | None = None`.
- **Full exit rule:** when a held symbol has `target_weight == 0` (absent from targets or zero) and `current_quantities` contains it, emit a SELL for the **entire held quantity**, bypassing both thresholds. `reason="removed_position"`. Fractional dust can no longer be left behind by threshold or rounding.
- The price requirement stays: an unpriced symbol is still skipped (and remains visible via the digest's `unpriced` warning) — no order is submitted that execution predictably cannot price.
- When `current_quantities` is not provided (legacy callers/tests), behavior is unchanged (delta-based sizing with thresholds).
- **Plumbing:** the service's portfolio-read block builds `current_quantities[sym] = float(pos.long_quantity)` alongside the weights map; it flows through graph deps into `generate_orders`. The backtest inherits automatically (same service/graph path).
- **Float/Decimal edge:** SELL validation rejects only if `quantity > held + 1e-6`, and a full-exit fill is clamped to the exact held quantity, so `handle_trade_executed` brings the position to exactly zero and `delete_if_flat` removes the row. A unit test asserts a full-exit trade leaves no residual position.

## A3 — Entry threshold

- New `PortfolioConfig.min_entry_weight: float = 0.005`.
- In `generate_orders`: `threshold = min_entry_weight if current_weight == 0.0 else min_rebalance_threshold`.
- A new entry skipped because `0 < target_weight < min_entry_weight` logs at INFO with symbol and weight.

## A4 — NAV hard error

In `EquitiesBranchService.run_pipeline`'s portfolio-read block, all silent fallbacks become a `RuntimeError` with a clear message (original exception chained where applicable):

- portfolio service not provided,
- portfolio row not found for the branch,
- portfolio read raises,
- `nav <= 0`.

A genuinely empty book (successful read, zero positions, positive NAV/cash) remains valid — that is a first run, not a failure. In `graph.py`, `deps.get("nav", 1_000_000.0)` becomes a required key (missing → clear error). The weekly runner already records a raised pipeline error as a `failed` run — that is the intended behavior. Backtests are unaffected (context always injects a seeded portfolio service). Any unit tests relying on the silent defaults are updated to expect the error or to provide a portfolio service.

## Testing

TDD throughout. New/updated unit tests:

- `risk_checks`: each invariant, boundary values, tolerance behavior, no-alerts-when-clean.
- Repository: `PostgresRiskAlertRepository.create` (in-memory-style unit test consistent with existing repo test conventions).
- Weekly runner: alerts persisted + digest lines rendered + isolation (risk-check failure doesn't fail the run).
- `generate_orders`: full exit emits full quantity regardless of thresholds; unpriced full exit skipped; legacy no-quantities behavior unchanged; entry threshold at 0.5% (entry at 0.4% skipped + logged, 0.6% trades; adjustment at 1.9% still skipped).
- Execution sequence regressions (3 scenarios above) + full-exit-leaves-no-residual.
- `run_pipeline` NAV hard-error paths; graph required-nav.
- `build_report_json`: notes array present and stable.

Done = full unit suite green + `ruff check` clean.

## Non-goals

- No sizing/selection/ranker changes, no sector caps (Phase 2 — B1/B2).
- No notifications/escalation (Phase 0 — D1).
- No DB migration; no changes to broker fill pricing.
- No standalone dust-sweep script (user decision 3).

## Deployment note

Everything lands on the session worktree branch; nothing ships until merged/pushed. Recommendation: let the 07-20 run go with what is already deployed (four simultaneous debuts is enough) and merge this for the 07-27 cycle. First run after deployment sweeps the existing dust positions.
