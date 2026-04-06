# Analyst Skills System

## Problem Statement

The LLM analyst agents (fundamentals, technical, news) have minimal prompts that provide no guidance on **how** to analyze stocks. The context normalization layer (Phase 2.5) solved the data presentation problem — the LLM now sees clean markdown tables. But the prompt tells it nothing about analytical methodology.

**Current state:**

| Component | What it says | What's missing |
|-----------|-------------|----------------|
| **System prompt** (`llm_client.py:39-46`) | "You are a financial analyst assistant. Respond ONLY with JSON." | No analysis framework, no score calibration, no domain expertise |
| **User prompt** (each analyst) | "{context}\n\nBased on the data above, provide: bullish_score (1-10), confidence (1-10), summary (1-2 sentences)." | No guidance on what makes a good/bad score, no red flags to watch for, no industry benchmarks |
| **Branch differentiation** | None at analysis level | Growth and value branches use the same analyst prompts — only the screener differs |

**Impact:** Without score calibration anchors, the LLM clusters scores around 5-7 with no meaningful spread. Without analytical frameworks, it produces generic summaries. Without branch-specific guidance, a stock gets the same fundamental analysis whether it's being evaluated for a growth or value portfolio.

**Inspiration:** Production AI finance systems like Fintool use markdown "skill files" that encode domain expertise — industry-specific valuation methodologies, step-by-step analysis workflows, and guidelines written by actual analysts. "The model is not the product. The skills are the product."

## Solution: Composable Markdown Skills

Markdown files that encode analytical methodology, loaded at analysis time and injected into the system prompt. Three layers compose into a single prompt:

```
┌─────────────────────────────┐
│  Base Skill                 │  Analysis framework, score calibration,
│  (per analyst type)         │  red flags, confidence calibration
├─────────────────────────────┤
│  Branch Overlay             │  Growth vs value priority adjustments,
│  (growth or value)          │  modified score modifiers
├─────────────────────────────┤
│  Sector Overlay (optional)  │  Industry-specific benchmarks,
│  (e.g., technology)         │  sector-specific considerations
├─────────────────────────────┤
│  Output Format              │  JSON output instruction
│  (always appended last)     │  (hardcoded, not a file)
└─────────────────────────────┘
```

**Key properties:**
- Skills are **additive** — branch/sector overlays only contain deltas from the base
- **Missing overlays are silently skipped** — the system degrades gracefully to base + output format
- Skills go in the **system prompt** (not user prompt) — enables Anthropic prompt caching across all stocks sharing the same (analyst_type, branch, sector) tuple
- **No code deploys to refine methodology** — edit a markdown file, restart the server

### Architecture Position

```
                   Skills (loaded once, cached)
                   ┌──────────────┐
                   │ base/        │
                   │ branches/    │
                   │ sectors/     │
                   └──────┬───────┘
                          │ compose_system_prompt()
                          ▼
DataPlatformService    Analysts                    LLM Client
┌──────────────────┐   ┌────────────────────┐   ┌──────────────────┐
│ get_metrics()    │──>│ format_context()    │──>│ system: [skill]  │──> Claude
│ get_prices()     │   │ + compose_prompt()  │   │ user: [context]  │
│ get_news()       │   └────────────────────┘   └──────────────────┘
└──────────────────┘    context_formatters.py       llm_client.py
     (unchanged)             (unchanged)             (modified)
```

**Separation of concerns:**
- `context_formatters.py` handles **data presentation** (what the LLM sees)
- `skills/*.md` handles **analytical methodology** (how the LLM thinks)
- `llm_client.py` handles **LLM communication** (system prompt + user prompt + parsing)

## Directory Structure

```
app/modules/equities/agents/skills/
    __init__.py
    loader.py                          # compose_system_prompt(), lru_cache

    base/                              # One per analyst type (required)
        fundamentals.md
        technical.md
        news.md

    branches/                          # Branch-specific overlays
        growth/
            fundamentals.md
            technical.md
            news.md
        value/
            fundamentals.md
            technical.md
            news.md

    sectors/                           # Sector overlays (optional, Phase 2)
        technology.md                  # SaaS rule-of-40, R&D intensity
        financials.md                  # NIM, tangible book, different D/E norms
        healthcare.md                  # Pipeline value, patent cliffs
        energy.md                      # Commodity sensitivity, reserve ratios
```

**Rationale:** Skills live inside `agents/` because they are methodology instructions that are part of the agent's identity — not config, not data, not architecture docs.

## Skill File Format

Each skill file follows a standardized markdown structure. Not all sections are required — branch/sector overlays are deliberately smaller, containing only deltas.

### Base Skill Template

```markdown
# [Analyst Type] Analyst

## Role
[1-2 sentences defining the analyst's specialization and what they evaluate]

## Analysis Framework
[Ordered steps the analyst should follow — this is the core methodology]
1. Step one...
2. Step two...

## Score Calibration
| Score | Label | Criteria |
|-------|-------|----------|
| 1-2   | Strong Sell | [concrete characteristics] |
| 3-4   | Bearish | ... |
| 5     | Neutral | ... |
| 6-7   | Bullish | ... |
| 8-9   | Strong Buy | ... |
| 10    | Extreme | ... |

## Confidence Calibration
| Level | Criteria |
|-------|----------|
| 1-3   | [when to use low confidence] |
| 4-6   | ... |
| 7-9   | ... |
| 10    | ... |

## Red Flags
- [Specific pattern that should decrease score by 1-2 points]
- ...

## Common Pitfalls
- [Mistakes the LLM should avoid]
- ...
```

### Branch Overlay Template

```markdown
# [Branch] Branch: [Analyst Type] Overlay

## Investment Context
[What this branch optimizes for — growth acceleration vs undervaluation]

## Priority Adjustments
- [Metric]: [HIGH/MEDIUM/LOW] priority. [Reasoning].
- ...

## Score Modifiers
- [Specific condition]: +1 to base score
- [Specific condition]: -1 to base score
- ...
```

### Sector Overlay Template

```markdown
# [Sector] Sector Overlay

## Sector Benchmarks
| Metric | Good | Average | Poor |
|--------|------|---------|------|
| ... | ... | ... | ... |

## Sector-Specific Considerations
- [Industry-specific analysis guidance]
- ...
```

## Skill Loader

**New file:** `app/modules/equities/agents/skills/loader.py`

```python
def compose_system_prompt(
    analyst_type: str,          # "fundamentals" | "technical" | "news"
    branch_name: str = "",      # "growth" | "value" | "test_growth" | ...
    sector: str | None = None,  # GICS sector from UniverseStock
) -> str:
    """Compose system prompt by layering: base + branch + sector + output format.

    Cached via lru_cache — same args return the same string object.
    This is important for Anthropic prompt caching (hashes system content).
    """
```

**Composition logic:**

1. Read `base/{analyst_type}.md` — required, warns if missing
2. Strip `test_` prefix from `branch_name` → read `branches/{branch}/{analyst_type}.md` — optional
3. Normalize sector name → read `sectors/{sector_key}.md` — optional
4. Append shared `output_format.md` layer (JSON schema + pre-response checklist) — required, raises `MissingSkillError` if absent
5. Join all layers with `\n\n---\n\n` separators

**Caching:** `@lru_cache(maxsize=64)` — the same (analyst_type, branch_name, sector) tuple always returns the same string object. In a 20-stock batch with ~8 sectors, this means ~8 unique prompts cached and reused.

**Helper:** `get_available_skills() -> dict` — returns summary of discovered skill files (useful for diagnostics and testing).

## Changes to Existing Files

### `llm_client.py` — Add `system_prompt` Parameter

```python
async def invoke(self, prompt: str, *, system_prompt: str | None = None) -> dict:
```

**Changes:**
1. New keyword-only parameter `system_prompt: str | None = None`
2. When `None`, falls back to the current hardcoded system prompt (backward compatible)
3. System prompt sent as list format with `cache_control: {"type": "ephemeral"}`:
   ```python
   system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
   ```
4. `max_tokens` increased 256 → 512 (richer summaries referencing specific data points)

### Each Analyst — Add `branch_name` + Skill Composition

Pattern (same for all 3 analysts):

1. Add `branch_name: str = ""` to `__init__`
2. In `analyze()`, call `compose_system_prompt(self.ANALYST_TYPE, self.branch_name, stock.sector)`
3. Pass `system_prompt=system_prompt` to `self.llm_client.invoke()`
4. Simplify user prompt: `f"{context}\n\nAnalyze this stock based on the data above and your instructions."`

### `service.py` — Wire `branch_name` to Analysts

Add 3 lines at the top of `run_pipeline()`:

```python
for analyst in [self.news_analyst, self.fundamentals_analyst, self.technical_analyst]:
    if hasattr(analyst, "branch_name"):
        analyst.branch_name = branch_name
```

This is safe because `run_pipeline()` runs sequentially per branch — no concurrent access. The `hasattr` check ensures quantitative analysts (backtest mode) are unaffected.

### NOT Modified

- `context_formatters.py` — data presentation stays separate
- `dependencies.py` — no structural changes needed (`branch_name` defaults to `""`)
- `backtest/quantitative_analysts.py` — no LLM calls, not affected
- `config.py` — skills are files, not config values

## Prompt Caching Economics

With `cache_control: {"type": "ephemeral"}` on the system prompt:

| Scenario | Cache Misses | Cache Hits | Savings |
|----------|-------------|------------|---------|
| 20 stocks, ~8 sectors, 1 analyst | ~8 | ~12 | ~60% of system prompt tokens |
| 20 stocks, ~8 sectors, 3 analysts | ~24 | ~36 | ~60% of system prompt tokens |
| Same batch, next rebalance | 0 (5-min TTL) | ~60 | ~100% of system prompt tokens |

Prompt caching reduces latency by ~80% and cost by ~90% for cached requests. The skill content (~1500-2000 tokens per composed prompt) is the same across all stocks with the same (analyst, branch, sector), making it ideal for caching.

## Initial Skill Content (What Goes in Each File)

### Base Fundamentals (`base/fundamentals.md`)

- **Role**: Senior equity research analyst evaluating financial health and intrinsic value
- **Framework**: Valuation → Profitability → Growth quality → Balance sheet → Capital allocation → Earnings consistency
- **Score calibration**: 1-2 = deteriorating fundamentals + rising debt; 5 = fairly valued, mixed signals; 8-9 = excellent across multiple dimensions; 10 = extremely rare
- **Red flags**: Revenue growing but OCF declining, gross margin compression despite revenue growth, FCF significantly below net income, consecutive earnings misses
- **Key relationships**: P/E vs growth rate (PEG sanity check), FCF yield vs dividend yield (sustainability), revenue growth vs margin trends (quality)

### Base Technical (`base/technical.md`)

- **Role**: Quantitative technical analyst evaluating price action and momentum
- **Framework**: Trend identification (SMA alignment) → Momentum (RSI/MACD) → Volume confirmation → Support/resistance → Multi-timeframe returns
- **Score calibration**: 1-2 = below all SMAs + bearish MACD + weak volume; 5 = consolidation; 8-9 = above all SMAs + golden cross + volume confirmation; 10 = all signals aligned (extremely rare)
- **Key insight**: Volume is the "truth detector" — always check if volume confirms the move. SMA alignment matters more than any single indicator.

### Base News (`base/news.md`)

- **Role**: Sentiment analyst interpreting news flow impact over 1-3 months
- **Framework**: Classify each headline → Weight by recency (7d = 3x weight) → Weight by source quality → Assess event type → Sentiment balance
- **Score calibration**: 1-2 = SEC investigation, product recall, earnings disaster; 5 = no news or balanced mix; 8-9 = major positive catalyst; 10 = multiple simultaneous major catalysts (extremely rare)
- **Key insight**: Absence of news is mildly positive (no negative surprises). Single negative headline from quality source outweighs multiple generic positive articles.

### Growth Branch Overlays

- **Fundamentals**: Revenue growth >20% = highest priority. High P/E acceptable if PEG < 1.5. Negative FCF acceptable if revenue accelerating. Decelerating growth for 2+ quarters = major red flag (-2 to score).
- **Technical**: Favor momentum. Breakout above resistance with volume = strong bullish. Weight 3M+ returns more than mean-reversion signals.
- **News**: Weight product launches, TAM expansion, and analyst upgrades higher. De-emphasize dividend/buyback news.

### Value Branch Overlays

- **Fundamentals**: P/E below sector median = highest priority. FCF yield >5% = strong buy signal. Sustainable dividends with payout ratio <60% = bullish. Declining ROE 2+ periods = value trap warning.
- **Technical**: Favor mean-reversion signals. Oversold RSI (<30) near support = bullish. De-emphasize pure momentum.
- **News**: Weight insider buying, activist involvement, and re-rating catalysts higher. De-emphasize growth narrative news.

## Testing Strategy

### Unit Tests (`tests/unit/equities/test_skill_loader.py`)

- `test_base_only_when_no_branch_or_sector` — base + output format
- `test_branch_overlay_included` — growth/value overlays appended
- `test_sector_overlay_included` — sector overlay appended
- `test_all_three_layers` — base + branch + sector + output format
- `test_test_prefix_stripped` — `test_growth` → `growth`
- `test_missing_sector_graceful` — unknown sector silently skipped
- `test_caching_returns_same_object` — `lru_cache` identity check
- `test_all_analyst_types_have_base_skills` — all 3 base files exist and produce non-trivial prompts

### Updated Analyst Tests

- Verify `invoke()` called with `system_prompt` kwarg containing expected skill content
- Verify backward compatibility when `branch_name` defaults to `""`

### Updated LLM Client Tests

- Verify custom `system_prompt` replaces default
- Verify `None` falls back to legacy prompt
- Verify `cache_control` present in system parameter

### Verification Commands

```bash
# All unit tests pass
pytest tests/unit/ -q

# Skills are discoverable
python -c "from app.modules.equities.agents.skills.loader import get_available_skills; print(get_available_skills())"

# Composition works
python -c "from app.modules.equities.agents.skills.loader import compose_system_prompt; print(compose_system_prompt('fundamentals', 'growth')[:500])"

# Integration test (requires ANTHROPIC_API_KEY)
pytest tests/integration/ -m integration -v
```

## Implementation Sequence

1. Create skill directory structure + all 9 markdown skill files (base × 3 + branch overlays × 6)
2. Create `skills/loader.py` with `compose_system_prompt()` and `get_available_skills()`
3. Create `tests/unit/equities/test_skill_loader.py` and verify
4. Modify `llm_client.py` — add `system_prompt` param, prompt caching, increase max_tokens
5. Modify each analyst — add `branch_name`, call `compose_system_prompt`, simplify user prompt
6. Modify `service.py` — set `branch_name` on analysts in `run_pipeline()`
7. Update existing analyst + llm_client tests for new behavior
8. Run full test suite: `pytest tests/unit/ -q`
