# Per-Ticker News Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add company-specific Yahoo headlines to each screened stock's news context, scope-labeled and relevance-filtered, so the news analyst's ranking can carry per-stock information (spec: `docs/superpowers/specs/2026-07-17-per-ticker-news-design.md`).

**Architecture:** Extend the existing graph-level news prefetch with a sequential per-symbol company stage; add a new pure-function module (`news_scope.py`) for filtering, scope tagging, and merge/dedupe; switch `format_news_context` from recency buckets to scope-grouped sections; update the news skill to trust scope labels. No data-platform, backtest-engine, or ranker changes.

**Tech Stack:** Python 3.12, Pydantic, LangGraph, pytest (asyncio auto mode), ruff.

---

## MACHINE + PROCESS RULES (read before every task)

1. **Work in the worktree:** `/Users/franco_lu/Desktop/ai-hedgefund-final/.claude/worktrees/adaptive-weights-checkin-999f44`. The shell cwd RESETS to this worktree after background-task notifications — start every command with an explicit `cd` to the worktree. Never use bare relative paths across turns.
2. **Tooling comes from the main repo's venv** (the worktree has none):
   - pytest: `/Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/pytest` (bare `pytest` is a broken conda env — never use it)
   - ruff: `/Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/ruff`
   Run them with cwd = worktree so imports resolve to worktree code (verified working 2026-07-17: `test_screener.py` 5 passed).
3. **NEVER `git commit`.** The user commits themselves. Every task ends with `git add <files>` (stage only). This overrides the usual TDD-commit cadence.
4. **iCloud hazard:** if a file reads empty or an import breaks inexplicably, do NOT "fix" it — verify with `git show :<path>` and restore from the index. Delete any `<name> 2.<ext>` duplicate only after `cmp` against the original.
5. All pytest commands below are written as `PYTEST` — expand to the absolute path in rule 2. All `cd` mean the worktree path in rule 1.

---

### Task 1: Config knobs on `AgentsConfig`

**Files:**
- Modify: `app/modules/equities/config.py` (AgentsConfig, after `max_concurrent_analyses: int = 10`)
- Test: `tests/unit/equities/test_config.py`

- [ ] **Step 1: Write the failing test** — append to `tests/unit/equities/test_config.py`:

```python
class TestCompanyNewsConfig:
    def test_company_news_defaults(self):
        config = AgentsConfig()
        assert config.company_news_fetch_limit == 10
        assert config.company_news_prompt_cap == 6

    def test_company_news_overridable(self):
        config = AgentsConfig(company_news_fetch_limit=5, company_news_prompt_cap=3)
        assert config.company_news_fetch_limit == 5
        assert config.company_news_prompt_cap == 3
```

If `AgentsConfig` is not already imported at the top of the file, add it to the existing `from app.modules.equities.config import ...` import line.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_config.py -q -k company_news`
Expected: 2 FAILED (ValidationError or AttributeError — unknown field).

- [ ] **Step 3: Implement** — in `app/modules/equities/config.py`, inside `AgentsConfig`, directly after `max_concurrent_analyses: int = 10`:

```python
    # Per-ticker company news (2026-07-17 per-ticker news spec):
    # fetch_limit = raw articles requested per symbol from Yahoo;
    # prompt_cap = filtered company articles rendered per stock prompt.
    company_news_fetch_limit: int = 10
    company_news_prompt_cap: int = 6
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_config.py -q`
Expected: all pass (existing + 2 new).

- [ ] **Step 5: Lint + stage**

```bash
cd <worktree> && /Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/ruff check app/modules/equities/config.py tests/unit/equities/test_config.py
git add app/modules/equities/config.py tests/unit/equities/test_config.py
```

---

### Task 2: `news_scope.py` — name normalization + relevance filter

**Files:**
- Create: `app/modules/equities/agents/news_scope.py`
- Create: `tests/unit/equities/test_news_scope.py`

- [ ] **Step 1: Write the failing tests** — create `tests/unit/equities/test_news_scope.py`:

```python
"""Unit tests for news_scope: relevance filter, scope tagging, merge/dedupe."""

from app.modules.equities.agents.news_scope import (
    filter_company_articles,
    is_company_relevant,
    merge_and_dedupe,
    normalize_company_name,
    tag_scope,
)


class TestNormalizeCompanyName:
    def test_strips_trailing_legal_suffix(self):
        assert normalize_company_name("NVIDIA Corp") == "NVIDIA"
        assert normalize_company_name("Apple Inc.") == "Apple"
        assert normalize_company_name("Exxon Mobil Corporation") == "Exxon Mobil"

    def test_strips_leading_the(self):
        assert normalize_company_name("The Coca-Cola Company") == "Coca-Cola"

    def test_keeps_multiword_core(self):
        assert normalize_company_name("Bank of America Corp") == "Bank of America"

    def test_strips_share_class_then_suffix(self):
        assert normalize_company_name("Berkshire Hathaway Inc Class B") == "Berkshire Hathaway"

    def test_plain_name_unchanged(self):
        assert normalize_company_name("Tesla") == "Tesla"


class TestIsCompanyRelevant:
    def test_ticker_token_match(self):
        assert is_company_relevant("NVDA rallies on earnings", "NVDA", "NVIDIA Corp")

    def test_ticker_must_be_standalone_token(self):
        assert not is_company_relevant("ENVDAX fund update", "NVDA", "NVIDIA Corp")

    def test_ticker_match_case_sensitive(self):
        assert not is_company_relevant("nvda in lowercase", "NVDA", "ZZZ Unrelated Name")

    def test_single_letter_ticker_ignored(self):
        # 'T' as a token must NOT qualify — single-letter tickers rely on name match
        assert not is_company_relevant("A T intersection story", "T", "AT&T Inc")

    def test_company_name_match_case_insensitive(self):
        assert is_company_relevant("Nvidia unveils new GPU line", "NVDA", "NVIDIA Corp")

    def test_short_normalized_name_skipped(self):
        # Normalized name under 3 chars must not substring-match
        assert not is_company_relevant("go to the store", "GO", "Go Inc")

    def test_unrelated_title_rejected(self):
        assert not is_company_relevant(
            "P&G raises dividend for 70th year", "NVDA", "NVIDIA Corp"
        )


class TestFilterCompanyArticles:
    def test_filters_by_title(self):
        articles = [
            {"title": "NVDA beats estimates", "url": "u1"},
            {"title": "Unrelated market story", "url": "u2"},
            {"title": "Nvidia data-center demand soars", "url": "u3"},
        ]
        kept = filter_company_articles(articles, "NVDA", "NVIDIA Corp")
        assert [a["url"] for a in kept] == ["u1", "u3"]

    def test_missing_title_rejected(self):
        assert filter_company_articles([{"url": "u1"}], "NVDA", "NVIDIA Corp") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_news_scope.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.modules.equities.agents.news_scope'`.

- [ ] **Step 3: Implement** — create `app/modules/equities/agents/news_scope.py`:

```python
"""Scope tagging, relevance filtering, and merge/dedupe for per-ticker news.

Pure functions used by the graph-level news prefetch and the per-stock article
merge (2026-07-17 per-ticker news spec). No I/O here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

_LEGAL_SUFFIXES = {
    "inc",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "plc",
    "holdings",
}


def normalize_company_name(name: str) -> str:
    """Strip a leading "The" and trailing legal/share-class tokens for matching.

    "NVIDIA Corp" -> "NVIDIA"; "Berkshire Hathaway Inc Class B" ->
    "Berkshire Hathaway"; "The Coca-Cola Company" -> "Coca-Cola".
    """
    tokens = name.strip().split()
    if tokens and tokens[0].lower() == "the":
        tokens = tokens[1:]
    changed = True
    while changed and tokens:
        changed = False
        if tokens[-1].lower().rstrip(".,") in _LEGAL_SUFFIXES:
            tokens = tokens[:-1]
            changed = True
        elif len(tokens) >= 2 and tokens[-2].lower() == "class" and len(tokens[-1]) <= 2:
            tokens = tokens[:-2]
            changed = True
    return " ".join(tokens)


def is_company_relevant(title: str, symbol: str, company_name: str) -> bool:
    """True if the title names the ticker or the normalized company name.

    Ticker rule: exact uppercase standalone token, only for tickers >= 2 chars
    (single-letter tickers like T/F would false-positive on ordinary words).
    Name rule: case-insensitive substring of the normalized company name, only
    when the normalized name is >= 3 chars.
    """
    if len(symbol) >= 2 and re.search(rf"\b{re.escape(symbol)}\b", title):
        return True
    normalized = normalize_company_name(company_name)
    return len(normalized) >= 3 and normalized.lower() in title.lower()


def filter_company_articles(
    articles: list[dict], symbol: str, company_name: str
) -> list[dict]:
    """Keep only articles whose title passes the company-relevance rules."""
    return [
        a for a in articles if is_company_relevant(a.get("title", ""), symbol, company_name)
    ]


def tag_scope(articles: list[dict], scope: str) -> list[dict]:
    """Return copies of the articles with their scope field set."""
    return [{**a, "scope": scope} for a in articles]


def _published(article: dict) -> datetime:
    value = article.get("published_at")
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
    else:
        return datetime.min.replace(tzinfo=UTC)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _dedupe_key(article: dict) -> tuple[str, str]:
    url = (article.get("url") or "").strip()
    if url:
        return ("url", url)
    return ("title", (article.get("title") or "").strip().lower())


def merge_and_dedupe(
    manual: list[dict],
    company: list[dict],
    sector: list[dict],
    market: list[dict],
    company_cap: int,
) -> list[dict]:
    """Merge scope-tagged lists with URL/title dedupe.

    Retention priority: manual > company > sector > market (a story in both the
    company feed and a sector feed renders once, under company). Company
    articles are sorted newest-first and capped at company_cap after dedupe.
    """
    company_sorted = sorted(company, key=_published, reverse=True)
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    company_kept = 0
    for scope_list in (manual, company_sorted, sector, market):
        for article in scope_list:
            key = _dedupe_key(article)
            if key in seen:
                continue
            if article.get("scope") == "company":
                if company_kept >= company_cap:
                    continue
                company_kept += 1
            seen.add(key)
            merged.append(article)
    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_news_scope.py -q`
Expected: all PASS (merge tests come in Task 3; only filter/normalize classes exist so far).

- [ ] **Step 5: Lint + stage**

```bash
cd <worktree> && /Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/ruff check app/modules/equities/agents/news_scope.py tests/unit/equities/test_news_scope.py
git add app/modules/equities/agents/news_scope.py tests/unit/equities/test_news_scope.py
```

---

### Task 3: `news_scope.py` — tagging + merge/dedupe tests

The implementation already exists (Task 2 Step 3 shipped `tag_scope`, `merge_and_dedupe`). This task adds their tests — TDD here means the tests must exercise every documented behavior; if any test fails, fix the implementation, not the test.

**Files:**
- Test: `tests/unit/equities/test_news_scope.py` (append)
- Possibly fix: `app/modules/equities/agents/news_scope.py`

- [ ] **Step 1: Append the tests**

```python
class TestTagScope:
    def test_tags_and_copies(self):
        original = [{"title": "x"}]
        tagged = tag_scope(original, "company")
        assert tagged == [{"title": "x", "scope": "company"}]
        assert "scope" not in original[0]  # copies, not mutation

    def test_overwrites_existing_scope(self):
        assert tag_scope([{"title": "x", "scope": "Technology"}], "manual") == [
            {"title": "x", "scope": "manual"}
        ]


def _art(title, url, scope, published="2026-07-10T10:00:00Z"):
    return {"title": title, "url": url, "scope": scope, "published_at": published}


class TestMergeAndDedupe:
    def test_priority_company_over_sector_and_market(self):
        dup_company = _art("Chip rally", "same-url", "company")
        dup_sector = _art("Chip rally", "same-url", "sector")
        dup_market = _art("Chip rally", "same-url", "market")
        merged = merge_and_dedupe([], [dup_company], [dup_sector], [dup_market], company_cap=6)
        assert merged == [dup_company]

    def test_manual_never_deduped_away(self):
        manual = _art("Story", "same-url", "manual")
        company = _art("Story", "same-url", "company")
        merged = merge_and_dedupe([manual], [company], [], [], company_cap=6)
        assert merged == [manual]

    def test_title_fallback_when_url_missing(self):
        a = {"title": "Same Headline", "url": "", "scope": "company", "published_at": None}
        b = {"title": "same headline", "url": "", "scope": "market", "published_at": None}
        merged = merge_and_dedupe([], [a], [], [b], company_cap=6)
        assert merged == [a]

    def test_company_cap_keeps_newest(self):
        old = _art("old", "u1", "company", "2026-07-01T00:00:00Z")
        mid = _art("mid", "u2", "company", "2026-07-05T00:00:00Z")
        new = _art("new", "u3", "company", "2026-07-09T00:00:00Z")
        merged = merge_and_dedupe([], [old, mid, new], [], [], company_cap=2)
        assert [a["title"] for a in merged] == ["new", "mid"]

    def test_cap_does_not_limit_other_scopes(self):
        company = [_art("c", "u1", "company")]
        sector = [_art("s1", "u2", "sector"), _art("s2", "u3", "sector")]
        merged = merge_and_dedupe([], company, sector, [], company_cap=1)
        assert len(merged) == 3

    def test_distinct_articles_all_kept_in_scope_order(self):
        manual = [_art("m", "u0", "manual")]
        company = [_art("c", "u1", "company")]
        sector = [_art("s", "u2", "sector")]
        market = [_art("k", "u3", "market")]
        merged = merge_and_dedupe(manual, company, sector, market, company_cap=6)
        assert [a["scope"] for a in merged] == ["manual", "company", "sector", "market"]
```

- [ ] **Step 2: Run tests**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_news_scope.py -q`
Expected: all PASS. If any fail, fix `news_scope.py` (the documented behavior in Task 2 Step 3 is the contract) and re-run.

- [ ] **Step 3: Lint + stage**

```bash
cd <worktree> && /Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/ruff check tests/unit/equities/test_news_scope.py
git add tests/unit/equities/test_news_scope.py app/modules/equities/agents/news_scope.py
```

---

### Task 4: Company stage in `_run_prefetch_news`

**Files:**
- Modify: `app/modules/equities/agents/graph.py` (function `_run_prefetch_news`, currently ~lines 39–94; add import)
- Test: `tests/unit/equities/test_news_prefetch.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/equities/test_news_prefetch.py`. Note the existing `_mkdeps()` helper does not define `get_news`; configure it per-test:

```python
def _company_feed(*titles):
    return {
        "articles": [
            {"title": t, "source": "Yahoo Finance", "url": f"u-{i}", "published_at": "2026-04-14T09:00:00Z"}
            for i, t in enumerate(titles)
        ],
        "source": "mock",
    }


async def test_prefetch_fetches_company_news_filtered_and_tagged():
    deps = _mkdeps()
    deps["data_service"].get_news = AsyncMock(
        return_value=_company_feed("AAPL beats estimates", "Unrelated P&G story")
    )
    screened = [_stock("AAPL", "Technology")]

    from app.modules.equities.agents.graph import _run_prefetch_news

    result = await _run_prefetch_news({"screened": screened, "deps": deps})

    ctx = result["news_context"]
    deps["data_service"].get_news.assert_awaited_once()
    kwargs = deps["data_service"].get_news.await_args.kwargs
    assert kwargs["symbols"] == ["AAPL"]
    assert kwargs["limit"] == 10  # company_news_fetch_limit default
    company = ctx["company"]["AAPL"]
    assert [a["title"] for a in company] == ["AAPL beats estimates"]
    assert company[0]["scope"] == "company"


async def test_prefetch_company_fetches_are_sequential_and_sorted():
    deps = _mkdeps()
    call_order = []

    async def fake_get_news(symbols, since=None, limit=10):
        call_order.append(symbols[0])
        return _company_feed(f"{symbols[0]} update")

    deps["data_service"].get_news = AsyncMock(side_effect=fake_get_news)
    screened = [_stock("MSFT", "Technology"), _stock("AAPL", "Technology")]

    from app.modules.equities.agents.graph import _run_prefetch_news

    await _run_prefetch_news({"screened": screened, "deps": deps})

    assert call_order == ["AAPL", "MSFT"]  # sorted, one at a time


async def test_prefetch_company_failure_degrades_that_symbol_only():
    deps = _mkdeps()

    async def flaky_get_news(symbols, since=None, limit=10):
        if symbols[0] == "AAPL":
            raise RuntimeError("rate limited")
        return _company_feed(f"{symbols[0]} rallies")

    deps["data_service"].get_news = AsyncMock(side_effect=flaky_get_news)
    screened = [_stock("AAPL", "Technology"), _stock("MSFT", "Technology")]

    from app.modules.equities.agents.graph import _run_prefetch_news

    result = await _run_prefetch_news({"screened": screened, "deps": deps})

    ctx = result["news_context"]
    assert ctx["company"]["AAPL"] == []
    assert [a["title"] for a in ctx["company"]["MSFT"]] == ["MSFT rallies"]


async def test_prefetch_company_prompt_cap_passed_through_context():
    deps = _mkdeps(company_news_prompt_cap=3)
    deps["data_service"].get_news = AsyncMock(return_value=_company_feed())
    screened = [_stock("AAPL", "Technology")]

    from app.modules.equities.agents.graph import _run_prefetch_news

    result = await _run_prefetch_news({"screened": screened, "deps": deps})
    assert result["news_context"]["company_prompt_cap"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_news_prefetch.py -q -k company`
Expected: 4 FAILED (`KeyError: 'company'` etc.).

- [ ] **Step 3: Implement** — in `app/modules/equities/agents/graph.py`:

Add to imports (after the `ManualNewsLoader` import):

```python
from app.modules.equities.agents.news_scope import (
    filter_company_articles,
    merge_and_dedupe,
    tag_scope,
)
```

Inside `_run_prefetch_news`, after the `sector_articles` block and before the `manual_articles` block, insert:

```python
    company_fetch_limit = deps.get("company_news_fetch_limit", 10)
    company_articles: dict[str, list[dict]] = {}
    for stock in sorted(state.get("screened", []), key=lambda s: s.symbol):
        try:
            company_result = await data_service.get_news(
                symbols=[stock.symbol], since=since, limit=company_fetch_limit
            )
            raw = list(company_result.get("articles", []))
        except Exception:
            logger.warning("Company news fetch failed for %s", stock.symbol, exc_info=True)
            raw = []
        filtered = filter_company_articles(raw, stock.symbol, stock.company_name)
        company_articles[stock.symbol] = tag_scope(filtered, "company")
```

Update the summary log call to include the company count (replace the existing `logger.info("News prefetch: ...")` call):

```python
    logger.info(
        "News prefetch: %d market + %d sector articles across %d sectors + %d company + %d manual",
        len(market_articles),
        sum(len(v) for v in sector_articles.values()),
        len(unique_sectors),
        sum(len(v) for v in company_articles.values()),
        len(manual_articles),
    )
```

Replace the returned `news_context` dict:

```python
    return {
        "news_context": {
            "market": market_articles,
            "sectors": sector_articles,
            "company": company_articles,
            "manual": manual_articles,
            "company_prompt_cap": deps.get("company_news_prompt_cap", 6),
        }
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_news_prefetch.py -q`
Expected: ALL pass — the 4 new tests AND the existing 8 (existing tests don't set `get_news` on the mock; `AsyncMock` auto-creates it returning a `MagicMock`, whose `.get("articles", [])`... does NOT return a list — **it returns a MagicMock**, so `filter_company_articles` iterates a MagicMock and fails). If the existing tests fail this way, fix by making the company stage defensive: wrap the whole body in the try/except (the `list(...)` call raises `TypeError` on a MagicMock, which the except already catches). Confirm the code in Step 3 puts `raw = list(company_result.get("articles", []))` INSIDE the try block (it does) — then a MagicMock return degrades to `raw = []` via the TypeError path and existing tests pass with empty company lists.

- [ ] **Step 5: Lint + stage**

```bash
cd <worktree> && /Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/ruff check app/modules/equities/agents/graph.py tests/unit/equities/test_news_prefetch.py
git add app/modules/equities/agents/graph.py tests/unit/equities/test_news_prefetch.py
```

---

### Task 5: Scope-aware merge in `_articles_for_stock`

**Files:**
- Modify: `app/modules/equities/agents/graph.py` (function `_articles_for_stock`, currently ~lines 97–106)
- Test: `tests/unit/equities/test_news_prefetch.py` (append + update one existing test)

- [ ] **Step 1: Write the failing tests** — append:

```python
async def test_articles_for_stock_merges_all_scopes_with_dedupe():
    from app.modules.equities.agents.graph import _articles_for_stock

    news_context = {
        "market": [{"title": "Market up", "url": "m1", "published_at": "2026-04-14T10:00:00Z"}],
        "sectors": {
            "Technology": [
                {"title": "Tech leads", "url": "s1", "published_at": "2026-04-14T10:00:00Z"},
                {"title": "Dup story", "url": "dup", "published_at": "2026-04-14T10:00:00Z"},
            ]
        },
        "company": {
            "AAPL": [
                {"title": "AAPL beats", "url": "c1", "scope": "company", "published_at": "2026-04-14T11:00:00Z"},
                {"title": "Dup story", "url": "dup", "scope": "company", "published_at": "2026-04-14T10:00:00Z"},
            ]
        },
        "manual": [{"title": "Curated market note", "scope": "market", "url": "mm1"}],
        "company_prompt_cap": 6,
    }
    stock = _stock("AAPL", "Technology")

    articles = _articles_for_stock(stock, news_context)

    by_url = {a["url"]: a for a in articles}
    assert set(by_url) == {"m1", "s1", "dup", "c1", "mm1"}
    assert by_url["dup"]["scope"] == "company"  # company wins the dedupe
    assert by_url["m1"]["scope"] == "market"
    assert by_url["s1"]["scope"] == "sector"
    assert by_url["mm1"]["scope"] == "manual"


async def test_articles_for_stock_manual_ticker_scope_targets_one_symbol():
    from app.modules.equities.agents.graph import _articles_for_stock

    news_context = {
        "market": [],
        "sectors": {},
        "company": {},
        "manual": [
            {"title": "AAPL supply-chain note", "scope": "AAPL", "url": "mt1"},
            {"title": "JPM note", "scope": "JPM", "url": "mt2"},
        ],
        "company_prompt_cap": 6,
    }
    aapl = _articles_for_stock(_stock("AAPL", "Technology"), news_context)
    jpm = _articles_for_stock(_stock("JPM", "Financial Services"), news_context)

    assert [a["url"] for a in aapl] == ["mt1"]
    assert [a["url"] for a in jpm] == ["mt2"]


async def test_articles_for_stock_company_cap_applied():
    from app.modules.equities.agents.graph import _articles_for_stock

    company = [
        {"title": f"story {i}", "url": f"c{i}", "scope": "company",
         "published_at": f"2026-04-{10 + i:02d}T10:00:00Z"}
        for i in range(4)
    ]
    news_context = {
        "market": [],
        "sectors": {},
        "company": {"AAPL": company},
        "manual": [],
        "company_prompt_cap": 2,
    }
    articles = _articles_for_stock(_stock("AAPL", "Technology"), news_context)
    assert [a["url"] for a in articles] == ["c3", "c2"]  # newest two
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_news_prefetch.py -q -k articles_for_stock`
Expected: 3 FAILED (no scope tagging / no company merge in current implementation).

- [ ] **Step 3: Implement** — replace `_articles_for_stock` in `graph.py`:

```python
def _articles_for_stock(stock, news_context: dict) -> list[dict]:
    """Merge manual + company + sector + market articles for one stock.

    Articles are scope-tagged, URL/title-deduped (manual > company > sector >
    market), and company articles are capped (newest first). Manual articles
    match by scope == "market", the stock's sector, or the stock's symbol.
    """
    market = tag_scope(list(news_context.get("market", [])), "market")
    sector_map = news_context.get("sectors", {})
    sector = tag_scope(
        list(sector_map.get(stock.sector, [])) if stock.sector else [], "sector"
    )
    company = news_context.get("company", {}).get(stock.symbol, [])
    manual = tag_scope(
        [
            a
            for a in news_context.get("manual", [])
            if a.get("scope") == "market"
            or (stock.sector and a.get("scope") == stock.sector)
            or a.get("scope") == stock.symbol
        ],
        "manual",
    )
    cap = news_context.get("company_prompt_cap", 6)
    return merge_and_dedupe(manual, company, sector, market, company_cap=cap)
```

- [ ] **Step 4: Run the full prefetch test file**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_news_prefetch.py -q`
Expected: the 3 new tests pass. `test_news_analysis_merges_and_passes_per_stock_articles` (line ~127) may fail if it asserts exact article dicts (they now carry `scope`). If so, update ONLY its assertions to account for the added `scope` key (e.g., compare titles/urls instead of whole dicts, or include `"scope": "market"` etc. in expected dicts). Do not weaken what it verifies: per-stock lists must still differ by sector.

- [ ] **Step 5: Lint + stage**

```bash
cd <worktree> && /Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/ruff check app/modules/equities/agents/graph.py tests/unit/equities/test_news_prefetch.py
git add app/modules/equities/agents/graph.py tests/unit/equities/test_news_prefetch.py
```

---

### Task 6: Scope-grouped `format_news_context`

**Files:**
- Modify: `app/modules/equities/agents/context_formatters.py` (`format_news_context` ~line 543; DELETE `_bucket_articles` ~line 510 — its only caller goes away)
- Test: `tests/unit/equities/test_context_formatters.py` (update news tests ~line 661+, add new)

- [ ] **Step 1: Write the failing tests** — append to the news test region of `tests/unit/equities/test_context_formatters.py`:

```python
class TestFormatNewsContextScopeSections:
    def _articles(self):
        return [
            {"title": "AAPL beats", "source": "Reuters", "scope": "company",
             "published_at": "2026-04-14T10:00:00Z"},
            {"title": "Tech leads", "source": "Bloomberg", "scope": "sector",
             "published_at": "2026-04-13T10:00:00Z"},
            {"title": "Market up", "source": "WSJ", "scope": "market",
             "published_at": "2026-04-12T10:00:00Z"},
            {"title": "Curated note", "source": "Analyst", "scope": "manual",
             "published_at": "2026-04-11T10:00:00Z"},
        ]

    def test_sections_in_scope_order(self):
        result = format_news_context("AAPL", "Apple Inc", "Technology", self._articles())
        i_company = result.index("## Company-specific (AAPL)")
        i_sector = result.index("## Sector (Technology)")
        i_market = result.index("## Market")
        i_manual = result.index("## Curated")
        assert i_company < i_sector < i_market < i_manual

    def test_empty_company_section_rendered_explicitly(self):
        articles = [a for a in self._articles() if a["scope"] != "company"]
        result = format_news_context("AAPL", "Apple Inc", "Technology", articles)
        assert "## Company-specific (AAPL)" in result
        assert "No company-specific headlines found for AAPL." in result

    def test_empty_non_company_sections_omitted(self):
        articles = [a for a in self._articles() if a["scope"] == "company"]
        result = format_news_context("AAPL", "Apple Inc", "Technology", articles)
        assert "## Sector" not in result
        assert "## Market" not in result
        assert "## Curated" not in result

    def test_untagged_articles_fall_back_to_market(self):
        result = format_news_context(
            "AAPL", "Apple Inc", "Technology",
            [{"title": "Legacy article", "source": "X", "published_at": "2026-04-14T10:00:00Z"}],
        )
        assert "## Market" in result
        assert "Legacy article" in result

    def test_rows_sorted_newest_first_within_section(self):
        articles = [
            {"title": "older", "source": "A", "scope": "company",
             "published_at": "2026-04-10T10:00:00Z"},
            {"title": "newer", "source": "B", "scope": "company",
             "published_at": "2026-04-14T10:00:00Z"},
        ]
        result = format_news_context("AAPL", "Apple Inc", "Technology", articles)
        assert result.index("newer") < result.index("older")

    def test_no_articles_at_all(self):
        result = format_news_context("AAPL", "Apple Inc", "Technology", [])
        assert "No recent news available for AAPL." in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_context_formatters.py -q -k ScopeSections`
Expected: FAIL (current output has time-bucket headers, no scope sections).

- [ ] **Step 3: Implement** — in `context_formatters.py`, delete `_bucket_articles` entirely and replace `format_news_context` with:

```python
def format_news_context(
    symbol: str,
    company_name: str,
    sector: str | None,
    articles: list[dict],
) -> str:
    """Format news articles as scope-grouped markdown sections.

    Section order: Company-specific, Sector, Market, Curated. An empty company
    section renders an explicit absence line (the analyst treats it as
    neutral); other empty sections are omitted. Untagged articles fall back to
    market scope.
    """
    sector_display = sector or "Unknown"
    lines = [
        f"# {symbol} — {company_name} | Recent News",
        f"**Sector:** {sector_display} | **Articles:** {len(articles)}",
        "",
    ]

    if not articles:
        lines.append(f"No recent news available for {symbol}.")
        return "\n".join(lines)

    by_scope: dict[str, list[dict]] = {"company": [], "sector": [], "market": [], "manual": []}
    for article in articles:
        scope = article.get("scope")
        by_scope[scope if scope in by_scope else "market"].append(article)

    def _rows(items: list[dict]) -> list[tuple]:
        def _key(a: dict):
            return _parse_datetime(a.get("published_at")) or datetime.min.replace(tzinfo=UTC)

        rows = []
        for a in sorted(items, key=_key, reverse=True):
            pub = _parse_datetime(a.get("published_at"))
            date_str = pub.strftime("%b %d") if pub else "--"
            source = a.get("author") or a.get("source", "--")
            rows.append((date_str, source, a.get("title", "No title")))
        return rows

    sections = [
        ("company", f"Company-specific ({symbol})"),
        ("sector", f"Sector ({sector_display})"),
        ("market", "Market"),
        ("manual", "Curated"),
    ]
    for scope, title in sections:
        items = by_scope[scope]
        if not items and scope != "company":
            continue
        lines.append(f"## {title}")
        if not items:
            lines.append(f"No company-specific headlines found for {symbol}.")
        else:
            lines.append(_render_table(["Date", "Source", "Headline"], _rows(items)))
        lines.append("")

    return "\n".join(lines)
```

(`datetime`, `UTC`, `_parse_datetime`, `_render_table` are already imported/defined in this module.)

- [ ] **Step 4: Run the whole formatter file; update stranded tests**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_context_formatters.py -q`
Expected: new tests pass; the pre-existing `format_news_context` tests (~lines 661–760) that assert bucket headers ("Last 7 Days") or bucket ordering FAIL. Update each to the new layout — preserve each test's intent: headline presence stays asserted, date/source rendering stays asserted, empty-input message unchanged. Delete any test that exists purely to verify time-bucketing (that behavior is removed by design). Also `grep -n "_bucket_articles" tests/` — if formatter tests import or test `_bucket_articles` directly, delete those tests (the helper is gone).

- [ ] **Step 5: Full equities test sweep + lint + stage**

```bash
cd <worktree> && PYTEST tests/unit/equities/ -q
cd <worktree> && /Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/ruff check app/modules/equities/agents/context_formatters.py tests/unit/equities/test_context_formatters.py
git add app/modules/equities/agents/context_formatters.py tests/unit/equities/test_context_formatters.py
```

---

### Task 7: News skill update (`base/news.md`)

**Files:**
- Modify: `app/modules/equities/agents/skills/base/news.md`

No unit test (prose); `tests/unit/equities/test_skill_loader.py` is content-agnostic and must still pass.

- [ ] **Step 1: Replace the "## Input Shape" section** (everything from `## Input Shape` up to but not including `## Analysis Framework`) with:

```markdown
## Input Shape

Your context contains up to four scope-labeled sections of news, each a table of
Date / Source / Headline sorted newest-first:

1. **Company-specific (TICKER)** — headlines that name this company or its
   ticker, pulled from the company's own news feed and relevance-filtered. This
   section is always present: when no qualifying headlines exist this week it
   says so explicitly, and that absence is neutral information — reason from
   sector/market inference as before.
2. **Sector** — articles covering the stock's sector (e.g., Technology,
   Financial Services): sector performance vs the market, visible sub-sector
   themes, structural narratives.
3. **Market** — broad-market articles covering the aggregate equity market,
   rate/monetary policy, risk appetite, and macro conditions.
4. **Curated** — manually-curated articles an operator tagged to the market,
   this sector, or this specific ticker. Treat a curated article tagged to this
   ticker as company-specific signal.

The sections are labeled — do not re-infer an article's scope from its
headline. Weight them deliberately: company-specific headlines dominate the
assessment when present; sector and market context adjust the score rather than
drive it; a duplicate story appears only once, under its most specific scope.
Weigh source quality — a wire service or major financial outlet carries more
signal than an aggregator or promotional feed.
```

- [ ] **Step 2: Replace the final bullet of "## Common Failure Modes"** — the bullet beginning "Do not treat "absence of stock-specific articles"" and the bullet beginning "Classify each article by its scope" are replaced by:

```markdown
- Do not treat an empty Company-specific section as a signal on its own. It means no qualifying company headlines this week — reason from sector/market inference, exactly as before.
- Do not re-infer article scope from headlines — the sections are already labeled by scope. Spend your reasoning on stock exposure and signal strength, not on reclassifying articles.
- Do not let one company-specific headline override a consistent contrary macro/sector picture without considering its materiality — a minor product note is not an earnings miss.
```

- [ ] **Step 3: Reconcile the framework references.** In "## Analysis Framework" step 4, change "Check the stock-specific layer." to "Check the Company-specific and Curated sections." and change "articles tagged to this ticker" to "curated articles tagged to this ticker". Leave the worked examples as-is (their layer language still reads correctly).

- [ ] **Step 4: Verify loader + full equities tests still green**

Run: `cd <worktree> && PYTEST tests/unit/equities/test_skill_loader.py tests/unit/equities/test_news_analyst.py -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
cd <worktree> && git add app/modules/equities/agents/skills/base/news.md
```

---

### Task 8: Service wiring for the config knobs

**Files:**
- Modify: `app/modules/equities/service.py` (deps dict, ~line 326, after `"manual_news_root": manual_news_root,`)

- [ ] **Step 1: Implement** — add two entries to the deps dict:

```python
                "company_news_fetch_limit": self.config.agents.company_news_fetch_limit,
                "company_news_prompt_cap": self.config.agents.company_news_prompt_cap,
```

- [ ] **Step 2: Run the service + graph test files**

Run: `cd <worktree> && PYTEST tests/unit/equities/ -q`
Expected: all pass (prefetch tests already cover deps consumption with defaults; this step threads real config).

- [ ] **Step 3: Lint + stage**

```bash
cd <worktree> && /Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/ruff check app/modules/equities/service.py
git add app/modules/equities/service.py
```

---

### Task 9: CLAUDE.md update

**Files:**
- Modify: `CLAUDE.md` (worktree copy)

- [ ] **Step 1: Update the equities pipeline section.** In "### Equities Branch Pipeline", after the bullet describing the 3 LLM analyst agents, add:

```markdown
- News context is scope-labeled since 2026-07 (per-ticker news spec): graph-level prefetch fetches market (SPY) + per-sector ETF + **per-company** headlines (screened symbols only, sequential, TTL-cached via `DataPlatformService`), applies a ticker/company-name title filter (`agents/news_scope.py`), tags scope (`company`/`sector`/`market`/`manual`), and merges with URL dedupe (manual > company > sector > market, company capped by `AgentsConfig.company_news_prompt_cap`). A failed company fetch degrades that symbol to sector+market only. Backtests have no news adapters, so company scope is always empty there.
```

- [ ] **Step 2: Verify no other CLAUDE.md text contradicts the change.** Grep the worktree `CLAUDE.md` for "news" and update any sentence still describing sector-only news input (the Gotchas section's adaptive-weights entries do not need changes).

- [ ] **Step 3: Stage**

```bash
cd <worktree> && git add CLAUDE.md
```

---

### Task 10: Full verification sweep (no commit)

- [ ] **Step 1: Full unit suite**

Run: `cd <worktree> && PYTEST tests/unit/ -q`
Expected: everything green (was 1,147 tests before this stream; now more). Any failure: fix before proceeding — do not proceed to Task 11 with a red suite.

- [ ] **Step 2: Ruff, whole tree**

```bash
cd <worktree> && /Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/ruff check app/ tests/ scripts/
cd <worktree> && /Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/ruff format --check app/modules/equities/agents/news_scope.py tests/unit/equities/test_news_scope.py
```

Expected: clean. Fix and re-stage anything flagged.

- [ ] **Step 3: Confirm everything intended is staged, nothing committed**

```bash
cd <worktree> && git status --short && git diff --cached --stat && git log --oneline -1
```

Expected staged set: `config.py`, `news_scope.py`, `graph.py`, `context_formatters.py`, `skills/base/news.md`, `service.py`, `CLAUDE.md`, the 3 test files, spec + plan docs. HEAD must still be `886206d` (NO new commits).

---

### Task 11: Experiment gate (approved spend ≈ $96; quick preset, growth)

Run only after Task 10 is fully green. All commands from the **worktree** with the main venv; secrets come from the main repo's `.env`.

- [ ] **Step 1: Bring baseline bundle + LLM cache into the worktree**

```bash
cd <worktree>
mkdir -p data/skill_bundles
cp -R /Users/franco_lu/Desktop/ai-hedgefund-final/data/skill_bundles/baseline_v1 data/skill_bundles/
cp /Users/franco_lu/Desktop/ai-hedgefund-final/data/llm_response_cache.db data/
```

- [ ] **Step 2: Freeze the item-2 skills as the treatment bundle**

```bash
cd <worktree> && /Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/python -m scripts.bundle_skills item2_news_v1
```

Expected: `Created bundle: data/skill_bundles/item2_news_v1`.

- [ ] **Step 3: Buy the noise floor (≈ $94 — approved)**

```bash
cd <worktree> && set -a && source /Users/franco_lu/Desktop/ai-hedgefund-final/.env && set +a && \
/Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/python -m scripts.probe_noise \
    --preset quick --branch growth --end-date 2025-06-30 --runs 5 \
    --skills-bundle baseline_v1 --yes
```

Expected: cost estimate printed (~$94), then 5 runs (~10–30 min total), then a stored noise floor. This is long-running — run it in the background and check output on completion.

- [ ] **Step 4: Run the experiment (≈ $1–2 — arms are mostly cache-served)**

```bash
cd <worktree> && set -a && source /Users/franco_lu/Desktop/ai-hedgefund-final/.env && set +a && \
/Users/franco_lu/Desktop/ai-hedgefund-final/.venv/bin/python -m scripts.run_experiment \
    --preset quick --branch growth --end-date 2025-06-30 \
    --baseline-bundle baseline_v1 --treatment-bundle item2_news_v1 \
    --t-correction --report-out data/backtest_runs/item2_gate_report.txt
cat data/backtest_runs/item2_gate_report.txt
```

Expected: a verdict report. **Ship criterion:** verdict is *improvement* or *within noise*. If *regression beyond the floor*: STOP — the change goes back to design; do not stage anything further.

- [ ] **Step 5: Record the verdict in the spec**

Edit `docs/superpowers/specs/2026-07-17-per-ticker-news-design.md`: under the "Cutover" block, add a line `> **Gate verdict (quick/growth, 2026-07-17):** <verdict summary + key numbers from the report>`. Then:

```bash
cd <worktree> && git add docs/superpowers/specs/2026-07-17-per-ticker-news-design.md
```

Note: `data/` artifacts (bundles, cache, floor, report) are gitignored — they stay local, as intended. The **cutover precondition** (item-1 live health check on the 07-20/07-27 runs) remains open regardless of the verdict; cutover is a separate, user-approved step targeted at 2026-08-03.

---

## Self-review checklist (run after writing, fixed inline)

- Spec coverage: §1 prefetch → Task 4; §2 filter/tag/dedupe → Tasks 2–3, merge → Task 5; §3 formatter + skill → Tasks 6–7; config → Tasks 1, 8; backtest parity → covered by design (no adapter → degrade path tested in Task 4) ; gate + verdict recording → Task 11; CLAUDE.md → Task 9. ✔
- No placeholders; every code step shows the code. ✔
- Type consistency: `news_scope` function names/signatures identical across Tasks 2–5; `company_prompt_cap` context key consistent between Tasks 4 and 5; config field names consistent across Tasks 1, 4 (defaults), and 8. ✔
