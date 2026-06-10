# Performance Improvement Roadmap: Attribution, Composite Weights, Cross-Sectional Ranking

**Date:** 2026-06-10
**Status:** Approved
**Phases:** D (measurement) → A (weights + hysteresis) → B (relative ranking)

## Motivation

Five weeks of production pipeline runs (2026-05-11 → 2026-06-08) produced decisions and
signals but no executed trades (see commit d5123bb for the execution fixes). Offline
reconstruction of those decisions showed:

- Hypothetical conviction-weighted baskets beat benchmarks: growth +3.19% vs VOOG −1.39%,
  value +4.93% vs VOOV +0.97% (4 of 5 weeks each).
- Per-analyst rank-IC (bullish_score vs forward 1-week return, n=160 pooled):
  fundamentals **+0.035** (value branch +0.109), news **−0.203**, technical **−0.185**.
- The composite formula weights the two anti-predictive signals at 60% combined
  (news 0.35 + technical 0.25) and the predictive one at 40%.
- News scores cluster in 4–7 with mean confidence 3.6 — minimal cross-sectional
  discrimination.

Decisions made with the user:
- Weight changes ship directly and are judged by live weekly attribution (no
  backtest-gating; avoids LLM API costs of the Phase 3 experiment harness).
- Scoring redesign is the **full relative-ranking** option, not light prompt edits.

Out of scope (parked): sector caps, external news data feeds, central orchestrator /
risk module, backtest-gated experiments.

---

## Phase 1 — D: Weekly Attribution Engine

### Purpose

Every weekly run scores *last week's* decision now that a week of prices exists, so the
fund accumulates a continuous record of basket-vs-benchmark performance and per-analyst
signal quality. All later changes (Phases 2 and 3) are evaluated against this record.

### Component: `app/modules/equities/attribution.py`

`AttributionEngine` with one public entry point:

```python
async def compute_and_persist(self, session, *, branch_id: str, branch_name: str,
                              as_of: date) -> AttributionReport | None
```

Steps:
1. Find the most recent `portfolio_decisions` row for the branch with
   `decided_at::date < as_of` (typically the prior Monday). Return None if none exists
   or if the gap exceeds 14 days (stale).
2. Resolve holdings + weights:
   - Preferred: `target_holdings` (real weights as of commit d5123bb).
   - Fallback (historical rows where weights are all zero): reconstruct
     conviction weights from `composite_scores` — weight ∝ score × confidence over the
     symbols present in `orders_generated` buy orders, normalized.
3. Fetch closes for holdings + benchmark + SPY from decision date to `as_of` via
   `DataPlatformService.get_prices`. Benchmark map: growth → VOOG, value → VOOV.
4. Compute:
   - `basket_return_conviction`: Σ wᵢ·rᵢ (renormalized over symbols with prices).
   - `basket_return_equal`: mean of rᵢ — the standing shadow A/B for position sizing.
   - `benchmark_return`, `spy_return` over the same window.
   - `analyst_ics`: for each analyst type, Spearman rank correlation between
     `agent_signals.bullish_score` (for the decision's screening run) and each symbol's
     forward return over the window. Requires ≥ 5 scored symbols with prices; else null.
5. Upsert the row (keyed branch_id + decision_date) and return a frozen
   `AttributionReport` dataclass mirroring the table below.

Implementation notes:
- Spearman via rank-then-Pearson on plain floats (no scipy dependency).
- Symbols missing price data are dropped and counted in `n_holdings_priced`;
  if holdings exist but none priced (full price-source outage), the engine
  refuses to persist and returns None rather than recording zeros.
- The pure math (`compute_report`) performs no writes; the engine owns the
  upsert (as-built: `compute_and_persist`).

### Table: `attribution_reports` (alembic migration)

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| branch_id | UUID FK branches.id | indexed with decision_date (unique together) |
| branch_name | str | |
| decision_date | date | the decision being scored |
| as_of_date | date | when it was scored |
| basket_return_conviction | numeric(10,6) | |
| basket_return_equal | numeric(10,6) | |
| benchmark_return | numeric(10,6) | |
| benchmark_symbol | str | VOOG / VOOV |
| spy_return | numeric(10,6) | |
| analyst_ics | JSONB | {"news": -0.18, "fundamentals": 0.11, "technical": null} |
| n_holdings | int | holdings in the decision |
| n_holdings_priced | int | holdings with usable prices |
| created_at | timestamptz | |

Upsert on (branch_id, decision_date): re-running a week overwrites rather than
duplicates.

### Integration: `WeeklyRunner` + digest

- After a branch's run finishes with status completed or skipped, the weekly CLI
  (`scripts/run_weekly_pipeline.py`, not the runner class) calls the attribution
  engine inside a SAVEPOINT and attaches the report to the summary. A failed run
  raises out before attribution (its session is rolling back anyway). Errors are
  caught and logged as warnings — **attribution must never fail or block the
  trading run**.
- `render_digest` gains an attribution section per branch:
  `Last week (2026-06-01): basket +1.2% (eq-wt +0.9%) vs VOOG +0.4%, SPY −0.3% · IC fund +0.11 / news −0.18 / tech −0.21`
- `WeeklyRunSummary` gains an optional `attribution: AttributionReport | None` field.

### Backfill

`scripts/backfill_attribution.py`: iterates all historical `portfolio_decisions`
(2026-05-11 onward), computes and upserts reports. Run once against Neon after deploy.

### Testing

Unit tests with stubbed price data: weight reconstruction fallback, both basket
calculations, IC math (known-answer test), stale-decision and missing-price edge cases,
runner integration (attribution failure does not fail the run).

---

## Phase 2 — A: Composite Weights + Holding Hysteresis

### Weight change (`AgentsConfig` defaults)

| analyst | old | new |
|---|---|---|
| fundamentals | 0.40 | **0.60** |
| news | 0.35 | **0.20** |
| technical | 0.25 | **0.20** |

Rationale: pooled ICs above. The weekly attribution report (Phase 1) is the standing
evaluation; if fundamentals' edge decays or news improves (e.g. after Phase 3), weights
get revisited with data.

### Holding hysteresis (`PortfolioManager.select_stocks`)

New signature: `select_stocks(scores, current_holdings: set[str] | None = None)`.

Selection rule:
1. Rank all scores ≥ `min_composite_score` by conviction (as today) → top
   `target_holdings` (20) are selected regardless of holding status.
2. Additionally keep any **currently held** symbol that (a) has composite score ≥
   `exit_score_threshold` and (b) ranks within `max_holdings` (30) by conviction —
   even if outside the top 20.
3. Result is capped at `max_holdings`; if over the cap, the lowest-conviction
   hysteresis keeps (rule 2) are dropped first.

New config: `PortfolioConfig.exit_score_threshold: float = 4.0`.

Wiring: the graph's `portfolio_decision` node passes
`set(deps["current_positions"].keys())` into `select_stocks`. Sizing and order
generation are unchanged (the 2% `min_rebalance_threshold` still suppresses tiny
adjustments).

Effect: a stock bouncing between rank 18 and rank 24 week to week no longer triggers a
full sell/rebuy cycle; only genuine score decay (< 4.0) or a fall below rank 30 exits.

### Testing

Unit tests: new weight defaults sum to 1.0; hysteresis keeps a held rank-25 stock with
score 5, drops a held stock with score 3.5, drops a held stock at rank 35, never exceeds
max_holdings, and behaves identically to today when `current_holdings` is empty.

---

## Phase 3 — B: Cross-Sectional Ranking (two-stage)

### Design

Per-stock scoring stays (stage 1). A new stage 2 forces each analyst to discriminate
across the screened set:

1. **Stage 1 (existing):** `analyze_batch` produces per-stock `StockSignal`s in
   parallel — thesis summary, provisional score, confidence.
2. **Stage 2 (new):** `CrossSectionalRanker` (`agents/ranker.py`) makes **one** LLM call
   per analyst per run: input is the list of (symbol, provisional score, thesis
   summary); the model must return a strict best-to-worst ranking of all symbols.
   Ranks map to forced deciles → final `bullish_score` (1–10):
   `score = 1 + floor((n−1−i) * 9 / (n−1))` where `i` is the 0-indexed rank position
   (best stock i=0 → 10, worst i=n−1 → 1, evenly spread for any n ≥ 2; n=1 → 10).
   Confidence and summary are preserved from stage 1.

Chosen over a single giant batch call (long-context quality risk, loses per-stock
summaries) and pairwise tournaments (O(n·log n) calls, much larger change surface).

Properties:
- `StockSignal`, `agent_signals` table, composite scoring, and the portfolio manager are
  **schema-unchanged**; `bullish_score` semantics become "cross-sectional decile".
- Forced ranking makes score clustering (news 4–7) structurally impossible.
- Graceful degradation: if the ranking call fails, returns invalid JSON, or covers
  less than 90% of the signal set, ALL symbols keep their stage-1 scores (logged
  warning) — a sparse partial ranking must not mix decile and stage-1 scales.
  With coverage ≥ 90%, the 1–2 omitted stragglers keep their stage-1 scores.
- The ranking call's max_tokens scales with universe size
  (`max(2048, 12·n + 128)`) so large universes can't silently truncate into
  permanent fallback.
- Cost: +3 LLM calls per run (one per analyst), negligible.

### Components

- `agents/ranker.py`: `CrossSectionalRanker(llm_client, analyst_type, branch_name)`
  with `async def rank(signals: list[StockSignal]) -> list[StockSignal]`. Skips ranking
  when n < `min_rank_universe` (default 5).
- `skills/base/ranking.md`: ranking prompt (role, decile discipline, output JSON:
  `{"ranking": ["SYM1", "SYM2", ...]}` best to worst). Branch overlays optional via the
  existing loader layering.
- `skills/output_format.md`: confidence redefined as "likelihood your directional call
  resolves correctly within ~1 month (1 = coin flip, 10 = near certain)".
- Graph wiring: each analyst node applies its ranker after `analyze_batch` (ranker
  injected via deps; absent ranker = current behavior, so backtests and tests that don't
  wire rankers are unaffected).

### Backtest parity

- Quantitative analysts: deterministic rank-normalization of their stage-1 scores to
  deciles (same formula), so quant-mode backtests share the new score semantics.
  Tied stage-1 scores share the rounded mean decile of their positions (alphabetical
  order is used only for stable iteration, never to spread scores) — otherwise the
  backtest would acquire a persistent alphabetical tilt that production lacks.
- LLM-mode backtests: ranking call flows through `LLMResponseCache` like any other call
  (prompt-keyed, reproducible). Implementation-time check: `CachedAnalystWrapper` must
  delegate through `analyze_batch` so stage 2 participates; if it caches per-symbol
  around stage 1 only, ranked scores must be what gets cached.

### Testing

Unit tests: decile mapping math (n=20, n=7, ties), missing/extra symbols in ranking
response fall back to stage-1 scores, ranking-call exception falls back wholesale,
ranker skipped under min universe, prompt loader picks up `ranking.md`, quant analyst
rank-normalization.

### Rollout

Ships after Phases 1–2 are live so the attribution report captures before/after IC for
each analyst. The before/after week is recorded in this doc's changelog when deployed.

---

## Sequencing & deliverables

| order | deliverable | risk |
|---|---|---|
| 1 | attribution module + table + runner/digest integration + backfill | none to trading path |
| 2 | weight defaults + hysteresis | low (config + selection logic, unit-tested) |
| 3 | ranker + prompts + graph wiring + backtest parity | medium (new LLM stage, graceful fallback) |

Each phase lands as its own commit(s) with unit tests; production deploys via push to
main (GitHub Actions picks it up the following Monday).

## Changelog

- **2026-06-10** — All three phases implemented (commits 38fda33..d7f300b). Migration
  applied to Neon and the 5 historical weeks backfilled into `attribution_reports`.
  First production run with new weights, hysteresis, and cross-sectional ranking:
  **Monday 2026-06-15** (Phase B before/after IC cutover week).
