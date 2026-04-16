# News Prompt Follow-Up Refinements

**Date:** 2026-04-16
**Status:** Design
**Related:** `2026-04-16-news-skill-prompt-redesign.md` (the rewrite this follows up on)

## Problem

The news skill prompt redesign shipped on 2026-04-16 as commit `df3d1c1`. A retrospective surfaced four concerns that the original spec did not address. Three of them are real risks that could erode the redesign's value in production, and the fourth is a low-cost maintainability improvement. This spec scopes the follow-up work.

### Item 3 — Prior-knowledge hallucination risk

The base prompt's Stock-Exposure Assessment section instructs the LLM: *"your prior knowledge of the company is valid input here"* and *"Company prior knowledge is valid input. You know what most listed companies do..."*. This unlocks useful reasoning for well-known tickers (AAPL, NVDA, JPM) but has no guardrails for:

- Less-known tickers where the LLM may hallucinate the business model
- Companies that have materially changed since the model's training cutoff
- Name collisions between ticker and non-public entity

The prompt currently has no mechanism that makes reliance on prior knowledge auditable or self-correcting when the knowledge is wrong.

### Item 4 — Overlays may under-anchor score magnitudes

The overlay rewrites replaced the old mechanical modifiers (`+1`, `-2`) with qualitative guidance ("judge magnitude by signal strength and corroboration"). LLMs tend to follow specific guidance more reliably than vague judgment calls. The risk is that, without anchors, scores collapse toward the middle of the calibration scale — weakening the news analyst's ability to produce differentiated scores across stocks.

### Item 5 — Structural duplication across overlays

Growth and value overlays have identical H2 section headers (Investment Thesis, Reading the Three Layers, Stock-Exposure Emphasis, Signals That Warrant Strong Conviction). If a future edit adds a section to one but not the other, the overlays drift — and nothing in the test suite would catch it.

### Item 6 — New failure modes of a three-layer prompt are not flagged

The rewritten Critical Reminders and Common Failure Modes sections are mostly repurposed from the old per-stock prompt. They do not warn the LLM about failure modes introduced by the three-layer input shape:

- Uniformly applying a macro signal across all stocks in a sector (collapses the news analyst into a regime classifier)
- Conflating sub-sector themes (e.g., treating an "AI infrastructure" rally as equally bullish for every technology stock)
- Misattributing articles to the wrong layer because time-bucketed tables don't label scope

## Goals

1. Add grounding and uncertainty guidance for prior-knowledge reasoning (Item 3).
2. Add qualitative score-zone anchors to both overlays so the LLM has reference points for magnitude (Item 4).
3. Add a structural invariant test that keeps growth and value overlays parallel (Item 5).
4. Add three-layer-specific warnings to Critical Reminders and Common Failure Modes in the base prompt (Item 6).
5. Make the LLM smoke check mandatory — not optional — in the final verification step. Run across 5+ stocks (mix of tech, value, obscure) to validate that anchors produce differentiated scores.

## Non-Goals

- Running a paired backtest comparison (old prompt vs new prompt). Still deferred.
- Restructuring the skill composition layer (e.g., shared overlay template).
- Rewriting the base prompt's Score Calibration table. Score anchors live in overlays only.
- Changing the overlays' section headers. Only the Signals-That-Warrant-Strong-Conviction body gains anchors.

## Design

### Item 3: Grounding and uncertainty for prior knowledge

**Location:** `base/news.md`, Stock-Exposure Assessment section.

Add a new sub-section after the existing bullets:

> **Grounding when using prior knowledge.** When you reason about a company using your pretrained knowledge (e.g., "NVDA is a GPU supplier", "JPM is rate-sensitive retail banking"), name the specific piece of prior knowledge you are relying on in your summary. If that prior knowledge cannot be corroborated by the articles in your context or by the stock's sector classification, lower your confidence. For less-known tickers where you are uncertain about the company's business model or current positioning, default to sector-level reasoning and set confidence accordingly.

Also add one bullet to Critical Reminders:

> - When you rely on prior knowledge about a company, say so explicitly in your summary and lower your confidence if that knowledge cannot be corroborated by the articles or sector classification.

### Item 4: Score-zone anchors in overlay signal-strength sections

**Location:** `branches/growth/news.md` and `branches/value/news.md`, Signals That Warrant Strong Conviction section.

**Design constraint:** anchors must be *zones on the calibration scale*, not offsets from a base. The old design said "+2 for insider buying" (mechanical offset). The new design says "insider buying is typically an 8-9 signal in isolation" (zone on the 1-10 scale, still synthesized by the LLM with other signals).

**Growth overlay (new Signals section):**

```markdown
## Signals That Warrant Strong Conviction

**Strongly bullish — typically an 8-9 signal zone when the signal is isolated, a 9 when multiple layers align:**
- A dovish Fed pivot with the market visibly repricing (rate cuts getting pulled forward)
- Sector outperformance driven by a secular theme the stock directly embodies
- Stock-specific: major product launch, enterprise win with a named customer of meaningful size, or raised guidance

**Moderately bullish — typically a 6-7 signal zone when isolated:**
- Falling-rate environment without a clear pivot signal
- Sector outperformance without a clearly-identified secular theme
- Stock-specific: positive analyst actions, incremental product announcements

**Strongly bearish — typically a 2-3 signal zone, a 1-2 when multiple layers align:**
- A hawkish surprise (hike or hawkish Fed speak) with the market repricing
- Rotation OUT of growth sectors into value, defensives, or cash
- Stock-specific: guidance cut, growth-slowdown signal, or evidence that the secular thesis the stock depends on is eroding

**Moderately bearish — typically a 4 signal zone when isolated:**
- Mixed rate signals without a clear direction
- Sector underperformance without a structural-decline narrative
- Stock-specific: minor analyst downgrades, delayed product timelines

Zones are reference points, not mechanical offsets. The LLM judges the final score by synthesizing all signals — a single moderately bullish signal with contradicting stock-specific headwinds may land at 4, not 6.
```

**Value overlay (new Signals section):**

```markdown
## Signals That Warrant Strong Conviction

**Strongly bullish — typically an 8-9 signal zone when isolated, a 9 when multiple layers align:**
- Insider buying with personal funds — the strongest conviction signal available, because insiders are betting their own capital
- An activist investor taking a meaningful position with a public thesis
- Dividend raise or a buyback announcement at a clearly depressed valuation
- Macro rotation INTO the stock's sector with a clear underlying driver (rate-normalization tailwind for financials, commodity cycle for energy, onshoring capex for industrials)

**Moderately bullish — typically a 6-7 signal zone when isolated:**
- Rising-rate environment without explicit growth-to-value rotation
- Sector tailwind from a known driver but without stock-specific corroboration
- Stock-specific: analyst upgrade off a neutral rating, modest capital-return increase

**Strongly bearish — typically a 2-3 signal zone, a 1-2 when multiple layers align:**
- Dividend cut — directly destroys the capital-return thesis, and usually signals deeper business distress
- A structural-decline narrative dominating sector news (not a cyclical headwind but secular erosion of the business model)
- A growth-euphoria melt-up environment where long-duration assets are rallying and value is being left behind

**Moderately bearish — typically a 4 signal zone when isolated:**
- Sector underperformance without a structural-decline narrative
- Risk-on rally driven by speculative flows
- Stock-specific: guidance at risk, modest cash-flow deterioration

Zones are reference points, not mechanical offsets. The LLM judges the final score by synthesizing all signals — a strongly-bullish stock-specific event in a moderately-bearish sector may still land at 6-7.
```

### Item 5: Parallel-structure invariant test

**Location:** `tests/unit/equities/test_skill_loader.py`, appended after the existing overlay tests.

```python
class TestOverlaysHaveParallelStructure:
    """Growth and value overlays must have the same H2 section headers.

    Prevents drift when someone edits one overlay but forgets to mirror
    the change in the other.
    """

    def test_growth_and_value_overlay_sections_match(self):
        import re
        g_prompt = compose_system_prompt("news", "growth")
        v_prompt = compose_system_prompt("news", "value")

        # Extract H2 headers from the overlay portion (everything after the
        # branch header, and before the output format separator).
        g_overlay = g_prompt.split("# Growth Branch")[1].split("\n---\n")[0]
        v_overlay = v_prompt.split("# Value Branch")[1].split("\n---\n")[0]

        g_sections = re.findall(r"^## .+$", g_overlay, re.MULTILINE)
        v_sections = re.findall(r"^## .+$", v_overlay, re.MULTILINE)

        assert g_sections == v_sections, (
            f"Overlay structure drift.\n"
            f"  Growth sections: {g_sections}\n"
            f"  Value sections:  {v_sections}"
        )
```

### Item 6: New failure-mode warnings

**Critical Reminders — add two bullets at the end:**

> - A macro signal is not a uniform modifier. Translate it into stock-level impact — a rate-cut tailwind does not help every stock in the universe equally, and a sector rally does not lift every constituent the same way.
> - Distinguish sub-sector themes within a sector. An "AI infrastructure" rally does not lift every technology stock; a "net interest margin expansion" article does not help every financial. Identify which specific stocks actually benefit from the theme.

**Common Failure Modes — add two bullets at the end:**

> - Do not apply macro sentiment uniformly across stocks in the same sector. If you give every Technology stock the same score on the same rebalance, the news analyst has collapsed into a regime classifier and is no longer a differentiated signal. Differentiation comes from stock-exposure reasoning, not from reading different articles.
> - Classify each article by its scope before weighting. The time-bucketed tables ("Last 7 Days", etc.) do not label articles by scope (market / sector / stock-specific) — use the headline and content to infer which layer each article belongs to. A misclassified article gets weighted incorrectly.

## Validation

**Mandatory LLM smoke checks** (no longer optional):

1. **Differentiation check**: run the news analyst against 5 Technology stocks with identical market+sector articles (no stock-specific content). Stocks: NVDA, MSFT, AAPL, IBM, INTC. Verify scores meaningfully differentiate (not all 6-7) — the LLM should produce different scores based on each stock's exposure to the AI/cloud/rate themes.

2. **Value-branch check**: run the news analyst on JPM and XOM (Financials / Energy) with the value overlay and a value-supportive macro scenario (rising rates, rotation into value). Verify the value overlay's reasoning shows up — specifically the rate-sensitivity and sector-driver logic.

3. **Obscure ticker check**: run on a less-known ticker (e.g., a mid-cap growth name from VOOG) to verify the Item 3 grounding guidance kicks in — the LLM should either cite specific prior knowledge with honest confidence or default to sector-level reasoning with appropriately lower confidence.

Success criteria: check (1) produces a spread of at least 3 score points across the 5 stocks. Check (2) shows explicit macro-rotation and sector-driver reasoning in the summary. Check (3) shows either cited prior knowledge or explicit sector fallback with appropriately calibrated confidence.

If any check fails, iterate on the prompt before landing.

**Unit tests (added):**
- `TestOverlaysHaveParallelStructure::test_growth_and_value_overlay_sections_match` — invariant for Item 5
- Extend `TestNewsPromptStructure` to require the new grounding sub-section in base prompt
- Extend overlay structure tests to require the new Moderately-Bullish and Moderately-Bearish zone sections

## File Changes

**Rewritten (partial — append/modify sections):**

| File | Change |
|------|--------|
| `app/modules/equities/agents/skills/base/news.md` | Add 2 bullets to Critical Reminders; add "Grounding when using prior knowledge" sub-section after Stock-Exposure Assessment bullets; add 2 bullets to Common Failure Modes |
| `app/modules/equities/agents/skills/branches/growth/news.md` | Replace Signals That Warrant Strong Conviction section with the zone-anchored version |
| `app/modules/equities/agents/skills/branches/value/news.md` | Replace Signals That Warrant Strong Conviction section with the zone-anchored version |

**Modified (test additions):**

| File | Change |
|------|--------|
| `tests/unit/equities/test_skill_loader.py` | Add `TestOverlaysHaveParallelStructure` class; extend existing structure tests for new base-prompt sub-section and new Moderately-* zone sections in overlays |

**Unchanged:** loader, analyst, context formatter, graph, portfolio manager, config.

## Risks

- **Zone anchors may push scores to the extremes.** Telling the LLM "insider buying is typically an 8-9" may cause it to produce 8-9 scores on every stock that has even minor insider activity, over-inflating conviction. Mitigation: the anchors explicitly say "when the signal is isolated" and "when multiple layers align" to constrain the zones. The mandatory smoke check will surface this if it happens.
- **"Don't apply macro uniformly" warning may cause over-correction.** The LLM may invent differentiation where none exists, producing noisy scores. Mitigation: check (1) in the smoke validation — if all 5 Tech stocks get wildly divergent scores without clear reasoning, the warning is too strong. Also: base prompt's Common Failure Modes already tells the LLM "differentiation comes from stock-exposure reasoning, not from reading different articles" — which should anchor the differentiation to real exposure differences.
- **Obscure-ticker check is hard to define precisely.** "Obscure" is subjective; a ticker that's obscure to one LLM version may be well-known to another. Mitigation: pick a ticker that's in the universe but not frequently in headlines (e.g., a mid-cap growth name from VOOG beyond the top 20). Accept that this check is a qualitative judgment, not pass/fail.
