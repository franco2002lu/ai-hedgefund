# AI Hedge Fund — Improvement Roadmap

**Date:** 2026-07-19 (Sunday, evening before the 07-20 run)
**Inputs:** production Neon DB (NAV series, trades, orders, attribution ICs, pipeline runs), full code survey of `equities/`, `backtest/`, `trade_execution/`, ops surface (workflows, scripts, data platform), and test/dependency audit.

---

## 1. Where the fund actually stands (evidence)

Live paper trading began 2026-06-15 with $1M per branch.

| | Growth | Value |
|---|---|---|
| NAV (07-17 EOD) | $1,009,641 (+0.96%) | $1,028,441 (+2.84%) |
| Cash | **-$55,473** | -$2,592 |
| Positions | 24 (4 are dust ≤$55) | 14 (3 are dust ≤$106) |
| Realized / unrealized P&L | +$2,126 / +$7,515 | +$15,088 / +$13,352 |

**Attribution, 9 decision weeks (05-11 → 07-13):**

| | Growth | Value |
|---|---|---|
| Conviction-weighted basket | **+8.04%** | **+5.58%** |
| Equal-weighted basket (same picks) | +9.01% | +6.69% |
| Benchmark (VOOG/VOOV top-50) | -0.59% | +3.28% |
| Fundamentals IC (mean, t) | +0.070 (t=0.92) | +0.127 (t=1.48) |
| News IC | -0.008 (t=-0.11) | -0.026 (t=-0.15) |
| Technical IC | -0.059 (t=-0.52) | **-0.184 (t=-1.64)**, 2/9 wks positive |

Three data-backed conclusions (with the usual small-sample caveat — 9 weeks, 8–20 name cross-sections):

1. **Stock selection is adding real value** — both baskets beat their benchmarks by wide margins (+8.6pp growth, +2.3pp value over 9 weeks).
2. **Conviction sizing is subtracting value** — equal-weighting the *same picks* beat conviction-weighting in both branches (~-10 to -12bp/week drag).
3. **Fundamentals is the only analyst with a positive IC.** News is ~zero (per-ticker news, shipping 07-20, is the designed fix). Technical is negative, persistently so on value.

---

## 2. Monday 2026-07-20 is a four-way debut — watch it

Tomorrow's run is the first to exercise, simultaneously:

1. **Adaptive analyst weights** (first live resolution; 1364a02).
2. **Per-ticker news** (c0c1f27 — observed on origin/main via `git ls-remote` 07-19; the planned 08-03 solo cutover is overtaken).
3. **Fill-time cash gate** (32e66cf, 07-16 — has *never* run in prod; all 89 historical orders filled, zero rejections).
4. **Sells-first execution** (same commit — historical fills were alphabetical: on 06-22 four value buys totaling $368k filled *before* the $132k SCHW sell).

Book state going in: growth cash **-$55,473**. Sizing diffs against NAV with targets summing to 99%, and sells sort first, so the run should *deleverage* (~5.5% net selling) rather than mass-reject — but any sell that fails validation (dust quantities, missing price) flips buys into rejections against the new gate.

**Action items for Monday:** watch the run; afterwards verify (a) cash ∈ [0, ~1.5% NAV], (b) zero rejected orders (or understand each), (c) `analyst_weights` on the decision row show the adaptive tilt, (d) news signals cite company-scope articles. Attribute any behavior change carefully across the four debuts.

---

## 3. Theme A — P&L integrity (correctness before tuning)

The performance numbers above carry known distortions. Fix these first; they're cheap.

**A1. Negative cash / unintended leverage — close it out.**
Value ran **-$236,553 cash (~24% leverage) for the week of 06-22**; growth is still -$55k today. Long-only branches earned returns on >100% exposure, so branch returns are modestly flattered/distorted. The 07-16 gate + sells-first fix the mechanism; remaining work:
- Post-run invariant check: cash within [0, buffer+ε] and no position > cap → write a `risk_alerts` row (the table has **zero rows ever** — it's dead schema today) and a ❌ in the digest.
- Regression test reproducing the 06-22 sequence (buys filling before sells into negative cash).
- One-line note in investor reporting for the levered weeks (06-15 → 07-20) rather than restating.

**A2. Dust positions + sell-all semantics.**
7 dust rows ($1–$106: KLAC, CAT, CSCO, MU / DIS, T, TMO) exist because `generate_orders` sizes sells as `delta × NAV / price` and the 2% `min_rebalance_threshold` blocks residual sells (`portfolio_manager.py:173`). Fix at the source: when `target_weight == 0`, sell the **entire held quantity**, bypassing the threshold. Then a one-time dust sweep. This also un-pollutes `position_count` and attribution's `n_holdings`.

**A3. Sub-2% *new* positions are silently never opened** (same threshold). Decide if intended; at minimum log skipped entries.

**A4. Hardcoded NAV fallback.** On portfolio-read failure the pipeline sizes against a fictional `nav = 1_000_000.0` (`service.py:159-173`). Replace with a hard error — trading against a made-up NAV is strictly worse than failing loudly.

---

## 4. Theme B — Profitability levers (ranked by evidence × effort)

**B1. Sizing: move to equal weight (or heavily softened conviction). Strongest data-backed change available.**
`conviction = composite_score × composite_confidence` (`portfolio_manager.py:49`) drives weights, but only the score is rank-normalized — confidence stays raw LLM output, so an over-confident analyst quadratically inflates weights. The live data says this sizing *loses* ~10-12bp/wk vs equal-weighting the same picks, in both branches. Plan: run an equal-weight **shadow portfolio** (C4) for 2–3 weeks to confirm live, then flip. Effort: S.

**B2. Risk limits that actually bite.**
Current: `max_position_weight = 0.50` (decorative — BAC is 16% of value NAV and "within cap"), **no sector caps**, and `min_holdings = 10` is dead config referenced nowhere (value holds as few as 8 names). Set name cap ~8–10%, add a sector cap (~30%), enforce or delete `min_holdings`. Effort: S–M.

**B3. Technical analyst: negative IC, especially value (-0.184, 2/9 weeks positive).**
Near term, let adaptive weights tilt it down — but the floor is 0.10, which force-feeds 10% weight to a negative-IC signal; consider floor → 0.05 or 0. Medium term, rework the prompt (value names likely mean-revert; the current momentum framing may be backwards for that branch). Re-evaluate with 4–6 more weeks of ICs before surgery. Effort: S (floor) / M (prompt work).

**B4. Un-degrade prod news (compounds tomorrow's per-ticker launch).**
The weekly runner calls `run_pipeline` with **no `as_of_date`** (`scripts/run_weekly_pipeline.py:139`), so the news prefetch runs `since=None` — **no recency window is enforced** and the curated/manual-news path **never loads in production** (`graph.py:56,94`), while the prompt tells the model it's seeing "trailing ~1 week". Pass the run date as `as_of_date`. Effort: S.

**B5. LLM truncation → silent neutral signals.**
Analyst calls cap at `max_tokens=512` while prompts demand step-by-step reasoning; a truncated response fails JSON parse and silently becomes `5/5` (`llm_client.py:86,102-107`). Raise the cap or strip the CoT instruction, and (C3) count parse failures per run. Effort: S.

**B6. Value branch breadth.**
Only 13–15 of 50 names pass the value screen → 8–14 holdings → noisy ICs and concentration. Widen VOOV `top_n` to ~100 for value (costs ~50 extra Yahoo calls), revisit `DividendYieldFilter` (excludes all non-payers — confirm that tilt is intended) and fix `FCFYieldFilter` config 0.0 (a no-op as configured). Effort: S–M.

**B7. Turnover control.**
Value traded **$831k gross on a ~$1M book on 07-13** (~42% one-sided). Structural churn source: the cross-sectional ranker rewrites scores as forced deciles weekly, so a name's weight moves whenever *peers* move; there's no cost model in decisions. Options: smooth scores (EW-average 2 weeks before ranking), trade bands around targets, a weekly turnover budget, and show realized slippage cost in the digest. Effort: M.

**B8. Analyst input blind spots** (from prompt audit): fundamentals prompt references sector comparisons but `sector_medians` is never passed; no earnings-date awareness anywhere (trades blindly into earnings weeks); technical sees no relative strength vs sector. Effort: M, incremental.

---

## 5. Theme C — Measurement before tuning

**C1. Fix analytics bugs before quoting metrics.**
- Turnover **double-counts** (buy+sell notional summed) → ~2× overstated (`analytics.py:120-124`).
- Win rate / profit factor pair `buys[i]` with `sells[i]` using buy quantity regardless of sell size — wrong under scale-ins/partial exits (`analytics.py:174-208`).
- Benchmark comparison zips daily returns **by index, not date** — alpha/beta misaligned when trading-day sets differ (`analytics.py:238-241`).
- Sharpe/Sortino/Calmar silently clamped to ±10, masking artifacts.
Effort: S.

**C2. Backtest ≠ production strategy — close the gap or label it.**
The default backtest runs **quantitative analysts** (not LLMs), **always static weights** (adaptive resolution requires a DB session; the engine passes `session=None` — the live fund's feedback loop is unsimulatable today), **zero commissions**, **no market/sector/company news**, and in LLM mode a **60-call/rebalance cap** that rations coverage in non-deterministic async order on a cold cache. Actions: make adaptive weights simulatable (inject an attribution-history source), set a nonzero commission default, parameterize the call cap for gates, and print a fidelity-caveats banner on every backtest report. Effort: M–L.

**C3. Signal-quality observability in the digest.**
Today an Anthropic outage produces a *completed* run trading on all-neutral signals (per-symbol try/except → `5/5` fallback), and Yahoo 429s silently thin the screen (`except: continue`, no logging). Add per-run counters: parse failures, neutral fallbacks, unpriced positions, screened-count vs universe, post-run cash. **Hard-abort the run if neutral-fallback rate > ~30%** — keeping last week's book beats trading on garbage. Effort: S–M.

**C4. Shadow portfolios — cheap live counterfactuals.**
From the same weekly picks/scores, book two paper shadow NAV series (no extra LLM cost): equal-weight sizing, and no-technical composite. These de-risk B1/B3 with live evidence in 2–3 weeks — stronger than backtests given C2's fidelity gaps. Effort: M.

**C5. Attribution robustness.** Add a 4-week horizon IC alongside 1-week; report stderr/CI in the digest; pool branches for analyst-level tests; guard the IC series against silent gaps (a Yahoo outage currently just skips the week, quietly starving the adaptive-weights input).

---

## 6. Theme D — Robustness / ops

**D1. Alerting — the single biggest ops gap: there is none.**
No Slack/email/issue-creation/webhook anywhere; failure surfaces are pull-only (Actions tab, digest ❌). A failed or stuck Monday run goes unnoticed until someone looks. Cheapest fix: a workflow failure step that opens a GitHub issue (or ntfy/Slack webhook), plus a scheduled staleness check that alerts on stuck-`running` `pipeline_runs` rows and on "no snapshot in 2 business days". Effort: S.

**D2. Yahoo resilience.** No retry/backoff/429-handling in the adapter; every fetch error is swallowed (`except Exception: continue` with no logging); cache is per-process and cold every GH run; no fallback source. Add bounded retries with backoff, log+count failures (feeds C3), and consider a secondary price source for marks. Effort: M.

**D3. Fail-loud guardrails.** Abort before trading when neutral-fallback rate is high (C3); add `ANTHROPIC_API_KEY` to `.env.example` (it's missing — a rotated key currently yields a "successful" garbage run).

**D4. Idempotency hardening.** Same-day dedupe and snapshot dedupe are app-level only — add DB unique constraints on `pipeline_runs(branch_id, run_date)` and `portfolio_snapshots(branch_id, NY-day)`; ship a `scripts/unstick_run.py` (recovery today is raw SQL); document that `force_retry` after a post-commit kill re-trades (attempt N+1 re-runs the full pipeline against the already-rebalanced book). Effort: S–M.

**D5. Universe staleness.** Latest N-PORT snapshots are **2025-11-30 — ~8 months old** — the fund is picking from a stale index membership (misses reconstitution). Refresh via `seed_nport_snapshots`, and add a digest warning when the snapshot in use is >120 days old. Effort: S.

**D6. Reproducible builds.** All deps are `>=`-only with **no lockfile**; `yfinance` (the notoriously breaking one) can take down a Monday run with zero code change. Add upper bounds + a committed lockfile. Effort: S.

---

## 7. Theme E — Maintainability

The codebase is in good shape overall (1,180 unit tests, zero TODO/FIXME anywhere, clean DI composition root, disciplined config). Remaining debt is concentrated:

- **E1.** Fill logic implemented twice (`paper.py` vs `backtest_broker.py`) and NAV math twice (`engine.py:110-128` vs `portfolio/service.py:114`) — exactly the surfaces live/backtest parity depends on. Single-source them. (M)
- **E2.** All five `Postgres*Repository` classes have zero direct unit tests (in-memory twins are tested; the SQL paths aren't). (M)
- **E3.** `context.build()` is 273 lines; `_run_simulation()` 160; the metrics-unwrap boilerplate is copy-pasted at 8 sites — extract a helper. (M)
- **E4.** Hygiene batch: CLAUDE.md says 14 tables (there are 20); `_build_screener` documented in the wrong file; empty `app/central_orchestrator/` package; dead config (`min_holdings`, unused `portfolio_manager` LLM config, misleading `alembic.ini` URL). (S)
- **E5.** `xbrl_mapping.py` and `historical_fundamentals.py` — the point-in-time core of backtesting — have no dedicated tests. (M)

---

## 8. Sequencing

**Phase 0 — this week (around the 07-20 run):**
Watch the four-way debut (§2). Then: A1 cash invariant + first real `risk_alerts` writer, D1 minimal alerting, D5 universe refresh, D6 lockfile, D3 `.env.example`. All small, all de-risking.

**Phase 1 — weeks of 07-27 / 08-03 (trust the numbers):**
A2 sell-all + dust sweep, B4 `as_of_date` in prod, B5 max_tokens + parse telemetry, C1 analytics fixes, C3 digest counters + abort-on-garbage, D4 unstick script + constraints.

**Phase 2 — August (returns):**
C4 shadow portfolios → decide B1 (sizing) and feed B3 (technical) with live evidence; B2 risk caps; B6 value breadth; B7 turnover controls. One live change per weekly cycle — tomorrow's accidental four-way debut is the counterexample to avoid repeating.

**Phase 3 — September+ (structure):**
C2 backtest parity (adaptive weights simulatable, cost realism, call-cap params), C5 attribution horizons/CIs, E1–E5 maintainability batch, B8 analyst input upgrades.

**Standing discipline:** each live change ships alone in its own weekly cycle, with the experiment-gate machinery (bundles + noise floor + t-corrected verdicts) for prompt-shaped changes and shadow portfolios for construction changes.

---

## 9. Priority matrix (tech-debt scoring: (Impact+Risk) × (6−Effort), 1–5 scales)

| Item | Impact | Risk | Effort | Score |
|---|---|---|---|---|
| D1 Alerting | 5 | 5 | 1 | 50 |
| A1 Cash invariant + risk_alerts | 4 | 5 | 1 | 45 |
| C3/D3 Abort-on-garbage guardrail | 4 | 4 | 1 | 40 |
| D6 Lockfile + dep caps | 3 | 4 | 1 | 35 |
| D5 Universe refresh + staleness warn | 3 | 3 | 1 | 30 |
| B5 max_tokens + parse telemetry | 3 | 3 | 1 | 30 |
| B4 as_of_date in prod news | 3 | 2 | 1 | 25 |
| A2 Sell-all semantics + dust sweep | 3 | 2 | 1 | 25 |
| C1 Analytics metric fixes | 3 | 3 | 2 | 24 |
| B1 Equal-weight sizing (after shadow) | 4 | 2 | 2 | 24 |
| B2 Position/sector caps + min_holdings | 3 | 3 | 2 | 24 |
| D4 Idempotency constraints + unstick | 3 | 3 | 2 | 24 |
| D2 Yahoo retries/logging | 3 | 4 | 3 | 21 |
| E1 Single-source fill/NAV logic | 2 | 3 | 2 | 20 |
| C4 Shadow portfolios | 4 | 1 | 3 | 15 |
| B7 Turnover controls | 3 | 2 | 3 | 15 |
| C2 Backtest parity | 4 | 3 | 4 | 14 |
