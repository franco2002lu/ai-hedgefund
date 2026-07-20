# Per-Ticker News for the News Analyst — Design

- **Date:** 2026-07-17
- **Status:** Approved design (brainstormed with Franco 2026-07-17); implementation pending
- **Work item:** #2 of the decision-process improvement stream (item 1 = adaptive analyst
  weights, commit 1364a02, first live run 2026-07-20)
- **Related:** `2026-04-14-news-ingestion-redesign-design.md` (why per-ticker news was
  removed), `2026-06-10-attribution-weights-ranking-design.md` (forced ranking),
  `2026-07-16-adaptive-analyst-weights-design.md` (IC feedback loop)

## Context and motivation

The news analyst receives only market + sector + manual headlines. `_articles_for_stock`
(`app/modules/equities/agents/graph.py`) merges those scopes and discards provenance, so
**every stock in a sector receives a byte-identical article list**. The analyst can only
differentiate stocks from its own priors, which is why the news IC is noise: as of
2026-07-17 the EW rolling news IC is ≈ +0.00 (growth) and −0.01 (value), the worst of the
three analysts, and adaptive weights have already tilted news down from 0.20 to ≈ 0.185.

`YahooFinanceAdapter.get_news(symbols=[...])` was retained in the 2026-04-14 redesign,
is free, and works today (verified live 2026-07-17). This design adds company-specific
headlines to each stock's context, clearly labeled by scope, so the news signal can
carry per-stock information.

### Premise corrections vs. the original work-item brief (verified 2026-07-17)

1. **The source-field bug is already fixed.** The 2026-04-14 redesign shipped the fix:
   the adapter sets `source` to the provider display name and `author=None`, and
   `format_news_context` renders `author or source`, so real provider names ("Motley
   Fool", "24/7 Wall St.") already reach prompts. Out of scope here.
2. **News scores no longer cluster 4–7.** Since the 2026-06-15 forced-ranking
   calibration, all analysts span 1–10 (news sd ≈ 2.59 in prod). The sharper framing:
   force-ranking identical inputs manufactures spread without information. Per-ticker
   input is what makes the news *rank* informative.
3. **The persistent LLM response cache needs no changes.** It keys on
   `sha256(system_prompt + user_prompt + model + temperature)`; article changes and
   skill edits auto-invalidate. Verified empirically in the 2026-07-17 gate-machinery
   rehearsal (see Appendix).

## Goals

1. Each screened stock's news context gains company-specific headlines (Yahoo per-ticker
   feed), labeled by scope, with company news explicitly weighted above sector/market.
2. Yahoo rate limits respected: screened symbols only (~25–40/branch), sequential,
   TTL-cached, no retries; per-symbol failures degrade that stock to today's behavior.
3. Backtest mode unchanged in behavior (no historical news adapters → empty company
   scope) and reproducible via the existing prompt-hash LLM cache.
4. Shipping gated on a cached-LLM experiment (non-negative verdict) **and** a clean
   item-1 live health check; cutover recorded so live news-IC before/after is measurable
   in `attribution_reports`.

## Non-goals

- No new data sources, no paid news APIs, no historical/backtest news reconstruction.
- No changes to `data_platform` adapters or service (the generic `get_news` suffices).
- No multi-provider aggregation, no LLM-based relevance judging.
- No change to ranker, weights, sizing, or any other pipeline stage.

## Design

### 1. Data flow (graph-level prefetch extension)

`_run_prefetch_news` gains a company stage after the sector fetches:

- Iterate screened symbols in sorted order (deterministic). For each:
  `await data_service.get_news(symbols=[symbol], since=since, limit=company_news_fetch_limit)`
  where `since` is the existing `_window_start(as_of_date, news_window_days)` (7 days).
- **Sequential** (no `asyncio.gather`) — politeness to Yahoo; adds ~30–60s to a weekly
  run, acceptable. `DataPlatformService.get_news` already TTL-caches per symbols-key and
  rate-limits per adapter; the two branches run minutes apart and share the cache.
- Per-symbol `except Exception`: log a warning, store an empty list — that stock
  degrades to exactly today's market+sector behavior. A total failure leaves the whole
  run byte-equivalent to today.
- `news_context` gains a fourth key: `"company": {symbol: [article_dict, ...]}` holding
  **filtered** articles (see §2), capped later at merge time.

Config: two new fields on `AgentsConfig` (`app/modules/equities/config.py`):
`company_news_fetch_limit: int = 10`, `company_news_prompt_cap: int = 6`, threaded into
graph deps alongside the existing `news_window_days` pattern.

### 2. Relevance filter, scope tagging, dedupe

**Ticker-mention filter** (equities-side, applied in prefetch to the raw per-ticker
feed; the data platform stays generic). Yahoo per-ticker feeds mix in unrelated stories
(live test: NVDA's feed carried a P&G dividend piece). An article qualifies as
company-scope if its title matches either rule:

- **Ticker token:** the uppercase ticker appears as a standalone token (regex
  `\bTICKER\b`, case-sensitive), only applied when `len(ticker) >= 2` (single-letter
  tickers like `T`/`F` would false-positive; they rely on the name rule).
- **Company name:** case-insensitive substring match of the normalized company name —
  strip a leading "The" and trailing legal-suffix tokens (`Inc`, `Inc.`, `Corp`,
  `Corp.`, `Corporation`, `Co`, `Co.`, `Company`, `Ltd`, `Ltd.`, `PLC`, `Holdings`,
  `Class A/B/C`, share-class markers) from `UniverseStock.company_name`; apply only if
  the remainder is ≥ 3 characters. "NVIDIA Corp" → "NVIDIA"; "Bank of America Corp" →
  "Bank of America".

Rejected articles are dropped from company scope entirely — generic stories are already
represented in (and naturally remain in) sector/market scope. The filter is best-effort
precision: rare false positives (generic-word names like "Target") are accepted noise.

**Scope tagging.** Every article dict gains `"scope"`: `"company"`, `"sector"`,
`"market"`, or `"manual"`, set at prefetch/merge time. Manual articles keep their
existing file format; their `scope` value may now also be a **ticker symbol** to target
a single stock (previously only `"market"` or a sector name).

**Merge + dedupe** (`_articles_for_stock`): merge manual + company(symbol) + sector +
market for the stock; dedupe by canonical URL (fallback: normalized lowercase title)
with retention priority **manual > company > sector > market** (a story appearing in
both the NVDA feed and the XLK feed renders once, under Company). Company articles are
capped at `company_news_prompt_cap` (newest first) after dedupe.

### 3. Prompt rendering and skill guidance

`format_news_context` switches from recency buckets to **scope-grouped sections**, in
order: `## Company-specific (NVDA)`, `## Sector (Technology)`, `## Market`,
`## Curated` (manual). Each section is a Date/Source/Headline table sorted newest-first
(the Date column carries the recency signal; buckets are removed). Empty sector/market
sections are omitted as today, but an empty **company** section renders explicitly:
"No company-specific headlines found for NVDA." — absence is information the analyst is
told to treat as neutral, not bearish.

`skills/base/news.md` is updated to match the new context shape: the **Input Shape**
section is rewritten (four scope-labeled sections — Company-specific, Sector, Market,
Curated — replace the "infer each article's layer from its headline" instruction, which
becomes obsolete), the final failure-mode bullet about re-inferring scope is replaced
with "trust the section labels", and explicit weighting guidance is added: company-scope
headlines dominate; sector/market adjust rather than drive; absence of company news is
neutral; weigh source quality. Branch overlays (growth/value news.md) are updated to the
same section vocabulary — the composed prompt must speak one model (caught in review:
the overlays' "Reading the Three Layers" sections would otherwise contradict the base).

Note: re-layouting the shared sections means **every news prompt changes**, not just
company-enriched ones. That is the treatment under test, and the prompt-hash cache
invalidates it correctly (rehearsed 2026-07-17).

### 4. Backtest parity

Backtests register `BacktestNewsAdapter` (an empty placeholder; the base
`NewsAdapter.get_news` also defaults to `[]`), so company fetches succeed with empty
article lists → empty company scope for all symbols → the pipeline behaves as today,
plus the new prompt layout. The per-symbol try/except additionally guards live-mode
fetch failures. `CachedAnalystWrapper` is unchanged (`articles_by_symbol` already flows
through `analyze_batch`). Consequence, stated honestly: **the backtest gate measures
prompt-shape risk only** — company-news *value* cannot be measured historically and is
instead measured live post-cutover (§6). Backtest news prompts remain date-invariant
per symbol (empty articles), so treatment re-runs cost ≈ unique screened symbols.

## Experiment gate and cutover

**Gate (approved budget: quick preset, growth only, ≈ $115 ceiling; expected ≈ $96):**

The experiment must run **from the worktree containing the item-2 code** — the new
formatter code changes news prompts in both arms, and the new config fields enter the
experiment config hash, so a noise floor probed on `main` would not match. Mechanics
(main-repo venv, worktree cwd; `baseline_v1` bundle and the LLM cache are copied in
from the main repo first so unchanged fundamentals/technical prompts stay cached):

```bash
cp -R <main>/data/skill_bundles/baseline_v1 <worktree>/data/skill_bundles/
cp <main>/data/llm_response_cache.db <worktree>/data/
python -m scripts.bundle_skills item2_news_v1          # freeze the item-2 skills from the worktree
python -m scripts.probe_noise --preset quick --branch growth --end-date 2025-06-30 \
    --runs 5 --skills-bundle baseline_v1 --yes         # ≈ $94 noise floor (probe runs bypass cache)
python -m scripts.run_experiment --preset quick --branch growth --end-date 2025-06-30 \
    --baseline-bundle baseline_v1 --treatment-bundle item2_news_v1 --t-correction
    # arms mostly cache-served: only news prompts re-run (≈ $1-2 total)
```

Honest limitation: with both arms on the new code, the measured delta isolates the
**skill-text change**; the formatter-layout change is common-mode across arms (its risk
is covered by unit tests and by the fact that backtest company scope is empty).

**Ship criterion:** the experiment verdict is non-negative — treatment is *improved* or
*within the noise floor*. A regression beyond the floor blocks shipping and sends the
change back to design.

**Cutover precondition — both must hold:**

1. **Item-1 live health check passes** (the deferred Phase-0 check-in, run against prod
   after the 2026-07-20 and 2026-07-27 runs): `portfolio_decisions.analyst_weights`
   non-null with sane values (mode=adaptive, no fallback reasons), attribution rows
   present with the `composite` IC, no pathological weight paths.
2. Non-negative gate verdict (above).

**Cutover:** ships alone in its own weekly cycle (one behavior change per cycle);
earliest realistic cutover **2026-08-03**. The actual cutover week MUST be recorded in
this spec (below) when it happens.

> **Gate verdict (quick/growth, 2026-07-17, N=5 floor, t-corrected): NON-NEGATIVE — PASS.**
> Treatment `item2_news_v1` (sha 006016f16de3) vs baseline `baseline_v1` (sha
> e9dccc5e909b): total_return +2.80pp (+1.4σ), sharpe +0.108 (+1.5σ), win_rate +13.7pp
> (+2.3σ), SPY.alpha +5.9pp (+1.5σ), max_drawdown −0.1pp (within noise). No metric
> regressed. Mechanism verified from the saved runs: only the 20 news signals differed
> (fundamentals/technical byte-identical across 414 signals); shifts were mostly
> confidence-only (e.g., (6,4)→(6,3)) — the analyst lowering confidence on
> inference-only calls per the new absence-is-neutral guidance, exactly as designed.
> Positive deltas at 1–2σ are encouraging but the gate's bar was non-negativity, not
> proof. Report: `data/backtest_runs/item2_gate_report.txt` (local, gitignored).
> Total gate spend ≈ $100 (probe $94 + experiment ~$6; cache reuse held).
>
> **Cutover week:** _pending_ (to be filled at ship time; target 2026-08-03, after the
> item-1 live health check on the 07-20/07-27 runs)

**Measurement plan:** compare `attribution_reports.analyst_ics["news"]` (weekly, and
the EW rolling news IC from the adaptive-weights machinery) for ≥ 4 weeks after cutover
against the pre-cutover series. With n this small the read is directional, not
statistical; the adaptive weights loop will independently tilt news up if the IC
improves. Secondary checks: news-score dispersion within sectors (should rise) and the
digest's news EWIC line.

## Risks

- **Filter false positives/negatives.** Generic company names ("Target") admit noise;
  stories that name neither ticker nor company are dropped. Accepted: the filter is a
  precision improvement over unlabeled merging, and sector/market scope still carries
  the generic stories.
- **Yahoo feed quality varies by symbol.** Small/less-covered names may get empty
  company sections — rendered explicitly as neutral absence.
- **Prompt growth.** Bounded by the cap (6 company articles) and existing sector/market
  limits. Note (final review): the news analyst's pre-existing 20-article truncation now
  applies to the scope-ordered merged list, so a busy sector feed plus 6 company
  articles can push the Market section out entirely — most-specific content survives by
  design; a per-scope trim is possible follow-up hardening.
- **Ranker interaction.** Forced ranking still shapes the distribution; the change is
  that ranking input is now differentiated per stock. No ranker changes.
- **Rate limits.** Sequential + TTL + screened-only keeps per-run Yahoo news calls
  ≤ ~40; CLAUDE.md's documented ban risk applies to full-universe *quote* scans, which
  this does not add to.

## Testing

TDD (superpowers flow) — new unit tests:

- Filter: ticker-token matching (≥ 2 chars, case-sensitive, word boundary),
  single-letter ticker skip, name normalization (leading "The", suffix stripping,
  ≥ 3-char remainder), combined rule.
- Dedupe: URL priority manual > company > sector > market; title fallback; company cap
  applied newest-first.
- Scope tagging and `_articles_for_stock` merge with the company map; manual
  ticker-scope targeting.
- `format_news_context`: section order, empty-company explicit line, omitted empty
  sector/market sections, date-desc sorting.
- Prefetch degrade: per-symbol failure → empty company list for that symbol only;
  total failure → context equals today's; sequential call order.
- Existing news formatter/graph tests updated for the new layout.

`ruff check` and the full unit suite stay green. CLAUDE.md: update the news-context
description (scope-labeled articles, company fetch, filter rule) and the adaptive/news
gotchas if semantics shift.

## Files

| File | Change |
|---|---|
| `app/modules/equities/agents/graph.py` | Company prefetch stage, filter, scope tagging, merge/dedupe/cap |
| `app/modules/equities/agents/context_formatters.py` | Scope-grouped `format_news_context` |
| `app/modules/equities/agents/skills/base/news.md` | Scope-weighting guidance |
| `app/modules/equities/config.py` | `company_news_fetch_limit`, `company_news_prompt_cap` on `AgentsConfig` |
| `tests/unit/equities/...` | New + updated tests per above |
| `CLAUDE.md` | News-context semantics |

No changes: `data_platform/*`, backtest engine, `CachedAnalystWrapper`,
`llm_response_cache.py`, ranker, portfolio manager.

## Appendix — 2026-07-17 gate-machinery rehearsal (evidence)

Micro config (growth, top-10, 2025-04-01→06-30, screens to AMZN/GOOG/GOOGL), ~$4.50:

| Run | Bundle | Time | API calls | Outcome |
|---|---|---|---|---|
| 1 | `baseline_v1` | 1m57s | 375 | LLM-mode works on current main; cache seeded |
| 2 | `baseline_v1` (identical) | 3.2s | 0 | $0; outputs identical (signals multiset-equal; list order is async-nondeterministic) |
| 3 | one-line edit to bundle `base/news.md` | 9.5s | 3 | Only the 3 unique news prompts re-ran; fund/tech fully cached; distinct bundle hash in run id |

Known observability quirks (accepted, not in scope): saved `llm_cache_hits/misses`
count only the analyst-client cache instance (~306/375 rows in the rehearsal came from
a second, uncounted instance — likely the ranker's `invoke_raw` client); signal list
order in saved runs is nondeterministic.

### Live render verification (2026-07-17, post-implementation)

Real Yahoo fetches through the staged code path (market + Technology sector + NVDA +
MSFT company feeds → filter → tag → merge → `format_news_context`):

- **Structure correct**: scope sections in order, explicit company-absence line, real
  provider names, dedupe, caps — the rendered markdown matches the skill's Input Shape
  description exactly.
- **Filter precision 100% on the live sample** (21/21 decisions correct): every dropped
  NVDA-feed title was genuinely non-NVDA content (AbbVie, Costco, Meta, IBM, TSMC…);
  the one keep ("Microsoft cut 4,800 jobs…") is genuinely company-specific.
- **Expected live sparsity**: Yahoo's per-ticker feed is exactly 10 items deep
  (`limit=10` truncates nothing — verified with limit=100) and ~90% related-market
  noise, so company sections will often hold 0–1 articles. That is the designed common
  case: the absence line + neutral guidance carry the honest-confidence mechanism the
  gate rewarded, and real company events (layoffs, earnings) punch through when they
  exist. Live news-IC post-cutover remains the value measurement.
