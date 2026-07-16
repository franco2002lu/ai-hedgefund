# Adaptive Analyst Weights — Closing the Attribution Feedback Loop

**Date:** 2026-07-16
**Status:** Approved
**Predecessor:** `2026-06-10-attribution-weights-ranking-design.md` (Phase D built the
measurement; this spec makes the fund act on it)

## Motivation

Per-analyst weekly rank-ICs have been computed into `attribution_reports.analyst_ics`
since 2026-06 (9 weekly reports per branch as of 2026-07-16, backfilled to the
2026-05-11 decision, zero nulls) but are never read back. Composite weights are static
0.60 fundamentals / 0.20 news / 0.20 technical (`AgentsConfig`), set once from a 5-week
pooled IC snapshot that predates the ranking redesign.

Exponentially-weighted rolling ICs on the live history (half-life 4 weeks, window 12):

| branch | fundamentals | news | technical |
|---|---|---|---|
| growth | **+0.09** | +0.00 | −0.08 |
| value  | **+0.12** | −0.01 | **−0.17** |

Fundamentals is persistently the strongest signal; value-technical's rolling IC has
been ≤ 0 at every weekly evaluation since it became estimable. The weights should follow the
measurement — gradually, boundedly, reversibly — and the adaptation itself must be
measurable afterward.

## Decisions made with the user (2026-07-16)

- **Mapping = multiplicative tilt on the static prior** (chosen over softmax-over-ICs
  and proportional-to-positive-IC). Softmax has a uniform attractor: with noisy
  near-zero EWICs it drifts every branch toward 1/3-each, re-litigating the static
  prior without evidence. The tilt nests current behavior exactly — EWIC = 0 for all
  analysts reproduces static weights — so any deviation from static is evidence-driven
  by construction, which is the cleanest thing to attribute.
- **Attribution moves ahead of the trading run** in the weekly CLI. Today it runs
  after, so at pipeline start the freshest IC row scores the decision from two Mondays
  ago. Reordering makes this week's weights see last week's decision scored through
  this morning, and the IC series keeps accruing even when the trading run fails.
  Which decision gets scored is unchanged (the engine selects `decided_at <` midnight
  of `as_of`; today's decision is made after that either way).
- **Per-branch adaptation** (growth and value adapt independently — attribution is
  already per-branch), static config as the shared prior and fallback.
- **No experiment-harness gate for this item.** Quant-mode backtests have a different
  IC structure than the LLM analysts and the backtest computes no attribution, so
  there is nothing cheap that simulates this policy faithfully. Validation is: unit
  tests, a replay of the policy over the real 9-week IC history pinned as a fixture,
  and a read-only preview script run against prod before merging.
- Ships as the **single behavior change** for the Monday 2026-07-20 cycle.

## Design

### Algorithm (pure functions in `app/modules/equities/adaptive_weights.py`)

Inputs per branch: the trailing `attribution_reports` rows (decision_date descending,
up to `lookback_weeks`), the previous week's used weights, and `AgentsConfig`.

1. **EW rolling IC per analyst.** Over the window's reports (freshest = lag 0), the
   exponentially-weighted mean of non-null ICs with decay λ = 0.5^(1/half_life_weeks);
   null ICs are skipped in both numerator and denominator (they don't count as zero).
   `valid_weeks(analyst)` = count of non-null ICs in the window.
2. **History gate.** If `min(valid_weeks over the three analysts) <
   min_history_weeks`, return static weights (`mode="static"`,
   `reason="insufficient_history"`). No partial adaptation — mixing one adapted and
   two static components then renormalizing distorts all three.
3. **Target weights.** `target_i ∝ static_i × exp(EWIC_i / ic_tilt_scale)`,
   normalized to 1.0.
4. **Rails (bounded projection).** Let `prev` be last week's used weights. Bounds:
   `lo_i = max(weight_floor, prev_i − max_weekly_shift)`,
   `hi_i = prev_i + max_weekly_shift`. Clip the target into `[lo, hi]`, then restore
   `Σ = 1.0` in one exact pass: distribute any deficit proportionally to remaining
   headroom `(hi_i − w_i)`, or remove any surplus proportionally to slack
   `(w_i − lo_i)`. Slack-proportional redistribution cannot breach any individual
   bound (each adjustment ≤ that component's slack because deficit ≤ total headroom).
   Feasibility: since `Σ prev = 1`, the bounds always admit a solution — except when a
   config change (e.g. the floor was raised above a previous weight) makes them
   infeasible (`Σ lo > 1` or `Σ hi < 1`); then drop the shift bounds for that week,
   project onto `[weight_floor, 1]` with the same pass, and log a warning (one-time
   jump).
5. **Determinism.** Round to 6 decimals and add any residual to the largest component
   so the persisted weights sum to exactly 1.0.
6. **Alerts.** For each analyst with ≥ `min_history_weeks` valid ICs: recompute the
   EWIC at each trailing evaluation point (window ending at that report), counting
   only evaluation points whose window itself contains ≥ `min_history_weeks` valid
   ICs (an EWIC we would not have trusted for adaptation shouldn't count toward an
   alert either). The alert streak is the number of consecutive most-recent
   evaluations with EWIC ≤ 0; alert when streak ≥ `alert_streak_weeks`. On today's
   9-week history value-technical fires immediately (all 4 trustworthy evaluation
   points ≤ 0 → streak 4).

Result model `AnalystWeightsReport` (Pydantic, so it embeds in `RunResult`):
`weights`, `mode` (`"adaptive" | "static"`), `reason`
(`"ok" | "disabled" | "insufficient_history" | "no_session" | "error"`),
`ewics` (per analyst, None when unavailable), `valid_weeks` (per analyst), `alerts`
(list of `{analyst, streak, ewic}`).

**Previous-weights anchor:** the `analyst_weights` JSONB of the branch's most recent
`portfolio_decisions` row; static weights when the column is null (pre-migration rows)
or no decision exists. Static-mode weeks persist their (static) weights too, so the
anchor is always "what actually ran last week."

Note the floor protects decision robustness only — IC measurement is unaffected by
weights because all three analysts score every stock regardless (`agent_signals` is
weight-independent), so a floored analyst keeps producing a full IC series and can
earn its weight back.

### Worked example (real history through 2026-07-13's report)

Tilt targets at τ = 0.40: growth 0.675 / 0.179 / 0.146, value 0.712 / 0.172 / 0.116.
First adaptive week moves at most ±0.05 per component from 0.60 / 0.20 / 0.20, so the
first live weights land near 0.65 / 0.19 / 0.16 (growth) — exact values depend on the
report the Monday run writes before trading. If EWICs collapse to 0, weights walk back
to static at the same ±0.05/week.

### Config (`AgentsConfig.adaptive: AdaptiveWeightsConfig`)

| field | default | notes |
|---|---|---|
| enabled | True | kill switch (one-line commit to disable) |
| lookback_weeks | 12 | reports loaded per branch |
| half_life_weeks | 4.0 | EW decay; λ = 0.5^(1/hl) |
| min_history_weeks | 6 | per-analyst valid-IC count gate |
| weight_floor | 0.10 | validator: 3 × floor ≤ 1 |
| max_weekly_shift | 0.05 | per component, vs last used weights |
| ic_tilt_scale | 0.40 | τ; smaller = more aggressive |
| alert_streak_weeks | 4 | digest alert threshold |

Existing `weight_fundamentals/news/technical` stay as the static prior and fallback.
Validators: all positive; `lookback_weeks ≥ min_history_weeks ≥ 1`;
`0 < max_weekly_shift ≤ 1`; `0 < weight_floor` and `3 × weight_floor ≤ 1`.

### Wiring

- **`EquitiesBranchService.run_pipeline`** — before constructing `PortfolioManager`:
  if `session` is present and `adaptive.enabled`, call
  `load_analyst_weights(session, branch_id, config.agents)` (module-level async
  orchestrator: two selects — trailing reports, previous decision — then the pure
  pipeline). Any exception → static fallback with `reason="error"` and a warning;
  **weight loading must never block trading**. `session=None` (all backtests) →
  static, `reason="no_session"` — quant and LLM backtests keep exactly today's
  behavior, and the LLM cache keying is untouched (weights act after analyst calls).
- **`PortfolioManager`** gains optional `analyst_weights: dict[str, float] | None`;
  `compute_composite_scores` uses it, defaulting to the config statics. Graph code
  unchanged.
- **Persistence:** `_persist_run_artifacts` writes the used weights on the decision
  row — new nullable JSONB column `analyst_weights` on `portfolio_decisions`
  (alembic migration, `down_revision="b91f2a6c3d44"`), shape:
  `{"weights": {...}, "mode": ..., "reason": ..., "ewics": {...}, "valid_weeks": {...}}`.
  The weekly workflow already runs `alembic upgrade head` before the pipeline, so the
  migration auto-applies on Monday.
- **`RunResult`** gains `analyst_weights_report: AnalystWeightsReport | None`;
  `WeeklyRunner.execute` copies it onto `WeeklyRunSummary`.
- **Weekly CLI (`scripts/run_weekly_pipeline.py`):** the attribution block moves
  ahead of `runner.execute` (own session, still try/except-non-fatal, still upserts
  idempotently); the report attaches to the summary after the run returns. Attribution
  now also persists when the trading run subsequently fails or aborts.
- **Composite IC (measurement only, no behavior change):** the attribution engine
  additionally computes a `"composite"` rank IC — Spearman of the decision's
  conviction (`composite_scores[sym].score × confidence`) vs forward returns, same
  window and ≥ 5-sample rule — stored under `analyst_ics["composite"]`. This is the
  baseline series for judging whether adaptive weights improve the blend. The weights
  loader reads only the three analyst keys, never `"composite"`.

### Digest

Per completed branch, after the attribution line:

```
- Weights: fund 0.65 / news 0.19 / tech 0.16 (adaptive, 9 wks; EWIC fund +0.09 / news +0.00 / tech −0.08)
- ⚠️ technical rolling IC ≤ 0 for 4 consecutive weeks (EWIC −0.17)
```

Static fallback renders the reason:
`- Weights: fund 0.60 / news 0.20 / tech 0.20 (static — insufficient_history, 4 wks < 6)`.

### Preview script

`scripts/preview_adaptive_weights.py` — read-only CLI (same env/DB conventions as the
other scripts): per branch, prints the trailing IC table, EWICs, valid weeks, alert
streaks, previous weights, and the weights the next run would use. Run against Neon
before merging to sanity-check Monday's move.

### Evaluating the adaptation (after ship)

- The decision row stores the used weights next to the signals that produced them, so
  the counterfactual (static-weight composite on the same signals) is recomputable
  offline at any time.
- The new `analyst_ics["composite"]` series is the weekly headline: if adaptation
  helps, composite IC should trend above what static weights would have produced.
- Digest EWIC lines + alert streaks give the week-to-week narrative.

### Out of scope (deliberately)

Per-analyst enable/disable, negative weights, IC significance testing,
sample-size-weighted ICs (`n_holdings_priced`), feeding ICs into selection or sizing
beyond the composite, adapting `conviction = score × confidence` itself (work item 5).

## Testing

Unit (all offline, mock sessions per `test_attribution_engine.py` conventions):

- `ewma`: known-answer, null-skipping, empty/all-null → None, half-life semantics.
- Tilt mapping: zero EWICs → exactly static; known-answer tilts; normalization.
- Projection: bounds respected, Σ = 1 exact, deficit and surplus paths, floor binding,
  infeasible bounds → floor-only projection, output determinism (rounding).
- History gate: any analyst below `min_history_weeks` → static fallback.
- Anchor: previous weights read from last decision row; null column → static.
- Alert streaks: known series (incl. the streak-6 value-technical case), gated below
  min history.
- Loader: `enabled=False`, `session=None`, query exception → correct static reasons.
- Replay fixture: the real 9-week IC history pinned; asserted weight trajectory
  (guards against silent policy drift).
- `PortfolioManager`: override respected, default unchanged (existing tests green).
- Persist artifacts: `analyst_weights` JSONB written with mode/reason.
- Runner/digest: summary carries the report; digest renders adaptive, static-reason,
  and alert lines; CLI ordering (attribution precedes trading; still non-fatal).
- Attribution: `"composite"` IC computed from composite_scores, ≥ 5-sample rule, and
  absent when composite_scores is empty.
- Config validators.

All 1098 existing unit tests plus ruff stay green (`.venv/bin/pytest tests/unit -q`).

## Rollout

1. Stage for user review (no commits by Claude); user commits and pushes to main.
2. Monday 2026-07-20 run: migration auto-applies; attribution (now first) writes the
   2026-07-13 report; weights adapt from 10 weeks of history; digest shows the move
   and the value-technical alert.
3. Kill switch: `adaptive.enabled = False`.
4. Review live attribution for 1–2 weeks before starting work item 2 (per-ticker
   news).

## Changelog

- **2026-07-16** — Spec approved (mapping + reordering decisions made with user).
- **2026-07-16** — Implemented and staged for user review (plan:
  `docs/superpowers/plans/2026-07-16-adaptive-analyst-weights.md`). Ships in the
  2026-07-20 cycle; migration `c4d2a91b7e55` auto-applies via the weekly workflow.
