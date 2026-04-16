# News Skill Prompt Redesign

**Date:** 2026-04-16
**Status:** Design

## Problem

The news analyst skill prompts (`app/modules/equities/agents/skills/base/news.md` plus the growth and value overlays) were written for an input shape that no longer exists. They assume the LLM sees stock-specific articles ("Apple beats Q4 estimates", "CEO sells $50M in shares") and tell it to classify each headline by source quality, recency, and event type. After the news ingestion redesign shipped on 2026-04-14, the LLM now sees market + sector articles plus a (usually sparse) stock-specific layer containing earnings events and optional manual articles.

The current prompts still work — the LLM produces *something* — but the reasoning framework is misaligned with the input. Specific mismatches:

- The "classify each headline" framing treats market articles like stock events, which mis-attributes macro signals
- The "absence of news is mildly positive for established companies" heuristic no longer fires correctly; the LLM almost always has market/sector articles
- The worked example (Apple earnings + analyst antitrust + Tim Cook insider sale) has no analog to the new input
- The branch overlays tell the LLM to weight stock-specific events (product launches, insider buying) when those are now the rarest layer, not the primary one
- The Red Flags list is all per-stock corporate events (SEC investigation, accounting restatement, product recall) with no macro or sector equivalents

The goal is to rewrite all three prompts around the three-layer input shape, preserving the analyst's role as a genuinely differentiated signal in the 35% weight slot while accommodating a future where stock-specific articles become richer.

## Goals

1. Rewrite the base prompt around the three-layer input shape (Market / Sector / Stock-specific).
2. Rewrite both branch overlays to tune the reasoning lens without prescribing numeric score offsets.
3. Preserve flexibility for a future richer stock-specific layer without requiring another rewrite.
4. Maintain the existing skill composition mechanism (`compose_system_prompt`), the existing context formatter output, and the existing `NewsAnalyst` interface — only the prompt content changes.
5. Add lightweight regression tests that catch prompt composition breakage and stale phrasing.

## Non-Goals

- Changing `format_news_context` output. The rendered markdown the LLM receives is unchanged.
- Changing `NewsAnalyst.analyze()` or `compose_system_prompt`. Prompts are pure content.
- Adjusting composite score weights. Fundamentals 0.40 / news 0.35 / technical 0.25 stay the same.
- Paired backtest comparison (old prompt vs new). Out of scope for this task; can be run manually after landing if desired.

## Framing

**Approach: macro lens on an individual stock.** The LLM reasons from macro environment down through sector conditions to the specific stock's exposure, producing a stock-specific bullish score that reflects how broad conditions translate to this stock's outlook over 1-3 months.

**Three layers:**
- **Market layer**: broad-market articles (sourced from SPY-proxy via Yahoo Finance today, a real provider later) plus manual articles with `scope: "market"`. Characterizes the macro environment: bull/bear, risk-on/risk-off, rate direction, inflation signals.
- **Sector layer**: sector-ETF news (XLK for Technology, XLF for Financials, etc.) plus manual articles scoped to the sector. Characterizes sector conditions: outperforming/underperforming the market, sub-sector themes visible in the news flow, structural narratives.
- **Stock-specific layer**: today sparse (earnings events from SEC filings, optional manual articles tagged with the stock's ticker via the `symbols` field). Future-compatible: when a per-ticker news source is plugged in, articles flow into this layer without requiring a prompt rewrite.

**Forward compatibility:** The prompt explicitly tells the LLM layers vary in richness across rebalances. When stock-specific is sparse, the LLM relies on macro-sector inference (lower confidence, inferential). When stock-specific becomes rich (future state), the LLM lets stock-specific signals dominate with macro/sector as supporting context (higher confidence, direct signal). One prompt, two regimes, no rewrite needed when the stock-specific layer fills in.

## Base Prompt Structure

Full rewrite of `app/modules/equities/agents/skills/base/news.md`, organized around the three-layer input:

### 1. Role (~2 sentences)

Tell the LLM it is a macro/sector analyst producing a stock-specific bullish outlook over 1-3 months. Output is a 1-10 bullish score and 1-10 confidence.

### 2. Input shape (~150 words)

Explain the three layers explicitly. Tell the LLM where each layer's articles come from in the rendered markdown (the existing time-bucket headers — "Last 7 Days", "Last 30 Days", "Older" — still apply; the scope of articles within those buckets is now market/sector/stock-specific). Tell it the layers vary in richness — especially stock-specific — and that's expected. Explicitly state that scarcity of the stock-specific layer on a given rebalance is not a reason to default to neutral-with-low-confidence; instead, lean on macro-sector inference and modulate confidence accordingly.

### 3. Reasoning chain (~200 words, 5 numbered steps)

1. **Characterize the macro environment** from market articles. Bull/bear direction, risk-on/risk-off posture, rate direction if signaled, inflation regime if signaled.
2. **Characterize the sector environment** from sector articles. Outperforming/underperforming the market, visible sub-sector themes (e.g., "AI infrastructure spending" within Technology), structural narratives.
3. **Assess this stock's exposure** to the macro/sector signals. Use the ticker, company name, and sector as anchors. The LLM's prior knowledge of the company is a valid input here — e.g., NVDA is a core AI beneficiary, IBM is less so; within financials, JPM is rate-sensitive retail banking, GS is capital-markets-heavy.
4. **Check the stock-specific layer.** Earnings filings, manual articles. If present, these are stronger signals than macro inference and may override the macro-inferred outlook.
5. **Synthesize** into a stock-specific score. Weight of each layer scales by its signal strength: strong macro signal with clear stock exposure → high confidence; stock-specific event present → stock-specific dominates; mixed or weak signals → lower confidence.

### 4. Stock-exposure assessment (~100 words)

Specific heuristics for mapping macro/sector signals to stock impact:
- Rate-sensitive: falling rates favor long-duration growth assets; rising rates favor near-term cash flows and financials
- Sector breadth: a sector rally doesn't help every stock equally — core thematic beneficiaries move more than peripheral names
- Narrow sub-sector themes (e.g., "AI infrastructure") help pure-plays more than conglomerates
- Explicitly: the LLM's general knowledge of the company (business model, revenue mix, competitive position) is a valid input to exposure assessment

### 5. Score Calibration

Reframed 1-10 table mapping score levels to environment-adjusted outlook. Draft levels:

| Score | Label | Criteria |
|-------|-------|----------|
| 1-2 | Strong Sell | Macro or sector headwinds specifically hit this stock's exposure. Stock-specific negative catalyst if present. Very bearish outlook. |
| 3-4 | Bearish | Macro/sector conditions unfavorable for this stock. No offsetting stock-specific positives. |
| 5 | Neutral | Mixed or weak macro signals; stock exposure unclear; no stock-specific events. Honest neutrality. |
| 6-7 | Bullish | Macro and sector tailwinds align with this stock's exposure. Moderately bullish outlook over 1-3 months. |
| 8-9 | Strong Buy | Macro tailwind + sector tailwind + stock exposure aligned + ideally a confirming stock-specific event. High-conviction bullish. |
| 10 | Extreme Conviction | All three layers align with unusual strength. Rare. |

### 6. Confidence Calibration

Reframed around the three-layer strength rather than article count:

| Level | Criteria |
|-------|----------|
| 1-3 | Weak or contradictory macro signals; stock exposure ambiguous; no stock-specific events. Score is tentative. |
| 4-6 | Moderate macro signal with inferred sector alignment; limited stock-specific confirmation. Score reflects reasoned inference but not direct evidence. |
| 7-8 | Clear macro signal + clear sector alignment + stock exposure explicit. Or: rich stock-specific layer present and consistent with macro/sector. |
| 9-10 | All three layers align strongly and independently. Rare. |

### 7. Worked examples

Two short examples demonstrating the same framework in both regimes:

**Example A — Sparse stock-specific layer (the common case today):**
Scenario: Market articles show "Fed signals rate cuts", "S&P 500 up 3% on easing cycle". Sector articles show "Technology sector leads broad market by 2%", "AI spending forecasts raised". No stock-specific articles for NVDA.
LLM reasoning: macro is risk-on with rate-cut tailwind; sector is outperforming on a secular theme NVDA directly embodies (AI infrastructure); no contradicting stock-specific events. NVDA is a core AI beneficiary in a rate-cut environment.
Score: 7. Confidence: 6 (macro inferential, no direct stock-specific confirmation).

**Example B — Rich stock-specific layer (future regime):**
Scenario: Same macro environment. Stock-specific layer: "Intel reports Q4 EPS miss", "Intel cuts dividend", "Intel plans 15% workforce reduction".
LLM reasoning: macro and sector environment would otherwise be bullish, but stock-specific headwinds are concrete and recent. Earnings miss + dividend cut is a direct negative that outweighs inferred macro/sector tailwinds.
Score: 3. Confidence: 7 (direct signal in stock-specific layer dominates).

### 8. Common failure modes (~5 bullets)

- Don't assume a broad market signal applies uniformly to every stock — a sector rally driven by AI helps different constituents very differently.
- Don't over-weight a single macro article — look for consistent signal across multiple articles and across market + sector layers.
- Don't default to 5/low-confidence as a cop-out when the macro signal is clear but stock exposure is ambiguous. Pick a side with honest confidence.
- Don't ignore the stock-specific layer when present. Earnings filings and explicitly-tagged articles are stronger signals than inferred macro effects.
- Don't treat "absence of stock-specific articles" as negative or positive on its own. Today this is the default state; it just means you're reasoning from macro/sector inference.

## Branch Overlays

Both overlays rewrite to tune the macro-lens reasoning for branch thesis. **No explicit score modifiers** — the overlay shapes how the LLM interprets signals; the base prompt's Score Calibration maps that interpretation to a number. The LLM decides magnitude based on signal strength and corroboration.

### Growth overlay (`skills/branches/growth/news.md`)

1. **Investment Thesis** (~100 words)
   Growth investors pay up for future earnings. Returns depend on continued earnings expansion AND the market maintaining or expanding the multiple it assigns those earnings. Sensitive to the discount-rate environment, evidence of secular growth drivers (vs cyclical bounces), and the market's risk appetite.

2. **What this means for reading the three layers** (~150 words)
   - **Macro layer**: weight discount-rate conditions heavily (falling rates → multiple expansion for long-duration assets); risk-on/risk-off is the next most important axis
   - **Sector layer**: distinguish secular tailwinds (AI, cloud, energy transition) from cyclical bounces; secular is load-bearing for growth theses
   - **Stock-specific layer**: prioritize product launches, enterprise wins, TAM expansion, raised guidance; dividend initiations may signal growth runway narrowing

3. **Stock-exposure emphasis** (~50 words)
   Within a rallying sector, score should skew more bullish for core secular beneficiaries than for incumbents being disrupted. Tech rallying on AI helps NVDA more than legacy IBM; cloud migration helps hyperscalers more than traditional infra.

4. **Signals that warrant strong conviction** (~100 words)
   - **Strongly bullish**: dovish pivot with market repricing; sector outperformance driven by a secular theme the stock embodies; major product launch or raised guidance
   - **Strongly bearish**: hawkish surprise; rotation OUT of growth into value/defensives; guidance cut or growth-slowdown signal; evidence the secular thesis is eroding
   - These warrant sharp score moves. Weaker versions of the same signals warrant proportionally smaller moves.

### Value overlay (`skills/branches/value/news.md`)

1. **Investment Thesis** (~100 words)
   Value investors pay less than intrinsic value for existing cash flows. Returns depend on mean reversion, re-rating, or reliable capital return, plus avoiding value traps. Sensitive to cash flow quality, insider conviction, catalyst visibility.

2. **What this means for reading the three layers** (~150 words)
   - **Macro layer**: weight rotation signals heavily. Rising rates favor near-term cash flows (value tailwind); growth→value rotation, flight to quality, moderate stable inflation help value. Risk-on melt-ups hurt.
   - **Sector layer**: traditional value sectors (financials, energy, industrials, utilities, materials) have distinct drivers — rising NIM for financials, commodity strength for energy. Watch for structural-decline narratives: these seed value traps.
   - **Stock-specific layer**: prioritize insider buying, activist involvement, dividend increases, buybacks at depressed valuations, re-ratings off sell/underperform

3. **Stock-exposure emphasis** (~50 words)
   Within sector tailwinds, favor established franchises with durable cash flows over speculative plays. A value thesis requires the business to be sound; cheap does not equal broken.

4. **Signals that warrant strong conviction** (~100 words)
   - **Strongly bullish**: insider buying with personal funds (strongest conviction signal available); activist taking meaningful position; dividend raise or buyback at depressed valuation; rotation INTO the sector with a clear macro driver (rate normalization for financials, commodity cycle for energy)
   - **Strongly bearish**: dividend cut (destroys the cash-flow thesis); structural-decline narrative dominating sector news (not cyclical — secular erosion); growth-euphoria melt-up environment
   - These warrant sharp score moves; weaker signals warrant proportionally smaller moves.

## Testing Strategy

Lightweight, CI-safe only (per user choice). No backtest comparison in scope.

### Test 1: Composition smoke

File: `tests/unit/equities/test_skill_loader.py` (extend existing).

Verify `compose_system_prompt("news", "growth", "Technology", skills_dir)` returns a string containing new base-prompt section markers ("Input shape", "Reasoning chain", "Stock-exposure assessment", "Common failure modes") AND new growth overlay markers ("Investment Thesis", "Signals that warrant strong conviction"). Same for value.

Catches: broken skill composition, accidentally deleted sections, overlay not being appended.

### Test 2: No stale per-stock assumptions

Same file. Assert the rewritten base prompt does NOT contain these known-stale phrases (case-insensitive match):

- "absence of news is mildly positive" — old heuristic that misfires on the new input
- "press releases" — old per-stock corporate-comms content
- "SEC investigation" — old red-flag list specific to per-stock events
- "Tim Cook" — old worked-example reference

Also assert neither overlay contains the old mechanical-modifier pattern — regex `\*\*[+-]\s*\d\*\*` (matches `**+1**`, `**+ 2**`, `**-2**`, etc., which was the exact formatting of the old overlay's Score Modifiers section). This is narrow enough to avoid false positives on legitimate content like "rising rates by 50bps" while catching any accidental reintroduction of the old pattern during future edits.

Catches: incomplete rewrite (stale content left behind), reintroduction of the mechanical-modifier pattern during future edits.

### Test 3: LLM call shape unchanged

File: `tests/unit/equities/test_news_analyst.py` (extend existing `test_branch_name_selects_overlay`).

Verify the composed `system_prompt` passed to `llm_client.invoke(...)` contains the new section markers, confirming the analyst reaches the LLM with the new prompt rather than a stale cached one.

Catches: `NewsAnalyst` accidentally using a different prompt source.

## File Changes

### Rewritten (full content replacement)

| File | Purpose |
|------|---------|
| `app/modules/equities/agents/skills/base/news.md` | New 8-section structure (Role, Input shape, Reasoning chain, Stock-exposure assessment, Score Calibration, Confidence Calibration, Worked examples, Common failure modes) |
| `app/modules/equities/agents/skills/branches/growth/news.md` | New 4-section structure (Investment Thesis, Reading the three layers, Stock-exposure emphasis, Signals that warrant strong conviction) |
| `app/modules/equities/agents/skills/branches/value/news.md` | Same 4-section structure as growth, branch-specific content |

### Modified (test additions)

| File | Change |
|------|--------|
| `tests/unit/equities/test_skill_loader.py` | Add composition smoke tests + stale-phrase regression tests |
| `tests/unit/equities/test_news_analyst.py` | Extend `test_branch_name_selects_overlay` to check for new-section markers in composed system prompt |

### Unchanged

- `app/modules/equities/agents/skills/loader.py` (composition logic)
- `app/modules/equities/agents/news_analyst.py`
- `app/modules/equities/agents/context_formatters.py`
- Everything in `graph.py`, `portfolio_manager.py`, config files

## Out of Scope

- Paired backtest comparison (old prompt vs new) — deferred to post-landing manual validation
- Stock-specific news provider integration — handled by the future Item 1 in the news-agent roadmap
- Manual article `symbols`-based filtering — data model supports it but `_articles_for_stock` does not yet consume it; deferred until a use case warrants
- Revisions to the fundamentals or technical skill prompts — unrelated to this change

## Risks

- **LLM behavior shift is not directly measured.** Unit tests verify composition, not reasoning quality. If the new prompt produces materially worse scores than the old one, we won't detect it until a backtest comparison is run. Mitigation: the prompt redesign is reversible via git; a post-landing backtest comparison can trigger rollback or iteration.
- **"No explicit modifiers" design may under-specify the overlay.** The LLM might underweight branch thesis guidance without numeric anchors. Mitigation: if early outputs look branch-agnostic, we can add qualitative strength anchors (e.g., "strong / moderate / weak") without going back to numeric offsets.
- **Stock-exposure inference relies on the LLM's prior knowledge of companies.** For well-known large-caps this is reliable; for obscure small-caps it may produce generic reasoning. Mitigation: current universe (top-N by ETF weight from VOOG/VOOV) skews to large-caps where LLM priors are strong.
