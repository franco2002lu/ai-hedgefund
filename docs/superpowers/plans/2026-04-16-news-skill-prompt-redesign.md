# News Skill Prompt Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the three news skill prompt files (base + growth overlay + value overlay) around the three-layer input shape (market/sector/stock-specific) and replace mechanical score-modifier prescriptions with thesis-based guidance.

**Architecture:** Three markdown prompt files are rewritten with no changes to the skill composition layer, `NewsAnalyst`, or the context formatter. The new base prompt follows an 8-section structure around the three-layer input. Branch overlays tune the macro-lens reasoning via investment thesis + layer-specific guidance + signal-strength anchors, with no numeric modifiers. Two regression tests prevent the old stale phrasing and the old mechanical-modifier pattern from reappearing.

**Tech Stack:** Markdown, Python 3.12, pytest with `asyncio_mode = "auto"`, ruff for lint/format.

**Spec:** `docs/superpowers/specs/2026-04-16-news-skill-prompt-redesign.md`

---

## File Plan

**Files rewritten (full content replacement):**
- `app/modules/equities/agents/skills/base/news.md`
- `app/modules/equities/agents/skills/branches/growth/news.md`
- `app/modules/equities/agents/skills/branches/value/news.md`

**Files modified (test additions):**
- `tests/unit/equities/test_skill_loader.py`
- `tests/unit/equities/test_news_analyst.py`

**Unchanged:**
- `app/modules/equities/agents/skills/loader.py` — composition logic is unchanged
- `app/modules/equities/agents/news_analyst.py` — signature and call sites unchanged
- `app/modules/equities/agents/context_formatters.py` — the markdown the LLM receives is unchanged
- Everything in `graph.py`, `portfolio_manager.py`, config files

---

## Constraint: Preserve existing test contracts

The existing `tests/unit/equities/test_skill_loader.py::TestCriticalReminders` asserts:
1. Every base analyst prompt contains `## Critical Reminders`
2. `## Critical Reminders` appears before `## Analysis Framework`
3. `test_branch_overlay_included_for_growth` expects `"Growth Branch"` text somewhere in the composed prompt
4. `test_branch_overlay_included_for_value` expects `"Value Branch"` text somewhere
5. `tests/unit/equities/test_news_analyst.py::test_branch_name_selects_overlay` expects `"News Analyst"` + `"Growth Branch"` in the composed system prompt

The rewritten base prompt MUST have `# News Analyst` as the top header, a `## Critical Reminders` section before `## Analysis Framework`. The overlays must retain `# Growth Branch` / `# Value Branch` as their top headers.

---

## Task 1: Rewrite base news skill prompt

**Files:**
- Rewrite: `app/modules/equities/agents/skills/base/news.md`
- Modify: `tests/unit/equities/test_skill_loader.py` (append new tests for news-specific structure and stale-phrase absence)

- [ ] **Step 1.1: Write the failing tests**

Append to `tests/unit/equities/test_skill_loader.py` (at the end of the file). These tests document the contract the rewrite must satisfy:

```python
# ---------------------------------------------------------------------------
# News-prompt-specific structural tests (post-2026-04-16 redesign)
# ---------------------------------------------------------------------------


class TestNewsPromptStructure:
    """Structural tests for the rewritten news analyst base prompt."""

    def test_news_base_contains_new_section_markers(self):
        """The rewritten news base prompt has the 8-section structure."""
        prompt = compose_system_prompt("news")
        assert "# News Analyst" in prompt
        assert "## Role" in prompt
        assert "## Critical Reminders" in prompt
        assert "## Input Shape" in prompt
        assert "## Analysis Framework" in prompt
        assert "## Stock-Exposure Assessment" in prompt
        assert "## Score Calibration" in prompt
        assert "## Confidence Calibration" in prompt
        assert "## Worked Examples" in prompt
        assert "## Common Failure Modes" in prompt

    def test_news_base_section_order(self):
        """Sections appear in the documented order."""
        prompt = compose_system_prompt("news")
        order = [
            "# News Analyst",
            "## Role",
            "## Critical Reminders",
            "## Input Shape",
            "## Analysis Framework",
            "## Stock-Exposure Assessment",
            "## Score Calibration",
            "## Confidence Calibration",
            "## Worked Examples",
            "## Common Failure Modes",
        ]
        positions = [prompt.find(marker) for marker in order]
        assert all(p >= 0 for p in positions), (
            f"Missing markers: {[m for m, p in zip(order, positions) if p < 0]}"
        )
        assert positions == sorted(positions), f"Out of order: {positions}"

    def test_news_base_references_three_layers(self):
        """The base prompt explicitly names the three input layers."""
        prompt = compose_system_prompt("news").lower()
        assert "market" in prompt
        assert "sector" in prompt
        assert "stock-specific" in prompt


class TestNewsPromptStalePhraseRegression:
    """Prevent known-stale phrases from reappearing in the news base prompt."""

    _STALE_PHRASES = [
        "absence of news is mildly positive",  # old heuristic that misfires
        "press releases",                       # old per-stock content
        "SEC investigation",                    # old red-flag list
        "Tim Cook",                             # old worked-example reference
        "antitrust probe",                      # old worked-example reference
    ]

    @pytest.mark.parametrize("phrase", _STALE_PHRASES)
    def test_base_news_does_not_contain_stale_phrase(self, phrase):
        prompt = compose_system_prompt("news")
        assert phrase.lower() not in prompt.lower(), (
            f"Stale phrase '{phrase}' still present in news base prompt"
        )
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd /Users/franco_lu/Desktop/ai-hedgefund-final
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestNewsPromptStructure tests/unit/equities/test_skill_loader.py::TestNewsPromptStalePhraseRegression -v
```

Expected: FAIL. `TestNewsPromptStructure::test_news_base_contains_new_section_markers` will fail because current news.md has `## How to Reason` (not `## Input Shape`), no `## Stock-Exposure Assessment`, etc. `TestNewsPromptStalePhraseRegression` tests will fail because current news.md contains "Absence of news is mildly positive", "press releases", "SEC investigation", and "Tim Cook".

- [ ] **Step 1.3: Rewrite `app/modules/equities/agents/skills/base/news.md`**

Replace the entire contents of `app/modules/equities/agents/skills/base/news.md` with:

```markdown
# News Analyst

## Role

You are a macro/sector analyst producing a stock-specific bullish outlook over the next 1–3 months. You reason from broad market conditions down through sector conditions to this stock's exposure, translating the environment into a 1–10 bullish score and 1–10 confidence for the specific stock you are evaluating.

## Critical Reminders

- Do not assume a broad macro signal applies uniformly to every stock. A tech sector rally helps different constituents very differently.
- Do not ignore the stock-specific layer when it has content. A concrete stock-specific event is a stronger signal than inferred macro effects.
- Do not default to 5 with low confidence as a cop-out. When macro signals are clear, pick a side with honest confidence — even if stock exposure is inferred rather than directly observed.

## Input Shape

Your context contains three layers of news, organized chronologically in time-bucketed tables:

1. **Market layer** — broad-market articles covering the aggregate equity market, rate/monetary policy, risk appetite, and macro conditions. These characterize the environment in which every stock operates.
2. **Sector layer** — articles covering the stock's sector (e.g., Technology, Financial Services). These characterize conditions specific to this sector: outperformance vs the market, visible sub-sector themes (e.g., AI infrastructure, cloud migration, rate-sensitive financials), structural narratives.
3. **Stock-specific layer** — articles about this specific stock. Includes any earnings filings (with EPS vs prior year) and any manually-curated articles tagged to the ticker. This layer is often sparse — many rebalances will have no stock-specific articles, and that is expected.

The three layers are interleaved in the rendered tables by time bucket ("Last 7 Days", "Last 30 Days", "Older"). Use the headlines and context to attribute each article to the correct layer. Your job is to reason down the hierarchy — market to sector to stock — producing a stock-specific score that reflects how the environment translates to this particular stock.

## Analysis Framework

Work through these steps in order:

1. **Characterize the macro environment** from market-layer articles. Is the market in a bull or bear regime? Risk-on or risk-off? Are rates signaled to rise, fall, or hold? Is inflation signaled to run hot, cool, or stable? Note the direction and strength.

2. **Characterize the sector environment** from sector-layer articles. Is the sector outperforming or underperforming the market? What sub-sector themes are visible in the news flow (e.g., "AI infrastructure spending", "rising net interest margins", "commodity cycle")? Any structural narratives (secular growth, secular decline)?

3. **Assess this stock's exposure** to the macro and sector signals. Use the ticker, company name, and sector as anchors. Your prior knowledge of the company is valid input here — what does the company do, what are its revenue drivers, how is it positioned within its sector, how sensitive is it to rates and risk appetite? A rising-rate environment hurts long-duration growth assets more than financials; an AI-infrastructure rally helps NVDA more than legacy IBM.

4. **Check the stock-specific layer.** If there are earnings filings or articles tagged to this ticker, read them carefully. Stock-specific signals are stronger than macro inference — a concrete earnings miss outweighs an inferred sector tailwind.

5. **Synthesize** into a stock-specific bullish score. Weight the layers by their signal strength: strong macro signal with clear stock exposure → score reflects the environmental tilt with moderate-to-high confidence. Stock-specific event present → stock-specific dominates, macro/sector provide supporting context. Mixed, weak, or contradictory signals → lower confidence regardless of the score.

## Stock-Exposure Assessment

When mapping macro and sector signals to stock-level impact, use these heuristics:

- **Rate sensitivity**: falling rates favor long-duration growth assets; rising rates favor near-term cash flows and benefit financials through higher net interest margins.
- **Sector breadth**: a sector rally does not help every stock equally. Core thematic beneficiaries move more than peripheral names; a "tech sector up on AI" headline helps NVDA more than a traditional enterprise software incumbent.
- **Sub-sector themes**: narrow themes (e.g., "AI infrastructure spending", "GLP-1 drugs", "onshoring capex") help pure-plays more than conglomerates.
- **Company prior knowledge is valid input.** You know what most listed companies do, what their revenue mix looks like, and how they are positioned in their sector. Use that knowledge to reason about exposure.

## Score Calibration

| Score | Label | Criteria |
|-------|-------|----------|
| 1-2 | Strong Sell | Macro or sector headwinds specifically hit this stock's exposure. Stock-specific negative catalyst if present. Very bearish outlook over the next 1–3 months. |
| 3-4 | Bearish | Macro and/or sector conditions unfavorable for this stock's exposure. No offsetting stock-specific positives. |
| 5 | Neutral | Mixed or weak macro signals; stock exposure unclear; no stock-specific events. Honest neutrality — pick this when the environment genuinely gives no clear read for this stock. |
| 6-7 | Bullish | Macro and sector tailwinds align with this stock's exposure. Moderately bullish outlook over 1–3 months. |
| 8-9 | Strong Buy | Macro tailwind + sector tailwind + stock exposure aligned, ideally with a confirming stock-specific event. High-conviction bullish. |
| 10 | Extreme Conviction | All three layers align with unusual strength — a powerful macro shift, a strong sector theme, and a direct stock-specific catalyst all pointing the same direction. Very rare. |

## Confidence Calibration

| Level | Criteria |
|-------|----------|
| 1-3 | Macro signals weak, mixed, or contradictory; stock exposure ambiguous; no stock-specific events. Score is tentative. |
| 4-6 | Moderate macro signal with inferred sector alignment; stock exposure plausibly mapped but not directly confirmed. Score reflects reasoned inference rather than direct evidence. |
| 7-8 | Clear macro signal + clear sector alignment + stock exposure explicit and well-reasoned. Or: rich stock-specific layer present and consistent with macro/sector. |
| 9-10 | All three layers align strongly and independently. Multiple corroborating signals with no contradictory evidence. Rare. |

## Worked Examples

### Example A — Sparse stock-specific layer (the common case today)

**Input:**
- Market articles: "Fed signals rate cuts later this year", "S&P 500 up 3% over the past month on easing cycle expectations"
- Sector articles (Technology): "Technology sector leads broad market by 2%", "AI spending forecasts raised for 2026"
- Stock-specific (NVDA): no articles

**Reasoning:**
1. Macro: risk-on, with a rate-cut tailwind supporting growth multiples.
2. Sector: Technology outperforming on a secular theme (AI infrastructure).
3. Stock exposure: NVDA is a core AI beneficiary — GPU revenue directly tied to AI infrastructure spending.
4. Stock-specific: nothing directly observable.
5. Synthesis: macro and sector tailwinds both align strongly with NVDA's exposure. Score should reflect that. Confidence is moderate because the stock-specific layer is empty — the reasoning is inferential.

**Result:** bullish_score: 7, confidence: 6

### Example B — Rich stock-specific layer

**Input:**
- Market articles: same as Example A (rate-cut tailwind, broad-market rally)
- Sector articles (Technology): same as Example A (sector outperforming, AI theme)
- Stock-specific (INTC): "Intel reports Q4 EPS miss vs prior year", "Intel cuts dividend", "Intel announces 15% workforce reduction"

**Reasoning:**
1. Macro and sector: same bullish environment as Example A.
2. Stock exposure: INTC is a technology stock in a rallying sector — the macro environment would otherwise be bullish.
3. Stock-specific: concrete earnings miss, dividend cut, and restructuring all point to direct negative signals.
4. Synthesis: stock-specific headwinds are concrete and recent; they outweigh the inferred macro/sector tailwinds. An earnings miss and dividend cut is a direct negative signal far stronger than sector-level momentum.

**Result:** bullish_score: 3, confidence: 7

## Common Failure Modes

- Do not assume a broad market signal applies uniformly to every stock. A rally driven by AI beneficiaries does not lift legacy tech incumbents equally.
- Do not over-weight a single macro article. Look for consistent signal across multiple articles and across the market and sector layers.
- Do not default to 5 with low confidence when the macro signal is clear but stock exposure is ambiguous. Pick a side with honest confidence — if the environment is clearly bullish, a stock with average exposure deserves a 6, not a 5/low-confidence cop-out.
- Do not ignore the stock-specific layer when it has content. Earnings filings and explicitly-tagged articles are stronger signals than inferred macro effects.
- Do not treat "absence of stock-specific articles" as a negative or positive signal on its own. Today it is the default state and simply means you are reasoning from macro/sector inference.
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py -v
```

Expected: all tests pass, including the new `TestNewsPromptStructure` and `TestNewsPromptStalePhraseRegression` classes, and the existing `TestCriticalReminders` class (because the new base prompt keeps `## Critical Reminders` before `## Analysis Framework`).

- [ ] **Step 1.5: Run the full test suite to catch regressions**

```bash
.venv/bin/pytest tests/unit/ -q
```

Expected: all unit tests pass. The only file changed is the base news prompt; overlays are unchanged, so overlay-dependent tests still pass against the (old) overlays. Full-suite pass count should increase by the number of new tests (3 structure tests + 5 parameterized stale-phrase tests = 8 new tests).

- [ ] **Step 1.6: Lint and format**

```bash
.venv/bin/ruff check tests/unit/equities/test_skill_loader.py
.venv/bin/ruff format tests/unit/equities/test_skill_loader.py
```

- [ ] **Step 1.7: Commit**

```bash
git add app/modules/equities/agents/skills/base/news.md tests/unit/equities/test_skill_loader.py
git commit -m "refactor(news-prompt): rewrite base prompt around three-layer input

Replaces the per-stock-article framing with an 8-section structure
organized around Market / Sector / Stock-specific layers. The prompt
now teaches the LLM to reason down the hierarchy, accommodating the
sparse stock-specific layer today and a richer one in the future.

Two regression tests prevent stale per-stock phrasing (absence-is-positive
heuristic, Tim Cook worked example, SEC-investigation red flag) from
reappearing.

Part of news skill prompt redesign."
```

---

## Task 2: Rewrite growth branch overlay

**Files:**
- Rewrite: `app/modules/equities/agents/skills/branches/growth/news.md`
- Modify: `tests/unit/equities/test_skill_loader.py` (append growth overlay tests)

- [ ] **Step 2.1: Write the failing tests**

Append to `tests/unit/equities/test_skill_loader.py`:

```python
# ---------------------------------------------------------------------------
# News overlay structural tests (post-2026-04-16 redesign)
# ---------------------------------------------------------------------------


class TestNewsGrowthOverlayStructure:
    """The rewritten growth overlay uses thesis + layer-guidance structure."""

    def test_growth_overlay_has_investment_thesis_section(self):
        prompt = compose_system_prompt("news", "growth")
        assert "# Growth Branch" in prompt
        assert "## Investment Thesis" in prompt

    def test_growth_overlay_has_layer_guidance_section(self):
        prompt = compose_system_prompt("news", "growth")
        assert "## Reading the Three Layers" in prompt

    def test_growth_overlay_has_stock_exposure_emphasis(self):
        prompt = compose_system_prompt("news", "growth")
        assert "## Stock-Exposure Emphasis" in prompt

    def test_growth_overlay_has_signal_strength_section(self):
        prompt = compose_system_prompt("news", "growth")
        assert "## Signals That Warrant Strong Conviction" in prompt


class TestNewsGrowthOverlayNoMechanicalModifiers:
    """The overlay must not prescribe numeric score offsets.

    The old overlay had lines like '+1', '-2' in bold. The new design
    trusts the LLM to judge magnitude based on the base prompt's Score
    Calibration. This regression test ensures the old mechanical pattern
    does not reappear.
    """

    def test_growth_overlay_no_bold_numeric_modifiers(self):
        """No **+N** or **-N** pattern anywhere in the growth overlay."""
        import re
        prompt = compose_system_prompt("news", "growth")
        # Narrow regex: match **+1**, **+ 2**, **-2**, etc. — the exact
        # formatting the old overlay used. Avoids false positives on
        # legitimate content like "rising rates by 50bps" or "10-year yield".
        pattern = re.compile(r"\*\*\s*[+-]\s*\d+\s*\*\*")
        matches = pattern.findall(prompt)
        assert not matches, f"Old mechanical-modifier pattern found: {matches}"
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestNewsGrowthOverlayStructure tests/unit/equities/test_skill_loader.py::TestNewsGrowthOverlayNoMechanicalModifiers -v
```

Expected: FAIL. Current growth overlay has `## Investment Context`, `## How This Changes Your Analysis`, and `## Score Modifiers` sections (with `**+1**` / `**-2**` patterns) — not the new structure.

- [ ] **Step 2.3: Rewrite `app/modules/equities/agents/skills/branches/growth/news.md`**

Replace the entire contents with:

```markdown
# Growth Branch: News Overlay

## Investment Thesis

Growth investors pay up for future earnings. Returns depend on two things happening together: continued earnings expansion AND the market maintaining or expanding the multiple it assigns those earnings. This makes growth investing especially sensitive to:

- The discount-rate environment — falling rates lift the present value of long-duration cash flows, supporting higher multiples.
- Evidence of secular growth drivers versus cyclical bounces — secular themes (AI, cloud migration, energy transition) are load-bearing for growth theses in a way that cyclical rebounds are not.
- The market's risk appetite — risk-on environments allow multiples to expand; risk-off environments force the market to demand near-term cash flows, which growth stocks trade at a disadvantage for.

## Reading the Three Layers

When reading the three input layers through a growth-branch lens:

- **Market layer**: weight discount-rate conditions and monetary-policy signals heavily. Rate direction is often more predictive for growth than other macro axes. Risk-on versus risk-off posture is the next most important axis. Evidence of broad-market multiple expansion is a tailwind.
- **Sector layer**: distinguish secular tailwinds from cyclical bounces. A technology sector rally driven by "AI infrastructure spending raised" or "cloud migration accelerating" is a different signal than one driven by a rebound from oversold conditions. Secular themes support growth theses; cyclical rebounds are less reliable.
- **Stock-specific layer**: prioritize forward-looking content — product launches, major enterprise wins, TAM expansion announcements, raised revenue or earnings guidance. Backward-looking signals (dividend initiations, buybacks from cash-rich but slow-growing companies) may actually be mild negatives in a growth portfolio — they can signal management believes the growth runway is narrowing.

## Stock-Exposure Emphasis

Within a rallying sector, scores should skew more bullish for core secular beneficiaries than for incumbents being disrupted by the same theme. A tech sector rally driven by AI helps NVDA and hyperscalers more than it helps legacy enterprise IT; a cloud-migration theme helps hyperscalers more than it helps traditional infrastructure vendors. The growth branch's edge is identifying which stocks are the sharpest exposures to a given macro or sector signal, not treating a broad rally as uniformly bullish.

## Signals That Warrant Strong Conviction

**Strongly bullish — score toward the high end of the calibration scale:**
- A dovish Fed pivot with the market visibly repricing (rate cuts getting pulled forward)
- Sector outperformance driven by a secular theme the stock directly embodies
- Stock-specific: major product launch, enterprise win with a named customer of meaningful size, or raised guidance

**Strongly bearish — score toward the low end:**
- A hawkish surprise (hike or hawkish Fed speak) with the market repricing
- Rotation OUT of growth sectors into value, defensives, or cash
- Stock-specific: guidance cut, growth-slowdown signal, or evidence that the secular thesis the stock depends on is eroding

The strength of the underlying signal determines the magnitude of the score move. A widely-corroborated hawkish surprise warrants a sharp bearish tilt; a single ambiguous rate-hike speculation article does not. Judge magnitude by signal strength and corroboration across the three layers.
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestNewsGrowthOverlayStructure tests/unit/equities/test_skill_loader.py::TestNewsGrowthOverlayNoMechanicalModifiers -v
```

Expected: all 5 tests pass (4 structure + 1 modifier regression).

- [ ] **Step 2.5: Run the full skill loader test file**

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py -v
```

Expected: all tests pass. The existing `test_branch_overlay_included_for_growth` test still passes because the new overlay still contains `"Growth Branch"`.

- [ ] **Step 2.6: Lint and commit**

```bash
.venv/bin/ruff check tests/unit/equities/test_skill_loader.py
.venv/bin/ruff format tests/unit/equities/test_skill_loader.py

git add app/modules/equities/agents/skills/branches/growth/news.md tests/unit/equities/test_skill_loader.py
git commit -m "refactor(news-prompt): rewrite growth overlay as thesis-based lens

Replaces the old Score Modifiers table (+1/-2 numeric offsets) with
investment thesis + layer-specific reading guidance + signal-strength
anchors. The LLM judges magnitude based on signal strength and
corroboration, using the base prompt's Score Calibration as the anchor.

A regression test enforces no bold numeric modifiers (**+1**, **-2**)
appear in the overlay, preventing the old mechanical pattern from
being reintroduced.

Part of news skill prompt redesign."
```

---

## Task 3: Rewrite value branch overlay

**Files:**
- Rewrite: `app/modules/equities/agents/skills/branches/value/news.md`
- Modify: `tests/unit/equities/test_skill_loader.py` (append value overlay tests)

- [ ] **Step 3.1: Write the failing tests**

Append to `tests/unit/equities/test_skill_loader.py`:

```python
class TestNewsValueOverlayStructure:
    """The rewritten value overlay uses thesis + layer-guidance structure."""

    def test_value_overlay_has_investment_thesis_section(self):
        prompt = compose_system_prompt("news", "value")
        assert "# Value Branch" in prompt
        assert "## Investment Thesis" in prompt

    def test_value_overlay_has_layer_guidance_section(self):
        prompt = compose_system_prompt("news", "value")
        assert "## Reading the Three Layers" in prompt

    def test_value_overlay_has_stock_exposure_emphasis(self):
        prompt = compose_system_prompt("news", "value")
        assert "## Stock-Exposure Emphasis" in prompt

    def test_value_overlay_has_signal_strength_section(self):
        prompt = compose_system_prompt("news", "value")
        assert "## Signals That Warrant Strong Conviction" in prompt


class TestNewsValueOverlayNoMechanicalModifiers:
    def test_value_overlay_no_bold_numeric_modifiers(self):
        import re
        prompt = compose_system_prompt("news", "value")
        pattern = re.compile(r"\*\*\s*[+-]\s*\d+\s*\*\*")
        matches = pattern.findall(prompt)
        assert not matches, f"Old mechanical-modifier pattern found: {matches}"
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestNewsValueOverlayStructure tests/unit/equities/test_skill_loader.py::TestNewsValueOverlayNoMechanicalModifiers -v
```

Expected: FAIL. Current value overlay has `## Investment Context`, `## How This Changes Your Analysis`, `## Score Modifiers` sections (with `**+2**` / `**-2**` patterns).

- [ ] **Step 3.3: Rewrite `app/modules/equities/agents/skills/branches/value/news.md`**

Replace the entire contents with:

```markdown
# Value Branch: News Overlay

## Investment Thesis

Value investors pay less than intrinsic value for existing cash flows. Returns depend on mean reversion in valuation, an explicit re-rating catalyst, or reliable capital return via dividends and buybacks — plus avoiding value traps (businesses in structural decline where the apparent discount is earned). This makes value investing especially sensitive to:

- Cash flow quality and visibility — the underlying business must be sound and generating predictable cash, not just cheap on a multiple basis.
- Catalysts that force the market to re-evaluate an underpriced asset — insider buying, activist involvement, material capital-return announcements.
- Rotation dynamics — value tends to underperform in growth-euphoria environments and outperform when investors rotate toward near-term cash flows (often during rising-rate or risk-off periods).

## Reading the Three Layers

When reading the three input layers through a value-branch lens:

- **Market layer**: weight rotation signals heavily. Rising rates favor near-term cash flows (value tailwind); a clear growth-to-value rotation in market commentary is a direct signal. Flight to quality during risk-off episodes tends to help established value franchises. Rapid risk-on rallies and falling rates drive investors away from value into long-duration growth.
- **Sector layer**: traditional value sectors (financials, energy, industrials, utilities, materials) have distinct drivers — rising net interest margins for financials, commodity price strength for energy and materials, capex cycles for industrials. Watch carefully for structural-decline narratives dominating sector news: these are the seeds of value traps where the sector is cheap because its terminal value is eroding.
- **Stock-specific layer**: prioritize concrete re-rating catalysts — insider buying (especially CEO/CFO personal share purchases), activist investor involvement, dividend increases, buybacks executed at depressed valuations, analyst upgrades from sell or underperform ratings.

## Stock-Exposure Emphasis

Within sector tailwinds, favor established franchises with durable cash flows over speculative or distressed plays in the same sector. A value thesis requires the underlying business to be sound; cheap is not the same as broken. A financial sector rally driven by rising net interest margins helps established retail banks more than it helps speculative regional banks in trouble; an energy sector rally on commodity strength helps major integrated producers more than it helps distressed high-cost exploration companies.

## Signals That Warrant Strong Conviction

**Strongly bullish — score toward the high end of the calibration scale:**
- Insider buying with personal funds — this is the strongest conviction signal available, because insiders are betting their own capital on the business being undervalued.
- An activist investor taking a meaningful position with a public thesis
- Dividend raise or a buyback announcement at a clearly depressed valuation
- Macro rotation INTO the stock's sector with a clear underlying driver (rate-normalization tailwind for financials, commodity cycle for energy, onshoring capex for industrials)

**Strongly bearish — score toward the low end:**
- Dividend cut — directly destroys the capital-return thesis, and usually signals deeper business distress
- A structural-decline narrative dominating sector news (not a cyclical headwind but secular erosion of the business model)
- A growth-euphoria melt-up environment where long-duration assets are rallying and value is being left behind

The strength of the underlying signal determines the magnitude of the score move. A major insider buy corroborated by activist positioning warrants sharp bullishness; a single small insider purchase is a weaker signal. Judge magnitude by the strength of the underlying evidence and its corroboration across the three layers.
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestNewsValueOverlayStructure tests/unit/equities/test_skill_loader.py::TestNewsValueOverlayNoMechanicalModifiers -v
```

Expected: all 5 tests pass.

- [ ] **Step 3.5: Run the full skill loader test file**

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py -v
```

Expected: all tests pass.

- [ ] **Step 3.6: Lint and commit**

```bash
.venv/bin/ruff check tests/unit/equities/test_skill_loader.py
.venv/bin/ruff format tests/unit/equities/test_skill_loader.py

git add app/modules/equities/agents/skills/branches/value/news.md tests/unit/equities/test_skill_loader.py
git commit -m "refactor(news-prompt): rewrite value overlay as thesis-based lens

Mirrors the growth overlay's structure: investment thesis, layer-specific
reading guidance, stock-exposure emphasis, and signal-strength anchors.
Replaces the old Score Modifiers table with qualitative guidance that
the LLM translates to score magnitude based on signal strength and
corroboration.

Part of news skill prompt redesign."
```

---

## Task 4: Extend news analyst test for new prompt markers

**Files:**
- Modify: `tests/unit/equities/test_news_analyst.py`

The existing `test_branch_name_selects_overlay` test checks that `"News Analyst"` and `"Growth Branch"` appear in the composed system prompt. Extend it to also check for a marker unique to the new base prompt — confirming the rewritten prompt is actually being sent to the LLM, not a stale cached version.

- [ ] **Step 4.1: Read the current test**

```bash
cat tests/unit/equities/test_news_analyst.py | grep -A 15 "test_branch_name_selects_overlay"
```

Verify the test currently reads:

```python
async def test_branch_name_selects_overlay(self):
    """When branch_name is set, invoke() receives a system_prompt kwarg."""
    analyst, llm_client = _make_analyst()
    analyst.branch_name = "growth"
    await analyst.analyze(_make_stock(sector="Technology"), articles=_make_articles())

    system_prompt = llm_client.invoke.call_args.kwargs["system_prompt"]
    assert "News Analyst" in system_prompt
    assert "Growth Branch" in system_prompt
```

- [ ] **Step 4.2: Update the test**

Replace that single `test_branch_name_selects_overlay` test method with:

```python
    async def test_branch_name_selects_overlay(self):
        """When branch_name is set, invoke() receives a system_prompt kwarg.

        Also verifies the rewritten 2026-04-16 base prompt is in use — if
        the prompt composition regresses or a stale cached string is sent,
        the new section markers will be absent and this test will fail.
        """
        analyst, llm_client = _make_analyst()
        analyst.branch_name = "growth"
        await analyst.analyze(_make_stock(sector="Technology"), articles=_make_articles())

        system_prompt = llm_client.invoke.call_args.kwargs["system_prompt"]
        assert "News Analyst" in system_prompt
        assert "Growth Branch" in system_prompt
        # Post-2026-04-16 redesign markers — confirm new prompts reach the LLM.
        assert "## Input Shape" in system_prompt
        assert "## Stock-Exposure Assessment" in system_prompt
        assert "## Investment Thesis" in system_prompt
```

- [ ] **Step 4.3: Run the test**

```bash
.venv/bin/pytest tests/unit/equities/test_news_analyst.py::TestNewsAnalyst::test_branch_name_selects_overlay -v
```

Expected: PASS.

- [ ] **Step 4.4: Run the full test suite**

```bash
.venv/bin/pytest tests/unit/ -q
```

Expected: all tests pass.

- [ ] **Step 4.5: Lint and commit**

```bash
.venv/bin/ruff check tests/unit/equities/test_news_analyst.py
.venv/bin/ruff format tests/unit/equities/test_news_analyst.py

git add tests/unit/equities/test_news_analyst.py
git commit -m "test(news-prompt): assert new prompt markers reach the LLM

Extends test_branch_name_selects_overlay to check that the rewritten
base prompt sections (Input Shape, Stock-Exposure Assessment) and the
rewritten growth overlay's Investment Thesis section are present in
the composed system_prompt argument passed to llm_client.invoke.

Catches: NewsAnalyst accidentally using a different prompt source,
or skill loader caching a stale version.

Part of news skill prompt redesign."
```

---

## Task 5: Final verification

**Files:** none (verification only)

- [ ] **Step 5.1: Full unit suite**

```bash
.venv/bin/pytest tests/unit/ -q
```

Expected: all tests pass. Count should increase by 22 over pre-task baseline (8 base-prompt tests + 5 growth overlay tests + 5 value overlay tests + 0 for task 4 modification).

- [ ] **Step 5.2: Lint**

```bash
.venv/bin/ruff check app/ tests/
```

Expected: `All checks passed!`.

- [ ] **Step 5.3: Quick composition sanity check**

```bash
.venv/bin/python -c "
from app.modules.equities.agents.skills.loader import compose_system_prompt, _load_output_format

# Clear caches so the test reads fresh files
compose_system_prompt.cache_clear()
_load_output_format.cache_clear()

for branch in ('growth', 'value'):
    prompt = compose_system_prompt('news', branch, 'Technology')
    print(f'=== news + {branch} branch, Technology sector ===')
    print(f'Length: {len(prompt)} chars')
    print(f'Has Input Shape: {\"## Input Shape\" in prompt}')
    print(f'Has three-layer reference: {all(s in prompt.lower() for s in [\"market\", \"sector\", \"stock-specific\"])}')
    print(f'Has Investment Thesis: {\"## Investment Thesis\" in prompt}')
    print()
"
```

Expected output: both growth and value report `Length: >3000 chars`, `Has Input Shape: True`, `Has three-layer reference: True`, `Has Investment Thesis: True`.

- [ ] **Step 5.4: Quick LLM sanity check (optional, requires ANTHROPIC_API_KEY)**

If the API key is configured, run one real LLM call through the new prompt to inspect behavior manually. This is a dev-time check, not a pytest fixture — do not commit any artifacts from this step.

```bash
[ -n "$ANTHROPIC_API_KEY" ] && .venv/bin/python <<'EOF'
import asyncio
import os
from app.modules.equities.agents.news_analyst import NewsAnalyst
from app.modules.equities.agents.llm_client import AnthropicAnalystClient
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import UniverseStock

async def main():
    analyst = NewsAnalyst(
        config=AnalystLLMConfig(),
        llm_client=AnthropicAnalystClient(model="claude-sonnet-4-6", temperature=0.3),
        branch_name="growth",
    )
    stock = UniverseStock(symbol="NVDA", company_name="Nvidia Corp", weight=0.07, sector="Technology")
    articles = [
        {"title": "Fed signals rate cuts later this year", "source": "Reuters", "published_at": "2026-04-14T10:00:00Z"},
        {"title": "S&P 500 up 3% on easing expectations", "source": "Bloomberg", "published_at": "2026-04-14T11:00:00Z"},
        {"title": "Technology sector leads broad market by 2%", "source": "WSJ", "published_at": "2026-04-14T12:00:00Z"},
        {"title": "AI spending forecasts raised for 2026", "source": "Bloomberg", "published_at": "2026-04-14T13:00:00Z"},
    ]
    signal = await analyst.analyze(stock, articles=articles)
    print(f"NVDA growth-branch signal: bullish_score={signal.bullish_score}, confidence={signal.confidence}")
    print(f"Summary: {signal.summary}")

asyncio.run(main())
EOF
```

Read the summary field. Expected: the LLM should articulate the three-layer reasoning (macro bullish + sector rallying + NVDA as AI beneficiary), producing a moderately bullish score (6-8) with moderate-to-high confidence. If the summary is generic or cites per-stock news that isn't in the input, the new prompt isn't taking effect.

This step produces no commits — it is a one-off dev sanity check.

- [ ] **Step 5.5: Review commits**

```bash
git log --oneline main..HEAD
```

Expected: 4 commits (one per Tasks 1–4).

- [ ] **Step 5.6: Summary**

Report to the user:
- Number of commits on the feature branch
- Unit test count and pass status
- Lint status
- Any sanity check observations from Step 5.4 if it was run

---

## Self-Review

**Spec coverage:**
- Three-layer base prompt rewrite → Task 1 ✓
- Branch overlays rewrite with thesis + layer guidance + no numeric modifiers → Tasks 2, 3 ✓
- Regression tests for stale phrasing → Task 1 ✓
- Regression tests for mechanical-modifier pattern → Tasks 2, 3 ✓
- Test that new prompt reaches the LLM → Task 4 ✓
- Existing test contracts preserved (Critical Reminders section, Growth/Value Branch markers, News Analyst header) — called out in constraints section and respected in all prompt content ✓
- No changes to loader, analyst, context formatter → File Plan section confirms ✓

**Placeholder scan:** Every code and content block is complete. No TBDs, TODOs, or "similar to Task N" references. Each task has the full file content inline.

**Type consistency:** Section markers used in tests match exactly what appears in the prompt content:
- `## Input Shape` in Task 1 test matches `## Input Shape` in Task 1 content ✓
- `## Stock-Exposure Assessment` in Task 1 test matches content ✓
- `## Investment Thesis` in Task 2 content matches Task 2 test ✓
- `# Growth Branch` header in Task 2 content satisfies existing `test_branch_overlay_included_for_growth` test ✓
- `# Value Branch` header in Task 3 content satisfies existing `test_branch_overlay_included_for_value` test ✓
- `## Critical Reminders` + `## Analysis Framework` ordering in Task 1 content satisfies existing `TestCriticalReminders` tests ✓
