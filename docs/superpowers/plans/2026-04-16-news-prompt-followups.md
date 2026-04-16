# News Prompt Follow-Up Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine the news skill prompts to address four follow-up items: prior-knowledge hallucination grounding, score-zone anchors in overlays, parallel-structure invariant, and three-layer-specific failure-mode warnings. Include mandatory 3-scenario LLM smoke validation.

**Architecture:** Incremental edits to three existing markdown prompt files + one new test class + extended existing structure tests. No changes to skill composition, analyst code, or context formatters.

**Tech Stack:** Markdown prompts, Python 3.12, pytest with `asyncio_mode = "auto"`, ruff.

**Spec:** `docs/superpowers/specs/2026-04-16-news-prompt-followups.md`

---

## File Plan

**Files modified (content edits):**
- `app/modules/equities/agents/skills/base/news.md` — add 3 Critical Reminders, 1 new sub-section under Stock-Exposure Assessment, 2 Common Failure Modes
- `app/modules/equities/agents/skills/branches/growth/news.md` — replace Signals That Warrant Strong Conviction section with zone-anchored 4-tier version
- `app/modules/equities/agents/skills/branches/value/news.md` — same replacement, value-branch content

**Files modified (test additions):**
- `tests/unit/equities/test_skill_loader.py` — add `TestOverlaysHaveParallelStructure`, extend overlay and base-prompt structure tests for new content

**Files unchanged:** loader.py, analyst code, graph, portfolio manager, context formatters, config.

---

## Task 1: Base prompt additions (Items 3 + 6)

**Files:**
- Modify: `app/modules/equities/agents/skills/base/news.md`
- Modify: `tests/unit/equities/test_skill_loader.py`

Three changes to the base prompt:
1. **Item 3**: Add "Grounding when using prior knowledge" sub-section to Stock-Exposure Assessment
2. **Item 6**: Add 2 bullets to Critical Reminders (macro-uniformity + sub-sector distinction)
3. **Item 3 + 6**: Add 1 bullet to Critical Reminders (prior-knowledge grounding reminder)
4. **Item 6**: Add 2 bullets to Common Failure Modes (regime-classifier collapse + article misattribution)

### Step 1.1: Write the failing tests

Append these test classes to the END of `tests/unit/equities/test_skill_loader.py`:

```python
# ---------------------------------------------------------------------------
# Item 3 + 6 follow-up tests (post-2026-04-16 refinements)
# ---------------------------------------------------------------------------


class TestBaseNewsPromptGrounding:
    """Item 3: base prompt has explicit grounding guidance for prior-knowledge use."""

    def test_base_has_grounding_subsection(self):
        prompt = compose_system_prompt("news")
        assert "Grounding when using prior knowledge" in prompt

    def test_grounding_requires_summary_disclosure(self):
        """The grounding section must instruct the LLM to name its prior knowledge in the summary."""
        prompt = compose_system_prompt("news")
        # Find the grounding section
        idx = prompt.find("Grounding when using prior knowledge")
        assert idx >= 0
        section = prompt[idx:idx + 1500]
        assert "summary" in section.lower()
        assert "confidence" in section.lower()

    def test_critical_reminders_mentions_prior_knowledge_grounding(self):
        """A Critical Reminders bullet must call out prior-knowledge grounding."""
        prompt = compose_system_prompt("news")
        reminders_start = prompt.find("## Critical Reminders")
        reminders_end = prompt.find("## Input Shape")
        reminders = prompt[reminders_start:reminders_end]
        assert "prior knowledge" in reminders.lower()


class TestBaseNewsPromptFailureModeWarnings:
    """Item 6: base prompt calls out three-layer-specific failure modes."""

    def test_critical_reminders_warns_against_uniform_macro_application(self):
        prompt = compose_system_prompt("news")
        reminders_start = prompt.find("## Critical Reminders")
        reminders_end = prompt.find("## Input Shape")
        reminders = prompt[reminders_start:reminders_end]
        # Strong warning: macro signals must be translated to stock-level impact
        assert "uniform" in reminders.lower() or "uniformly" in reminders.lower()

    def test_critical_reminders_warns_about_sub_sector_themes(self):
        prompt = compose_system_prompt("news")
        reminders_start = prompt.find("## Critical Reminders")
        reminders_end = prompt.find("## Input Shape")
        reminders = prompt[reminders_start:reminders_end]
        assert "sub-sector" in reminders.lower()

    def test_failure_modes_warns_about_regime_classifier_collapse(self):
        prompt = compose_system_prompt("news")
        fm_start = prompt.find("## Common Failure Modes")
        fm = prompt[fm_start:]
        assert "regime classifier" in fm.lower()

    def test_failure_modes_warns_about_article_classification(self):
        prompt = compose_system_prompt("news")
        fm_start = prompt.find("## Common Failure Modes")
        fm = prompt[fm_start:]
        # Tells the LLM to classify articles by scope before weighting
        assert "classify" in fm.lower()
        assert "scope" in fm.lower()
```

### Step 1.2: Run tests to verify they fail

```bash
cd /Users/franco_lu/Desktop/ai-hedgefund-final/.worktrees/news-prompt-followups
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestBaseNewsPromptGrounding tests/unit/equities/test_skill_loader.py::TestBaseNewsPromptFailureModeWarnings -v
```

Expected: FAIL. Current base prompt has no "Grounding when using prior knowledge" sub-section, no sub-sector warning in Critical Reminders, no "regime classifier" phrase in Common Failure Modes.

### Step 1.3: Edit the base prompt

Use the `Edit` tool (not `Write`) to make three targeted edits to `app/modules/equities/agents/skills/base/news.md`.

**Edit 1 — Critical Reminders:** Find the existing `## Critical Reminders` block (3 bullets) and replace it with:

```markdown
## Critical Reminders

- Do not assume a broad macro signal applies uniformly to every stock. A tech sector rally helps different constituents very differently.
- Do not ignore the stock-specific layer when it has content. A concrete stock-specific event is a stronger signal than inferred macro effects.
- Do not default to 5 with low confidence as a cop-out. When macro signals are clear, pick a side with honest confidence — even if stock exposure is inferred rather than directly observed.
- A macro signal is not a uniform modifier. Translate it into stock-level impact — a rate-cut tailwind does not help every stock in the universe equally, and a sector rally does not lift every constituent the same way.
- Distinguish sub-sector themes within a sector. An "AI infrastructure" rally does not lift every technology stock; a "net interest margin expansion" article does not help every financial. Identify which specific stocks actually benefit from the theme.
- When you rely on prior knowledge about a company, say so explicitly in your summary and lower your confidence if that knowledge cannot be corroborated by the articles or sector classification.
```

**Edit 2 — Stock-Exposure Assessment:** Find the existing `## Stock-Exposure Assessment` block (4 bullets), and replace it with the same content PLUS a new sub-section appended at the end:

```markdown
## Stock-Exposure Assessment

When mapping macro and sector signals to stock-level impact, use these heuristics:

- **Rate sensitivity**: falling rates favor long-duration growth assets; rising rates favor near-term cash flows and benefit financials through higher net interest margins.
- **Sector breadth**: a sector rally does not help every stock equally. Core thematic beneficiaries move more than peripheral names; a "tech sector up on AI" headline helps NVDA more than a traditional enterprise software incumbent.
- **Sub-sector themes**: narrow themes (e.g., "AI infrastructure spending", "GLP-1 drugs", "onshoring capex") help pure-plays more than conglomerates.
- **Company prior knowledge is valid input.** You know what most listed companies do, what their revenue mix looks like, and how they are positioned in their sector. Use that knowledge to reason about exposure.

### Grounding when using prior knowledge

When you reason about a company using your pretrained knowledge (e.g., "NVDA is a GPU supplier", "JPM is rate-sensitive retail banking"), name the specific piece of prior knowledge you are relying on in your summary. If that prior knowledge cannot be corroborated by the articles in your context or by the stock's sector classification, lower your confidence. For less-known tickers where you are uncertain about the company's business model or current positioning, default to sector-level reasoning and set confidence accordingly.
```

**Edit 3 — Common Failure Modes:** Find the existing `## Common Failure Modes` block (5 bullets), and replace it with the same content PLUS 2 new bullets appended:

```markdown
## Common Failure Modes

- Do not assume a broad market signal applies uniformly to every stock. A rally driven by AI beneficiaries does not lift legacy tech incumbents equally.
- Do not over-weight a single macro article. Look for consistent signal across multiple articles and across the market and sector layers.
- Do not default to 5 with low confidence when the macro signal is clear but stock exposure is ambiguous. Pick a side with honest confidence — if the environment is clearly bullish, a stock with average exposure deserves a 6, not a 5/low-confidence cop-out.
- Do not ignore the stock-specific layer when it has content. Earnings filings and explicitly-tagged articles are stronger signals than inferred macro effects.
- Do not treat "absence of stock-specific articles" as a negative or positive signal on its own. Today it is the default state and simply means you are reasoning from macro/sector inference.
- Do not apply macro sentiment uniformly across stocks in the same sector. If you give every Technology stock the same score on the same rebalance, the news analyst has collapsed into a regime classifier and is no longer a differentiated signal. Differentiation comes from stock-exposure reasoning, not from reading different articles.
- Classify each article by its scope before weighting. The time-bucketed tables ("Last 7 Days", etc.) do not label articles by scope (market / sector / stock-specific) — use the headline and content to infer which layer each article belongs to. A misclassified article gets weighted incorrectly.
```

### Step 1.4: Run tests to verify they pass

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py -v
```

Expected: all tests pass, including the new classes and all existing tests (the edits are additive — existing markers like `## Critical Reminders` and `## Stock-Exposure Assessment` are still present).

### Step 1.5: Full suite

```bash
.venv/bin/pytest tests/unit/ -q
```

Expected: baseline + 7 new tests passing.

### Step 1.6: Lint and commit

```bash
.venv/bin/ruff check tests/unit/equities/test_skill_loader.py
.venv/bin/ruff format tests/unit/equities/test_skill_loader.py

git add app/modules/equities/agents/skills/base/news.md tests/unit/equities/test_skill_loader.py
git commit -m "refactor(news-prompt): base prompt follow-ups for items 3 + 6

Item 3 (prior-knowledge grounding):
- Add 'Grounding when using prior knowledge' sub-section under
  Stock-Exposure Assessment, instructing the LLM to name its prior
  knowledge in summaries and lower confidence when uncorroborated.
- Add Critical Reminder bullet reinforcing the grounding rule.

Item 6 (three-layer-specific failure modes):
- Add two Critical Reminders: don't apply macro signals uniformly,
  distinguish sub-sector themes within a sector.
- Add two Common Failure Modes: regime-classifier collapse warning,
  article-scope-classification instruction.

Regression tests enforce that each new piece of guidance is present.

Part of news skill prompt follow-up refinements."
```

---

## Task 2: Growth overlay — zone-anchored Signals section (Item 4)

**Files:**
- Modify: `app/modules/equities/agents/skills/branches/growth/news.md`
- Modify: `tests/unit/equities/test_skill_loader.py`

Replace the existing Signals That Warrant Strong Conviction section (Strongly Bullish / Strongly Bearish only) with a 4-tier version that adds Moderately Bullish and Moderately Bearish zones, each tied to a specific range on the 1-10 calibration scale.

### Step 2.1: Write the failing tests

Append to `tests/unit/equities/test_skill_loader.py`:

```python
class TestNewsGrowthOverlayZoneAnchors:
    """Item 4: growth overlay's Signals section has zone anchors tied to the calibration scale."""

    def test_growth_overlay_has_moderately_bullish_zone(self):
        prompt = compose_system_prompt("news", "growth")
        assert "Moderately bullish" in prompt

    def test_growth_overlay_has_moderately_bearish_zone(self):
        prompt = compose_system_prompt("news", "growth")
        assert "Moderately bearish" in prompt

    def test_growth_overlay_anchors_strong_signals_to_calibration_zones(self):
        """Strong signals must reference specific calibration zones (8-9, 7-8, etc.)."""
        prompt = compose_system_prompt("news", "growth")
        # Find the signals section
        idx = prompt.find("## Signals That Warrant Strong Conviction")
        assert idx >= 0
        section = prompt[idx:]
        # Zone anchors: at least one reference to 8-9 (strongly bullish zone)
        # and one to 2-3 (strongly bearish zone)
        assert "8-9" in section, "Missing strong-bullish zone anchor (8-9)"
        assert "2-3" in section, "Missing strong-bearish zone anchor (2-3)"

    def test_growth_overlay_anchors_moderate_signals_to_calibration_zones(self):
        """Moderate signals must reference calibration zones (6-7 bullish, 4 bearish)."""
        prompt = compose_system_prompt("news", "growth")
        idx = prompt.find("## Signals That Warrant Strong Conviction")
        section = prompt[idx:]
        assert "6-7" in section, "Missing moderate-bullish zone anchor (6-7)"

    def test_growth_overlay_still_has_no_bold_numeric_modifiers(self):
        """Zone anchors must not reintroduce the old **+N**/**-N** mechanical pattern."""
        import re
        prompt = compose_system_prompt("news", "growth")
        pattern = re.compile(r"\*\*\s*[+-]\s*\d+\s*\*\*")
        matches = pattern.findall(prompt)
        assert not matches, f"Mechanical-modifier pattern reappeared: {matches}"
```

### Step 2.2: Run tests to verify they fail

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestNewsGrowthOverlayZoneAnchors -v
```

Expected: FAIL. Current growth overlay has only Strongly Bullish / Strongly Bearish sections, no Moderately tiers, no zone-number references like "6-7" or "2-3".

### Step 2.3: Rewrite the growth overlay's Signals section

Use `Edit` to replace the existing Signals section in `app/modules/equities/agents/skills/branches/growth/news.md`. Find the current section (from `## Signals That Warrant Strong Conviction` to the end of the file) and replace with:

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

### Step 2.4: Run tests to verify they pass

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestNewsGrowthOverlayZoneAnchors tests/unit/equities/test_skill_loader.py::TestNewsGrowthOverlayNoMechanicalModifiers -v
```

Expected: all pass — new zone tests AND the existing no-mechanical-modifier test (zones don't match the `**[+-]N**` pattern).

### Step 2.5: Full skill loader tests

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py -v
```

Expected: all pass. Existing `TestNewsGrowthOverlayStructure` still passes (Investment Thesis, Reading the Three Layers, Stock-Exposure Emphasis, Signals That Warrant Strong Conviction are all still present).

### Step 2.6: Full suite

```bash
.venv/bin/pytest tests/unit/ -q
```

Expected: baseline + 5 new tests passing.

### Step 2.7: Lint and commit

```bash
.venv/bin/ruff check tests/unit/equities/test_skill_loader.py
.venv/bin/ruff format tests/unit/equities/test_skill_loader.py

git add app/modules/equities/agents/skills/branches/growth/news.md tests/unit/equities/test_skill_loader.py
git commit -m "refactor(news-prompt): add zone anchors to growth overlay (item 4)

Expand the Signals That Warrant Strong Conviction section from two
tiers (strongly bullish / bearish) to four (adding moderately bullish /
bearish), each tied to a specific range on the 1-10 calibration scale
(8-9, 6-7, 2-3, 4).

Zone anchors tell the LLM WHAT RANGE a given finding belongs in on
the calibration scale — NOT how much to offset a base score. This
gives the LLM reference points for magnitude without reintroducing
the old mechanical +N/-N modifier pattern. The no-mechanical-modifier
regression test still passes.

Regression tests require specific zone anchors (8-9, 6-7, 2-3) to
be present in the signals section, preventing accidental regression
to vague guidance.

Part of news skill prompt follow-up refinements."
```

---

## Task 3: Value overlay — zone-anchored Signals section (Item 4)

**Files:**
- Modify: `app/modules/equities/agents/skills/branches/value/news.md`
- Modify: `tests/unit/equities/test_skill_loader.py`

Same pattern as Task 2, applied to the value overlay.

### Step 3.1: Write the failing tests

Append to `tests/unit/equities/test_skill_loader.py`:

```python
class TestNewsValueOverlayZoneAnchors:
    """Item 4: value overlay's Signals section has zone anchors tied to the calibration scale."""

    def test_value_overlay_has_moderately_bullish_zone(self):
        prompt = compose_system_prompt("news", "value")
        assert "Moderately bullish" in prompt

    def test_value_overlay_has_moderately_bearish_zone(self):
        prompt = compose_system_prompt("news", "value")
        assert "Moderately bearish" in prompt

    def test_value_overlay_anchors_strong_signals_to_calibration_zones(self):
        prompt = compose_system_prompt("news", "value")
        idx = prompt.find("## Signals That Warrant Strong Conviction")
        assert idx >= 0
        section = prompt[idx:]
        assert "8-9" in section, "Missing strong-bullish zone anchor (8-9)"
        assert "2-3" in section, "Missing strong-bearish zone anchor (2-3)"

    def test_value_overlay_anchors_moderate_signals_to_calibration_zones(self):
        prompt = compose_system_prompt("news", "value")
        idx = prompt.find("## Signals That Warrant Strong Conviction")
        section = prompt[idx:]
        assert "6-7" in section, "Missing moderate-bullish zone anchor (6-7)"

    def test_value_overlay_still_has_no_bold_numeric_modifiers(self):
        import re
        prompt = compose_system_prompt("news", "value")
        pattern = re.compile(r"\*\*\s*[+-]\s*\d+\s*\*\*")
        matches = pattern.findall(prompt)
        assert not matches, f"Mechanical-modifier pattern reappeared: {matches}"
```

### Step 3.2: Run tests to verify they fail

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestNewsValueOverlayZoneAnchors -v
```

Expected: FAIL.

### Step 3.3: Rewrite the value overlay's Signals section

Use `Edit` to replace the existing Signals section in `app/modules/equities/agents/skills/branches/value/news.md`. Find the current section (from `## Signals That Warrant Strong Conviction` to the end of the file) and replace with:

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

### Step 3.4: Run tests

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestNewsValueOverlayZoneAnchors -v
.venv/bin/pytest tests/unit/equities/test_skill_loader.py -v
```

Expected: both all pass.

### Step 3.5: Full suite

```bash
.venv/bin/pytest tests/unit/ -q
```

Expected: baseline + 5 new tests passing.

### Step 3.6: Lint and commit

```bash
.venv/bin/ruff check tests/unit/equities/test_skill_loader.py
.venv/bin/ruff format tests/unit/equities/test_skill_loader.py

git add app/modules/equities/agents/skills/branches/value/news.md tests/unit/equities/test_skill_loader.py
git commit -m "refactor(news-prompt): add zone anchors to value overlay (item 4)

Mirrors the growth overlay change: expand Signals That Warrant Strong
Conviction from two tiers (strongly bullish / bearish) to four (adding
moderately bullish / bearish), each tied to a specific range on the
1-10 calibration scale.

Same no-mechanical-modifier regression continues to pass — zone
anchors are not **+N**/**-N** offsets.

Part of news skill prompt follow-up refinements."
```

---

## Task 4: Parallel-structure invariant test (Item 5)

**Files:**
- Modify: `tests/unit/equities/test_skill_loader.py`

Add a single test that asserts both overlays have the same H2 section headers. Prevents future drift.

### Step 4.1: Write the test

Append to `tests/unit/equities/test_skill_loader.py`:

```python
class TestOverlaysHaveParallelStructure:
    """Item 5: growth and value overlays must have the same H2 section headers.

    Prevents drift when someone edits one overlay but forgets to mirror
    the change in the other.
    """

    def test_growth_and_value_overlay_sections_match(self):
        import re
        g_prompt = compose_system_prompt("news", "growth")
        v_prompt = compose_system_prompt("news", "value")

        # Extract H2 headers from the overlay portion. The overlay starts at
        # the branch header (# Growth Branch / # Value Branch) and ends at
        # the next --- separator that joins the output format layer.
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

### Step 4.2: Run the test

```bash
.venv/bin/pytest tests/unit/equities/test_skill_loader.py::TestOverlaysHaveParallelStructure -v
```

Expected: PASS immediately. After Tasks 2 and 3, both overlays have identical section structures: Investment Thesis, Reading the Three Layers, Stock-Exposure Emphasis, Signals That Warrant Strong Conviction.

If this test fails, one of the overlay rewrites produced drift — investigate which section names differ and fix.

### Step 4.3: Full suite

```bash
.venv/bin/pytest tests/unit/ -q
```

Expected: baseline + 1 new test passing.

### Step 4.4: Lint and commit

```bash
.venv/bin/ruff check tests/unit/equities/test_skill_loader.py
.venv/bin/ruff format tests/unit/equities/test_skill_loader.py

git add tests/unit/equities/test_skill_loader.py
git commit -m "test(news-prompt): enforce parallel structure between overlays (item 5)

Adds a test that extracts H2 section headers from the growth and value
overlay portions of the composed prompt and asserts they are identical.

Prevents structural drift when someone edits one overlay without
mirroring the change in the other — the two overlays are designed to
have the same 4-section shape (Investment Thesis / Reading the Three
Layers / Stock-Exposure Emphasis / Signals That Warrant Strong
Conviction), and this invariant catches accidental asymmetry.

Part of news skill prompt follow-up refinements."
```

---

## Task 5: Mandatory LLM smoke validation

**Files:** none (validation only, no commits)

Runs three specific LLM smoke checks to validate the prompt changes work as intended. This is NOT a pytest fixture — it is a dev-time verification that requires `ANTHROPIC_API_KEY`. Results are read and judged manually; no test artifacts get committed.

### Step 5.1: Verify ANTHROPIC_API_KEY is available

```bash
cd /Users/franco_lu/Desktop/ai-hedgefund-final/.worktrees/news-prompt-followups
[ -n "$ANTHROPIC_API_KEY" ] && echo "KEY_SET" || (test -f .env && set -a && . ./.env && set +a && [ -n "$ANTHROPIC_API_KEY" ] && echo "KEY_LOADED_FROM_ENV") || echo "NO_KEY"
```

Expected: `KEY_SET` or `KEY_LOADED_FROM_ENV`. If `NO_KEY`, stop — the validation cannot run without API access. If it reports `NO_KEY`, copy from the parent repo's `.env`:

```bash
cp ../../.env . 2>/dev/null && echo "Copied .env from parent tree"
```

### Step 5.2: Run Check 1 — Differentiation across Technology stocks

5 tech stocks reading identical market + sector articles. Expect scores to differentiate based on the LLM's stock-exposure reasoning.

```bash
set -a; source .env; set +a

.venv/bin/python <<'EOF' 2>&1
import asyncio
from app.modules.equities.agents.news_analyst import NewsAnalyst
from app.modules.equities.agents.llm_client import AnthropicAnalystClient
from app.modules.equities.agents.skills.loader import compose_system_prompt, _load_output_format
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import UniverseStock

# Bust any cached prompts so we get the freshly-edited content
compose_system_prompt.cache_clear()
_load_output_format.cache_clear()

STOCKS = [
    UniverseStock(symbol="NVDA", company_name="Nvidia Corp", weight=0.07, sector="Technology"),
    UniverseStock(symbol="MSFT", company_name="Microsoft Corp", weight=0.07, sector="Technology"),
    UniverseStock(symbol="AAPL", company_name="Apple Inc", weight=0.07, sector="Technology"),
    UniverseStock(symbol="IBM", company_name="International Business Machines", weight=0.02, sector="Technology"),
    UniverseStock(symbol="INTC", company_name="Intel Corp", weight=0.02, sector="Technology"),
]

ARTICLES = [
    {"title": "Fed signals rate cuts later this year", "source": "Reuters", "published_at": "2026-04-14T10:00:00Z"},
    {"title": "S&P 500 up 3% on easing expectations", "source": "Bloomberg", "published_at": "2026-04-14T11:00:00Z"},
    {"title": "Technology sector leads broad market by 2%", "source": "WSJ", "published_at": "2026-04-14T12:00:00Z"},
    {"title": "AI infrastructure spending forecasts raised for 2026", "source": "Bloomberg", "published_at": "2026-04-14T13:00:00Z"},
]

async def main():
    analyst = NewsAnalyst(
        config=AnalystLLMConfig(),
        llm_client=AnthropicAnalystClient(model="claude-sonnet-4-6", temperature=0.3),
        branch_name="growth",
    )
    print("=== Check 1: Differentiation across 5 Tech stocks ===\n")
    scores = []
    for stock in STOCKS:
        signal = await analyst.analyze(stock, articles=ARTICLES)
        print(f"{stock.symbol:6s}  score={signal.bullish_score:2d}  conf={signal.confidence:2d}")
        print(f"  Summary: {signal.summary[:300]}")
        print()
        scores.append(signal.bullish_score)
    spread = max(scores) - min(scores)
    print(f"\nScore spread: {spread} (target: >= 3)")
    print(f"All scores: {scores}")

asyncio.run(main())
EOF
```

**Success criterion:** score spread >= 3. All 5 stocks seeing the same articles should produce different scores based on exposure reasoning (NVDA and MSFT likely high from AI exposure; IBM and INTC lower). If the spread is <= 2, the prompt isn't producing enough differentiation — iterate on the zone anchors and Critical Reminders before proceeding.

### Step 5.3: Run Check 2 — Value-branch reasoning kicks in

Run on JPM and XOM with value overlay and a value-supportive macro scenario.

```bash
.venv/bin/python <<'EOF' 2>&1
import asyncio
from app.modules.equities.agents.news_analyst import NewsAnalyst
from app.modules.equities.agents.llm_client import AnthropicAnalystClient
from app.modules.equities.agents.skills.loader import compose_system_prompt, _load_output_format
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import UniverseStock

compose_system_prompt.cache_clear()
_load_output_format.cache_clear()

async def main():
    analyst = NewsAnalyst(
        config=AnalystLLMConfig(),
        llm_client=AnthropicAnalystClient(model="claude-sonnet-4-6", temperature=0.3),
        branch_name="value",
    )

    # Value-supportive macro scenario
    macro_articles = [
        {"title": "Fed raises rates 25bps, signals more hikes if inflation persists", "source": "Reuters", "published_at": "2026-04-14T10:00:00Z"},
        {"title": "Investors rotate from growth to value stocks on rising yields", "source": "Bloomberg", "published_at": "2026-04-14T11:00:00Z"},
    ]

    stocks = [
        (UniverseStock(symbol="JPM", company_name="JPMorgan Chase", weight=0.04, sector="Financial Services"),
         macro_articles + [{"title": "Banks report rising net interest margins on rate hikes", "source": "WSJ", "published_at": "2026-04-14T12:00:00Z"}]),
        (UniverseStock(symbol="XOM", company_name="Exxon Mobil", weight=0.03, sector="Energy"),
         macro_articles + [{"title": "Oil prices hold above $85 as supply tightens", "source": "Bloomberg", "published_at": "2026-04-14T12:00:00Z"}]),
    ]

    print("=== Check 2: Value-branch reasoning ===\n")
    for stock, articles in stocks:
        signal = await analyst.analyze(stock, articles=articles)
        print(f"{stock.symbol:5s} ({stock.sector})  score={signal.bullish_score:2d}  conf={signal.confidence:2d}")
        print(f"  Summary: {signal.summary[:400]}")
        print()

asyncio.run(main())
EOF
```

**Success criterion:** JPM summary explicitly mentions rate sensitivity / net interest margin dynamics. XOM summary mentions commodity / energy cycle dynamics. Both should be bullish (score >= 6) given value-supportive macro. If summaries are generic and don't cite sector-specific value drivers, the value overlay isn't influencing reasoning — iterate on the Reading the Three Layers section.

### Step 5.4: Run Check 3 — Obscure-ticker grounding behavior

Pick a less-known mid-cap name from the growth universe to test the Item 3 grounding guidance. A stock the LLM may not have strong prior knowledge about.

```bash
.venv/bin/python <<'EOF' 2>&1
import asyncio
from app.modules.equities.agents.news_analyst import NewsAnalyst
from app.modules.equities.agents.llm_client import AnthropicAnalystClient
from app.modules.equities.agents.skills.loader import compose_system_prompt, _load_output_format
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import UniverseStock

compose_system_prompt.cache_clear()
_load_output_format.cache_clear()

# Mid-cap-ish ticker the LLM may or may not know in detail
STOCK = UniverseStock(symbol="PODD", company_name="Insulet Corp", weight=0.005, sector="Healthcare")

ARTICLES = [
    {"title": "Fed signals rate cuts later this year", "source": "Reuters", "published_at": "2026-04-14T10:00:00Z"},
    {"title": "Healthcare sector trades flat against broad market", "source": "WSJ", "published_at": "2026-04-14T12:00:00Z"},
]

async def main():
    analyst = NewsAnalyst(
        config=AnalystLLMConfig(),
        llm_client=AnthropicAnalystClient(model="claude-sonnet-4-6", temperature=0.3),
        branch_name="growth",
    )
    print("=== Check 3: Obscure-ticker grounding ===\n")
    signal = await analyst.analyze(STOCK, articles=ARTICLES)
    print(f"{STOCK.symbol}  score={signal.bullish_score}  conf={signal.confidence}")
    print(f"Summary:\n{signal.summary}")

asyncio.run(main())
EOF
```

**Success criterion:** summary either (a) explicitly cites prior knowledge the LLM has about the company ("Insulet makes Omnipod insulin delivery systems") OR (b) acknowledges lack of direct company knowledge and defaults to sector-level reasoning with appropriately lower confidence. If the LLM confidently makes up specific business details without flagging uncertainty, the grounding guidance isn't working.

### Step 5.5: Decide whether to proceed

Review the three check outputs:

- **Check 1 spread >= 3, differentiation looks reasoned** → pass
- **Check 2 summaries cite value-specific drivers (rate sensitivity, commodity dynamics)** → pass
- **Check 3 either cites prior knowledge or acknowledges uncertainty** → pass

**If all 3 pass:** proceed to Step 5.6 (final verification commit).

**If any fail:** stop and report which check failed and what the summary said. Do NOT fix prompts reflexively — we may want to discuss whether to iterate or accept the limitation.

### Step 5.6: Final full-suite verification

```bash
.venv/bin/pytest tests/unit/ -q
.venv/bin/ruff check app/ tests/
```

Expected: all tests pass, lint clean.

### Step 5.7: Summary commit (optional)

If the smoke checks revealed observations worth recording but no prompt changes, a summary commit is not needed. If a small polish to the prompts was required based on smoke results, make those edits and commit them separately with a clear message about what the smoke check revealed.

---

## Self-Review

**Spec coverage:**
- Item 3 (grounding) → Task 1 adds grounding sub-section and Critical Reminder; tests enforce ✓
- Item 4 (zone anchors) → Tasks 2 and 3 add zone-anchored Signals sections to both overlays ✓
- Item 5 (parallel invariant) → Task 4 adds the invariant test ✓
- Item 6 (new failure modes) → Task 1 adds 2 new Critical Reminders and 2 new Common Failure Modes ✓
- Mandatory 3-scenario LLM smoke → Task 5 runs all 3 checks ✓
- No-mechanical-modifier regression preserved → Tasks 2 and 3 include `test_*_overlay_still_has_no_bold_numeric_modifiers` ✓
- Existing overlay structure tests (4 sections each) → still pass because all 4 existing section headers are preserved in the overlay rewrites ✓

**Placeholder scan:** No TBDs, TODOs, or "similar to Task N". Every step has concrete code, commands, or content.

**Type consistency:**
- Section headers match exactly between edit content and test assertions: `Grounding when using prior knowledge`, `Moderately bullish`, `Moderately bearish`, `6-7`, `8-9`, `2-3`, `4` all consistent ✓
- The parallel-structure test's split logic (`"# Growth Branch"` / `"# Value Branch"` / `"\n---\n"`) matches the loader's actual output (base prompt + overlay + `\n\n---\n\n` separator + output format) ✓
- `TestNewsGrowthOverlayNoMechanicalModifiers` from the prior change still applies after Task 2 rewrite (zone strings like `"8-9"` do not match `\*\*\s*[+-]\s*\d+\s*\*\*`) ✓
- `TestNewsPromptStructure` and `TestNewsPromptStalePhraseRegression` from the prior change still pass because Task 1 additions are purely additive (no removals of existing markers, no reintroduction of stale phrases) ✓
