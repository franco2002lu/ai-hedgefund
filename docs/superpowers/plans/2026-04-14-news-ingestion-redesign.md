# News Ingestion Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-stock news fetching with market + sector scoped news, add an extensible adapter interface, and support file-based manual article insertion.

**Architecture:** `NewsAdapter` gets two scoped methods (`get_market_news`, `get_sector_news`). A `prefetch_news` LangGraph node runs once per rebalance to fetch market + per-sector news and load manual articles from `data/news/manual/{date}.json`. `NewsAnalyst.analyze()` becomes a pure consumer that takes pre-fetched articles instead of calling `data_service` itself. A `BacktestNewsAdapter` placeholder returns empty lists until a real historical news provider is plugged in.

**Tech Stack:** Python 3.12, Pydantic v2, LangGraph, pytest, ruff. Test runner is `.venv/bin/pytest`; linter/formatter is `.venv/bin/ruff`.

**Spec:** `docs/superpowers/specs/2026-04-14-news-ingestion-redesign-design.md`

---

## File Plan

**New files:**
- `app/modules/data_platform/adapters/news_base.py` — `SectorMapping` helper
- `app/modules/data_platform/manual_news.py` — `ManualNewsLoader` (file-based article loader)
- `app/modules/backtest/adapters/backtest_news.py` — `BacktestNewsAdapter` (empty placeholder)
- `app/modules/equities/news_window.py` — `window_days_for_frequency()` helper
- `data/news/manual/.gitkeep` — directory marker
- `tests/unit/data_platform/test_manual_news.py`
- `tests/unit/data_platform/test_yahoo_finance_news.py` (new file if not present; otherwise add to existing)
- `tests/unit/backtest/test_backtest_news_adapter.py`
- `tests/unit/equities/test_news_prefetch.py`
- `tests/unit/equities/test_news_window.py`

**Modified files:**
- `app/common/interfaces/news.py` — add abstract `get_market_news`, `get_sector_news`; keep `get_news` as non-abstract for backward compatibility
- `app/modules/data_platform/service.py` — add `get_market_news`, `get_sector_news` pass-throughs
- `app/modules/data_platform/adapters/yahoo_finance.py` — implement new methods; fix source/author bug
- `app/modules/equities/agents/graph.py` — add `prefetch_news` node, new edge, add `news_context` to state; update `news_analysis` node to pass per-stock article lists
- `app/modules/equities/agents/news_analyst.py` — new `analyze(stock, articles)` signature; drop `data_service` dependency
- `app/modules/backtest/llm_analyst_cache.py` — `CachedAnalystWrapper` forwards articles to the wrapped news analyst only
- `app/modules/backtest/context.py` — register `BacktestNewsAdapter` under `"news"`; pass `news_window_days` via deps
- `app/modules/equities/service.py` — pass `news_window_days` via deps
- `app/main.py` — already registers Yahoo Finance under `"news"` key; no change needed to registration (confirmed at `app/main.py:45-47`)
- `app/dependencies.py` — remove `data_service` kwarg from `NewsAnalyst(...)` construction
- `tests/unit/equities/test_news_analyst.py` — update for new signature

---

## Task 1: Add scoped methods to the `NewsAdapter` interface

**Files:**
- Modify: `app/common/interfaces/news.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/unit/data_platform/test_news_interface.py`:

```python
"""Contract tests for the NewsAdapter interface."""

from datetime import date

from app.common.interfaces.news import NewsAdapter, NewsArticle


def test_news_adapter_requires_get_market_news():
    class Incomplete(NewsAdapter):
        async def get_sector_news(self, sector, since=None, limit=20):
            return []

    try:
        Incomplete()
    except TypeError as exc:
        assert "get_market_news" in str(exc)
    else:
        raise AssertionError("expected TypeError for missing get_market_news")


def test_news_adapter_requires_get_sector_news():
    class Incomplete(NewsAdapter):
        async def get_market_news(self, since=None, limit=20):
            return []

    try:
        Incomplete()
    except TypeError as exc:
        assert "get_sector_news" in str(exc)
    else:
        raise AssertionError("expected TypeError for missing get_sector_news")


async def test_concrete_subclass_satisfies_interface():
    class Concrete(NewsAdapter):
        async def get_market_news(self, since=None, limit=20):
            return [NewsArticle(title="m", source="s", published_at=date.today(), url="u")]

        async def get_sector_news(self, sector, since=None, limit=20):
            return [NewsArticle(title="s", source="s", published_at=date.today(), url="u")]

    a = Concrete()
    m = await a.get_market_news()
    s = await a.get_sector_news("Technology")
    assert len(m) == 1 and len(s) == 1
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/data_platform/test_news_interface.py -v
```

Expected: FAIL — `get_market_news` / `get_sector_news` do not exist on `NewsAdapter`.

- [ ] **Step 1.3: Update the interface**

Replace `app/common/interfaces/news.py` contents with:

```python
from abc import ABC, abstractmethod
from datetime import date, datetime

from pydantic import BaseModel


class NewsArticle(BaseModel):
    title: str
    author: str | None = None
    source: str
    published_at: datetime | date
    url: str = ""
    symbols: list[str] = []
    sentiment: str | None = None
    scope: str | None = None  # "market" or a sector name — used by manual articles


class NewsAdapter(ABC):
    """Abstract news adapter.

    New adapters must implement `get_market_news` and `get_sector_news`.
    `get_news` (symbol-scoped) is retained as a non-abstract method for
    backward compatibility with YahooFinanceAdapter and DataPlatformService.get_news().
    """

    @abstractmethod
    async def get_market_news(
        self,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]:
        """Return broad-market news articles since `since` (inclusive)."""

    @abstractmethod
    async def get_sector_news(
        self,
        sector: str,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]:
        """Return sector-scoped news articles since `since` (inclusive)."""

    async def get_news(
        self,
        symbols: list[str] | None = None,
        query: str | None = None,
        since: date | None = None,
        limit: int = 100,
    ) -> list[NewsArticle]:
        """Legacy symbol-scoped fetch. Default returns []; subclasses may override."""
        return []
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/data_platform/test_news_interface.py -v
```

Expected: PASS.

- [ ] **Step 1.5: Run full test suite to check for regressions**

```bash
.venv/bin/pytest tests/unit/ -q
```

Expected: existing `YahooFinanceAdapter` tests pass (because it still has `get_news`); `NewsAnalyst` and `DataPlatformService` tests still pass (we haven't removed legacy paths yet).

- [ ] **Step 1.6: Lint**

```bash
.venv/bin/ruff check app/common/interfaces/news.py tests/unit/data_platform/test_news_interface.py
.venv/bin/ruff format app/common/interfaces/news.py tests/unit/data_platform/test_news_interface.py
```

- [ ] **Step 1.7: Stage (do not commit — user commits on main)**

```bash
git add app/common/interfaces/news.py tests/unit/data_platform/test_news_interface.py
git status --short
```

---

## Task 2: Add `get_market_news` / `get_sector_news` to `DataPlatformService`

**Files:**
- Modify: `app/modules/data_platform/service.py`
- Test: `tests/unit/data_platform/test_data_platform_service.py`

- [ ] **Step 2.1: Write the failing test**

Append to `tests/unit/data_platform/test_data_platform_service.py` (create the file if needed — check first with `ls tests/unit/data_platform/`):

```python
from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.common.interfaces.news import NewsArticle
from app.modules.data_platform.noop import NoOpCache, NoOpRateLimiter
from app.modules.data_platform.service import DataPlatformService, DataUnavailableError


def _article(title="t"):
    return NewsArticle(title=title, source="Reuters", published_at=date(2026, 4, 14), url="https://x")


async def test_get_market_news_returns_first_successful_adapter():
    adapter = AsyncMock()
    adapter.name = "mock_news"
    adapter.get_market_news.return_value = [_article()]

    svc = DataPlatformService(
        adapter_registry={"news": {"all": [adapter]}},
        cache=NoOpCache(),
        rate_limiter=NoOpRateLimiter(),
    )

    result = await svc.get_market_news(since=date(2026, 4, 7), limit=10)

    assert result["articles"][0]["title"] == "t"
    assert result["source"] == "mock_news"
    adapter.get_market_news.assert_awaited_once_with(date(2026, 4, 7), 10)


async def test_get_sector_news_returns_sector_articles():
    adapter = AsyncMock()
    adapter.name = "mock_news"
    adapter.get_sector_news.return_value = [_article("tech")]

    svc = DataPlatformService(
        adapter_registry={"news": {"all": [adapter]}},
        cache=NoOpCache(),
        rate_limiter=NoOpRateLimiter(),
    )

    result = await svc.get_sector_news("Technology", since=date(2026, 4, 7))

    assert result["sector"] == "Technology"
    assert result["articles"][0]["title"] == "tech"
    adapter.get_sector_news.assert_awaited_once_with("Technology", date(2026, 4, 7), 20)


async def test_get_market_news_raises_when_no_adapter_succeeds():
    svc = DataPlatformService(
        adapter_registry={},
        cache=NoOpCache(),
        rate_limiter=NoOpRateLimiter(),
    )
    with pytest.raises(DataUnavailableError):
        await svc.get_market_news()
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/data_platform/test_data_platform_service.py -v -k "market_news or sector_news"
```

Expected: FAIL — methods do not exist.

- [ ] **Step 2.3: Implement the methods**

Add these methods to `DataPlatformService` in `app/modules/data_platform/service.py`, right after `get_news()`:

```python
    async def get_market_news(
        self,
        since: date | None = None,
        limit: int = 20,
    ) -> dict:
        cache_key = f"market:{since}:{limit}"
        cached = self.cache.get("news", cache_key)
        if cached is not None:
            return cached

        adapters = self.registry.get("news", {}).get("all", [])
        for adapter in adapters:
            try:
                await self.rate_limiter.acquire(adapter.name)
                articles = await adapter.get_market_news(since, limit)
                result = {
                    "articles": [a.model_dump(mode="json") for a in articles],
                    "source": adapter.name,
                }
                self.cache.set("news", cache_key, result)
                return result
            except Exception:
                continue

        raise DataUnavailableError("No adapter could serve market news")

    async def get_sector_news(
        self,
        sector: str,
        since: date | None = None,
        limit: int = 20,
    ) -> dict:
        cache_key = f"sector:{sector}:{since}:{limit}"
        cached = self.cache.get("news", cache_key)
        if cached is not None:
            return cached

        adapters = self.registry.get("news", {}).get("all", [])
        for adapter in adapters:
            try:
                await self.rate_limiter.acquire(adapter.name)
                articles = await adapter.get_sector_news(sector, since, limit)
                result = {
                    "sector": sector,
                    "articles": [a.model_dump(mode="json") for a in articles],
                    "source": adapter.name,
                }
                self.cache.set("news", cache_key, result)
                return result
            except Exception:
                continue

        raise DataUnavailableError(f"No adapter could serve sector news for {sector}")
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/data_platform/test_data_platform_service.py -v -k "market_news or sector_news"
```

Expected: PASS.

- [ ] **Step 2.5: Lint and stage**

```bash
.venv/bin/ruff check app/modules/data_platform/service.py tests/unit/data_platform/test_data_platform_service.py
.venv/bin/ruff format app/modules/data_platform/service.py tests/unit/data_platform/test_data_platform_service.py
git add app/modules/data_platform/service.py tests/unit/data_platform/test_data_platform_service.py
```

---

## Task 3: Implement new methods on `YahooFinanceAdapter` + source/author fix

**Files:**
- Modify: `app/modules/data_platform/adapters/yahoo_finance.py`
- Test: `tests/unit/data_platform/test_yahoo_finance_news.py` (new file)

Yahoo Finance doesn't have a direct market/sector news endpoint in the existing adapter shape. We'll proxy: **market = news for the SPY ticker**; **sector = news for the sector ETF ticker** (e.g., Technology → XLK). This is a simple default that works with the existing `yf.Ticker(symbol).news` path.

- [ ] **Step 3.1: Write the failing test**

Create `tests/unit/data_platform/test_yahoo_finance_news.py`:

```python
"""Unit tests for YahooFinanceAdapter's news methods — uses monkeypatched yfinance."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.data_platform.adapters.yahoo_finance import YahooFinanceAdapter


def _yf_news_item(title, provider="Reuters", pub_date="2026-04-14T12:00:00Z"):
    return {
        "content": {
            "title": title,
            "provider": {"displayName": provider},
            "pubDate": pub_date,
            "canonicalUrl": {"url": "https://example.com/article"},
        }
    }


@pytest.fixture
def mock_ticker():
    with patch("app.modules.data_platform.adapters.yahoo_finance.yf.Ticker") as m:
        yield m


async def test_get_news_puts_provider_in_source_field(mock_ticker):
    mock_ticker.return_value = MagicMock(news=[_yf_news_item("Apple beats earnings", provider="Reuters")])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_news(symbols=["AAPL"], limit=10)

    assert len(articles) == 1
    assert articles[0].source == "Reuters"  # NOT "yahoo_finance"
    assert articles[0].author is None


async def test_get_news_falls_back_when_provider_missing(mock_ticker):
    item = {
        "content": {
            "title": "No provider",
            "pubDate": "2026-04-14T12:00:00Z",
            "canonicalUrl": {"url": "https://example.com/a"},
        }
    }
    mock_ticker.return_value = MagicMock(news=[item])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_news(symbols=["AAPL"], limit=10)

    assert articles[0].source == "Yahoo Finance"


async def test_get_market_news_queries_spy(mock_ticker):
    mock_ticker.return_value = MagicMock(news=[_yf_news_item("Markets rally")])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_market_news(limit=5)

    mock_ticker.assert_called_with("SPY")
    assert len(articles) == 1


async def test_get_sector_news_queries_sector_etf(mock_ticker):
    mock_ticker.return_value = MagicMock(news=[_yf_news_item("Tech leads")])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_sector_news("Technology")

    mock_ticker.assert_called_with("XLK")


async def test_get_sector_news_unknown_sector_returns_empty(mock_ticker):
    mock_ticker.return_value = MagicMock(news=[])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_sector_news("FakeSector")

    assert articles == []


async def test_get_market_news_respects_since(mock_ticker):
    old = _yf_news_item("Old", pub_date="2026-01-01T00:00:00Z")
    new = _yf_news_item("New", pub_date="2026-04-14T00:00:00Z")
    mock_ticker.return_value = MagicMock(news=[old, new])
    adapter = YahooFinanceAdapter()

    articles = await adapter.get_market_news(since=date(2026, 4, 1))

    titles = [a.title for a in articles]
    assert "Old" not in titles and "New" in titles
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/data_platform/test_yahoo_finance_news.py -v
```

Expected: FAIL.

- [ ] **Step 3.3: Implement the changes**

In `app/modules/data_platform/adapters/yahoo_finance.py`, replace the current `get_news()` body (lines ~243-287) and add the two new methods. Full replacement for the `# --- News ---` section:

```python
    # --- News ---

    _SECTOR_ETF_MAP: dict[str, str] = {
        "Technology": "XLK",
        "Financial Services": "XLF",
        "Healthcare": "XLV",
        "Consumer Cyclical": "XLY",
        "Consumer Defensive": "XLP",
        "Energy": "XLE",
        "Industrials": "XLI",
        "Basic Materials": "XLB",
        "Real Estate": "XLRE",
        "Utilities": "XLU",
        "Communication Services": "XLC",
    }

    async def get_news(
        self,
        symbols: list[str] | None = None,
        query: str | None = None,
        since: date | None = None,
        limit: int = 100,
    ) -> list[NewsArticle]:
        target_symbols = symbols or ([query] if query else [])
        return await self._fetch_news_for_symbols(target_symbols[:5], since, limit)

    async def get_market_news(
        self,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]:
        return await self._fetch_news_for_symbols(["SPY"], since, limit)

    async def get_sector_news(
        self,
        sector: str,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]:
        etf = self._SECTOR_ETF_MAP.get(sector)
        if etf is None:
            return []
        return await self._fetch_news_for_symbols([etf], since, limit)

    async def _fetch_news_for_symbols(
        self,
        tickers: list[str],
        since: date | None,
        limit: int,
    ) -> list[NewsArticle]:
        def _fetch():
            articles: list[NewsArticle] = []
            for symbol in tickers:
                ticker = yf.Ticker(symbol)
                news = ticker.news or []
                for item in news[:limit]:
                    content = item.get("content", {})
                    pub_date = content.get("pubDate")
                    if pub_date:
                        try:
                            published = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            published = datetime.now(UTC)
                    else:
                        published = datetime.now(UTC)

                    if since and published.date() < since:
                        continue

                    provider_name = content.get("provider", {}).get("displayName")
                    articles.append(
                        NewsArticle(
                            title=content.get("title", ""),
                            author=None,
                            source=provider_name or "Yahoo Finance",
                            published_at=published,
                            url=content.get("canonicalUrl", {}).get("url", ""),
                            symbols=[symbol],
                        )
                    )
            return articles[:limit]

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)
```

- [ ] **Step 3.4: Run tests**

```bash
.venv/bin/pytest tests/unit/data_platform/test_yahoo_finance_news.py -v
```

Expected: PASS.

- [ ] **Step 3.5: Run full data_platform test directory**

```bash
.venv/bin/pytest tests/unit/data_platform/ -q
```

Expected: all green.

- [ ] **Step 3.6: Lint and stage**

```bash
.venv/bin/ruff check app/modules/data_platform/adapters/yahoo_finance.py tests/unit/data_platform/test_yahoo_finance_news.py
.venv/bin/ruff format app/modules/data_platform/adapters/yahoo_finance.py tests/unit/data_platform/test_yahoo_finance_news.py
git add app/modules/data_platform/adapters/yahoo_finance.py tests/unit/data_platform/test_yahoo_finance_news.py
```

---

## Task 4: Manual news loader

**Files:**
- Create: `app/modules/data_platform/manual_news.py`
- Create: `data/news/manual/.gitkeep`
- Test: `tests/unit/data_platform/test_manual_news.py`

- [ ] **Step 4.1: Write the failing test**

Create `tests/unit/data_platform/test_manual_news.py`:

```python
"""Unit tests for ManualNewsLoader."""

import json
from datetime import date, datetime
from pathlib import Path

from app.modules.data_platform.manual_news import ManualNewsLoader


def _write_day(root: Path, day: str, articles: list[dict]) -> None:
    (root / f"{day}.json").write_text(json.dumps(articles))


def test_returns_empty_when_directory_missing(tmp_path):
    loader = ManualNewsLoader(root=tmp_path / "nonexistent")
    assert loader.load(reference_date=date(2026, 4, 14), window_days=7) == []


def test_returns_empty_when_no_files(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    loader = ManualNewsLoader(root=tmp_path)
    assert loader.load(reference_date=date(2026, 4, 14), window_days=7) == []


def test_loads_articles_within_window(tmp_path):
    _write_day(tmp_path, "2026-04-10", [
        {"title": "A", "source": "Reuters", "published_at": "2026-04-10T10:00:00Z", "scope": "market"},
    ])
    _write_day(tmp_path, "2026-04-14", [
        {"title": "B", "source": "Bloomberg", "published_at": "2026-04-14T10:00:00Z", "scope": "Technology"},
    ])
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    titles = sorted(a["title"] for a in articles)
    assert titles == ["A", "B"]


def test_excludes_articles_outside_window(tmp_path):
    _write_day(tmp_path, "2026-04-01", [
        {"title": "Old", "source": "Reuters", "published_at": "2026-04-01T10:00:00Z", "scope": "market"},
    ])
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []


def test_excludes_future_dates(tmp_path):
    _write_day(tmp_path, "2026-04-20", [
        {"title": "Future", "source": "Reuters", "published_at": "2026-04-20T10:00:00Z", "scope": "market"},
    ])
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []


def test_skips_malformed_files(tmp_path, caplog):
    (tmp_path / "2026-04-14.json").write_text("{not-valid-json")
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []


def test_skips_invalid_filenames(tmp_path):
    _write_day(tmp_path, "not-a-date", [
        {"title": "X", "source": "R", "published_at": "2026-04-14T10:00:00Z", "scope": "market"},
    ])
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []


def test_required_fields_enforced(tmp_path):
    # Missing "scope" → article dropped
    (tmp_path / "2026-04-14.json").write_text(json.dumps([
        {"title": "No scope", "source": "R", "published_at": "2026-04-14T10:00:00Z"},
    ]))
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []


def test_inclusive_boundaries(tmp_path):
    # Day == reference_date → included
    _write_day(tmp_path, "2026-04-14", [
        {"title": "Today", "source": "R", "published_at": "2026-04-14T10:00:00Z", "scope": "market"},
    ])
    # Day == reference_date - window_days → included
    _write_day(tmp_path, "2026-04-07", [
        {"title": "Edge", "source": "R", "published_at": "2026-04-07T10:00:00Z", "scope": "market"},
    ])
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    titles = sorted(a["title"] for a in articles)
    assert titles == ["Edge", "Today"]
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/data_platform/test_manual_news.py -v
```

Expected: FAIL — `ManualNewsLoader` does not exist.

- [ ] **Step 4.3: Implement `ManualNewsLoader`**

Create `app/modules/data_platform/manual_news.py`:

```python
"""ManualNewsLoader — loads date-scoped article files from data/news/manual/*.json.

File format: {YYYY-MM-DD}.json — an array of article dicts. Required fields:
title, source, published_at, scope. Optional: author, url, symbols, sentiment.

On a given reference date, files with dates in the window
[reference_date - window_days, reference_date] are loaded. Malformed files,
invalid filenames, and articles missing required fields are dropped silently
with a debug log — additive inserts must never break a pipeline run.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = ("title", "source", "published_at", "scope")


class ManualNewsLoader:
    """Loads manually-curated articles from a directory of date-scoped JSON files."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def load(
        self,
        reference_date: date,
        window_days: int,
    ) -> list[dict]:
        """Return all articles whose filename date falls within the window.

        Window: [reference_date - window_days, reference_date], inclusive on both ends.
        """
        if not self._root.is_dir():
            return []

        window_start = reference_date - timedelta(days=window_days)

        articles: list[dict] = []
        for file_path in sorted(self._root.glob("*.json")):
            file_date = self._parse_filename_date(file_path)
            if file_date is None:
                continue
            if not (window_start <= file_date <= reference_date):
                continue
            articles.extend(self._load_file(file_path))

        return articles

    @staticmethod
    def _parse_filename_date(path: Path) -> date | None:
        try:
            return date.fromisoformat(path.stem)
        except ValueError:
            logger.debug("Skipping manual-news file with non-ISO filename: %s", path.name)
            return None

    @staticmethod
    def _load_file(path: Path) -> list[dict]:
        try:
            raw = path.read_text()
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            logger.warning("Malformed manual-news file skipped: %s", path.name, exc_info=True)
            return []

        if not isinstance(parsed, list):
            logger.warning("Manual-news file %s is not a JSON array — skipping", path.name)
            return []

        valid: list[dict] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            if not all(field in entry for field in _REQUIRED_FIELDS):
                logger.debug("Manual article missing required fields: %s", entry)
                continue
            valid.append(entry)
        return valid
```

- [ ] **Step 4.4: Create the directory marker**

```bash
mkdir -p data/news/manual
touch data/news/manual/.gitkeep
```

- [ ] **Step 4.5: Run tests**

```bash
.venv/bin/pytest tests/unit/data_platform/test_manual_news.py -v
```

Expected: PASS.

- [ ] **Step 4.6: Lint and stage**

```bash
.venv/bin/ruff check app/modules/data_platform/manual_news.py tests/unit/data_platform/test_manual_news.py
.venv/bin/ruff format app/modules/data_platform/manual_news.py tests/unit/data_platform/test_manual_news.py
git add app/modules/data_platform/manual_news.py tests/unit/data_platform/test_manual_news.py data/news/manual/.gitkeep
```

---

## Task 5: `BacktestNewsAdapter` placeholder

**Files:**
- Create: `app/modules/backtest/adapters/backtest_news.py`
- Test: `tests/unit/backtest/test_backtest_news_adapter.py`

- [ ] **Step 5.1: Write the failing test**

Create `tests/unit/backtest/test_backtest_news_adapter.py`:

```python
"""Unit tests for the BacktestNewsAdapter placeholder."""

from datetime import date

from app.common.interfaces.news import NewsAdapter
from app.modules.backtest.adapters.backtest_news import BacktestNewsAdapter


def test_is_a_news_adapter():
    assert issubclass(BacktestNewsAdapter, NewsAdapter)


async def test_get_market_news_returns_empty():
    adapter = BacktestNewsAdapter()
    assert await adapter.get_market_news() == []
    assert await adapter.get_market_news(since=date(2026, 1, 1), limit=5) == []


async def test_get_sector_news_returns_empty():
    adapter = BacktestNewsAdapter()
    assert await adapter.get_sector_news("Technology") == []
    assert await adapter.get_sector_news("Healthcare", since=date(2026, 1, 1), limit=5) == []


def test_has_name_attribute():
    adapter = BacktestNewsAdapter()
    assert adapter.name == "backtest_news"
```

- [ ] **Step 5.2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/backtest/test_backtest_news_adapter.py -v
```

Expected: FAIL.

- [ ] **Step 5.3: Implement the adapter**

Create `app/modules/backtest/adapters/backtest_news.py`:

```python
"""BacktestNewsAdapter — empty placeholder until a historical news provider is plugged in.

Returns empty lists for both methods. Manual articles are loaded separately by
the prefetch_news graph node, so the news analyst still receives content when
curated articles exist in data/news/manual/.
"""

from __future__ import annotations

from datetime import date

from app.common.interfaces.news import NewsAdapter, NewsArticle


class BacktestNewsAdapter(NewsAdapter):
    name = "backtest_news"

    async def get_market_news(
        self,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]:
        return []

    async def get_sector_news(
        self,
        sector: str,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]:
        return []
```

- [ ] **Step 5.4: Run tests**

```bash
.venv/bin/pytest tests/unit/backtest/test_backtest_news_adapter.py -v
```

Expected: PASS.

- [ ] **Step 5.5: Lint and stage**

```bash
.venv/bin/ruff check app/modules/backtest/adapters/backtest_news.py tests/unit/backtest/test_backtest_news_adapter.py
.venv/bin/ruff format app/modules/backtest/adapters/backtest_news.py tests/unit/backtest/test_backtest_news_adapter.py
git add app/modules/backtest/adapters/backtest_news.py tests/unit/backtest/test_backtest_news_adapter.py
```

---

## Task 6: Window helper function

**Files:**
- Create: `app/modules/equities/news_window.py`
- Test: `tests/unit/equities/test_news_window.py`

- [ ] **Step 6.1: Write the failing test**

Create `tests/unit/equities/test_news_window.py`:

```python
"""Unit tests for news_window.window_days_for_frequency."""

import pytest

from app.modules.equities.news_window import window_days_for_frequency


@pytest.mark.parametrize("freq,expected", [
    ("daily", 1),
    ("weekly", 7),
    ("biweekly", 14),
    ("monthly", 30),
])
def test_known_frequencies(freq, expected):
    assert window_days_for_frequency(freq) == expected


def test_unknown_frequency_defaults_to_weekly():
    assert window_days_for_frequency("quarterly") == 7
    assert window_days_for_frequency("") == 7
    assert window_days_for_frequency(None) == 7
```

- [ ] **Step 6.2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/equities/test_news_window.py -v
```

Expected: FAIL.

- [ ] **Step 6.3: Implement**

Create `app/modules/equities/news_window.py`:

```python
"""Helper mapping rebalance frequency strings to news-window sizes in days."""

from __future__ import annotations

_WINDOW_DAYS: dict[str, int] = {
    "daily": 1,
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
}

_DEFAULT_WINDOW_DAYS = 7


def window_days_for_frequency(frequency: str | None) -> int:
    """Return the news lookback window in days for a given rebalance frequency.

    Unknown or missing frequencies default to 7 (weekly).
    """
    if not frequency:
        return _DEFAULT_WINDOW_DAYS
    return _WINDOW_DAYS.get(frequency, _DEFAULT_WINDOW_DAYS)
```

- [ ] **Step 6.4: Run tests, lint, stage**

```bash
.venv/bin/pytest tests/unit/equities/test_news_window.py -v
.venv/bin/ruff check app/modules/equities/news_window.py tests/unit/equities/test_news_window.py
.venv/bin/ruff format app/modules/equities/news_window.py tests/unit/equities/test_news_window.py
git add app/modules/equities/news_window.py tests/unit/equities/test_news_window.py
```

---

## Task 7: `NewsAnalyst` accepts pre-fetched articles

**Files:**
- Modify: `app/modules/equities/agents/news_analyst.py`
- Modify: `tests/unit/equities/test_news_analyst.py`

The new signature: `analyze(self, stock, articles)`. The old `data_service` dependency is removed. `analyze_batch` takes `(stocks, articles_by_symbol, max_concurrent)`.

- [ ] **Step 7.1: Update existing tests to drive the new signature**

Open `tests/unit/equities/test_news_analyst.py` and rewrite `_make_analyst` plus the affected tests. Full replacement for the file:

```python
"""Unit tests for the News Analyst agent (mocked LLM)."""

from unittest.mock import AsyncMock

import pytest

from app.modules.equities.agents.news_analyst import NewsAnalyst
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import StockSignal, UniverseStock


def _make_stock(**overrides) -> UniverseStock:
    defaults = dict(symbol="AAPL", company_name="Apple Inc.", weight=0.05, sector="Technology")
    defaults.update(overrides)
    return UniverseStock(**defaults)


def _make_llm_response(bullish_score=7, confidence=8, summary="Positive"):
    return {"bullish_score": bullish_score, "confidence": confidence, "summary": summary}


def _make_articles():
    return [
        {"title": "Apple beats earnings", "source": "Reuters", "published_at": "2026-04-14T10:00:00Z"},
        {"title": "iPhone sales surge", "source": "Bloomberg", "published_at": "2026-04-14T11:00:00Z"},
    ]


def _make_analyst(llm_response=None, llm_side_effect=None):
    llm_client = AsyncMock()
    if llm_side_effect:
        llm_client.invoke.side_effect = llm_side_effect
    else:
        llm_client.invoke.return_value = llm_response or _make_llm_response()
    analyst = NewsAnalyst(config=AnalystLLMConfig(), llm_client=llm_client)
    return analyst, llm_client


class TestNewsAnalyst:
    async def test_returns_valid_stock_signal(self):
        analyst, _ = _make_analyst()
        signal = await analyst.analyze(_make_stock(), articles=_make_articles())

        assert isinstance(signal, StockSignal)
        assert signal.symbol == "AAPL"
        assert signal.analyst_type == "news"
        assert signal.bullish_score == 7
        assert signal.confidence == 8

    async def test_empty_articles_are_allowed(self):
        analyst, _ = _make_analyst()
        signal = await analyst.analyze(_make_stock(), articles=[])
        assert isinstance(signal, StockSignal)

    async def test_passes_articles_into_prompt(self):
        analyst, llm_client = _make_analyst()
        await analyst.analyze(_make_stock(), articles=_make_articles())

        user_prompt = llm_client.invoke.call_args.args[0]
        assert "Apple beats earnings" in user_prompt
        assert "iPhone sales surge" in user_prompt

    async def test_does_not_call_data_service(self):
        # The analyst no longer fetches its own news — ensure no data_service attr is used.
        analyst, _ = _make_analyst()
        assert not hasattr(analyst, "data_service") or analyst.data_service is None
        await analyst.analyze(_make_stock(), articles=_make_articles())

    async def test_handles_malformed_llm_response(self):
        analyst, _ = _make_analyst(llm_response={"bullish_score": 3})
        signal = await analyst.analyze(_make_stock(), articles=_make_articles())
        assert signal.bullish_score == 3
        assert signal.confidence == 5  # default
        assert signal.summary == "No analysis available."

    async def test_propagates_llm_errors(self):
        analyst, _ = _make_analyst(llm_side_effect=TimeoutError("timeout"))
        with pytest.raises(TimeoutError):
            await analyst.analyze(_make_stock(), articles=_make_articles())

    async def test_branch_name_selects_overlay(self):
        analyst, llm_client = _make_analyst()
        analyst.branch_name = "growth"
        await analyst.analyze(_make_stock(sector="Technology"), articles=_make_articles())

        system_prompt = llm_client.invoke.call_args.kwargs["system_prompt"]
        assert "News Analyst" in system_prompt
        assert "Growth Branch" in system_prompt


class TestAnalyzeBatch:
    async def test_distributes_articles_per_symbol(self):
        analyst, _ = _make_analyst()
        stocks = [_make_stock(symbol="AAPL"), _make_stock(symbol="MSFT")]
        articles_by_symbol = {
            "AAPL": _make_articles(),
            "MSFT": [{"title": "MSFT news", "source": "WSJ", "published_at": "2026-04-14T10:00:00Z"}],
        }

        signals = await analyst.analyze_batch(stocks, articles_by_symbol=articles_by_symbol, max_concurrent=2)

        assert len(signals) == 2
        assert {s.symbol for s in signals} == {"AAPL", "MSFT"}

    async def test_missing_symbol_in_mapping_uses_empty_list(self):
        analyst, _ = _make_analyst()
        stock = _make_stock(symbol="ZZZ")

        signals = await analyst.analyze_batch([stock], articles_by_symbol={}, max_concurrent=2)

        assert len(signals) == 1
        assert signals[0].symbol == "ZZZ"

    async def test_failure_yields_neutral_fallback(self):
        analyst, _ = _make_analyst(llm_side_effect=Exception("boom"))
        stock = _make_stock()

        signals = await analyst.analyze_batch(
            [stock], articles_by_symbol={"AAPL": _make_articles()}, max_concurrent=2,
        )

        assert len(signals) == 1
        assert signals[0].bullish_score == 5
        assert signals[0].confidence == 1
```

- [ ] **Step 7.2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/unit/equities/test_news_analyst.py -v
```

Expected: FAIL — signature mismatch.

- [ ] **Step 7.3: Update `NewsAnalyst`**

Replace `app/modules/equities/agents/news_analyst.py` with:

```python
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.modules.equities.agents.context_formatters import format_news_context
from app.modules.equities.agents.skills.loader import compose_system_prompt
from app.modules.equities.config import AnalystLLMConfig
from app.modules.equities.models import StockSignal, UniverseStock

logger = logging.getLogger(__name__)


class NewsAnalyst:
    """Assesses recent news sentiment, catalysts, and risks.

    Articles are pre-fetched by the prefetch_news graph node and passed in
    via `analyze(stock, articles)`. The analyst does not fetch its own data.
    """

    ANALYST_TYPE = "news"

    def __init__(
        self,
        config: AnalystLLMConfig,
        llm_client=None,
        branch_name: str = "",
        skills_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.llm_client = llm_client
        self.branch_name = branch_name
        self.skills_dir = skills_dir

    async def analyze(
        self,
        stock: UniverseStock,
        articles: list[dict],
    ) -> StockSignal:
        context = format_news_context(
            symbol=stock.symbol,
            company_name=stock.company_name,
            sector=stock.sector,
            articles=(articles or [])[:20],
        )
        system_prompt = compose_system_prompt(
            self.ANALYST_TYPE, self.branch_name, stock.sector, self.skills_dir,
        )
        prompt = f"{context}\n\nAnalyze this stock based on the data above and your instructions."
        result = await self.llm_client.invoke(prompt, system_prompt=system_prompt)
        if isinstance(result, StockSignal):
            return result
        return StockSignal(
            symbol=stock.symbol,
            analyst_type=self.ANALYST_TYPE,
            bullish_score=result.get("bullish_score", 5),
            confidence=result.get("confidence", 5),
            summary=result.get("summary", "No analysis available."),
        )

    async def analyze_batch(
        self,
        stocks: list[UniverseStock],
        articles_by_symbol: dict[str, list[dict]] | None = None,
        max_concurrent: int = 10,
    ) -> list[StockSignal]:
        if not stocks:
            return []
        articles_by_symbol = articles_by_symbol or {}
        sem = asyncio.Semaphore(max_concurrent)

        async def _limited(s: UniverseStock) -> StockSignal:
            async with sem:
                try:
                    return await self.analyze(s, articles=articles_by_symbol.get(s.symbol, []))
                except Exception:
                    logger.warning(
                        "%s: analyze failed for %s",
                        self.__class__.__name__,
                        s.symbol,
                        exc_info=True,
                    )
                    return StockSignal(
                        symbol=s.symbol,
                        analyst_type=self.ANALYST_TYPE,
                        bullish_score=5,
                        confidence=1,
                        summary="Analysis failed — neutral fallback signal.",
                    )

        return list(await asyncio.gather(*(_limited(s) for s in stocks)))
```

- [ ] **Step 7.4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/equities/test_news_analyst.py -v
```

Expected: PASS.

- [ ] **Step 7.5: Lint and stage**

```bash
.venv/bin/ruff check app/modules/equities/agents/news_analyst.py tests/unit/equities/test_news_analyst.py
.venv/bin/ruff format app/modules/equities/agents/news_analyst.py tests/unit/equities/test_news_analyst.py
git add app/modules/equities/agents/news_analyst.py tests/unit/equities/test_news_analyst.py
```

---

## Task 8: `CachedAnalystWrapper` forwards articles (news-only)

**Files:**
- Modify: `app/modules/backtest/llm_analyst_cache.py`
- Test: `tests/unit/backtest/test_llm_analyst_cache.py` (append or create if absent)

The wrapper is shared by all three analyst types. Only the news analyst's `analyze` takes an extra `articles` kwarg. We'll pass `articles_by_symbol` through the wrapper's `analyze_batch` optionally; when set, `analyze(stock)` looks up the symbol's articles and forwards them.

- [ ] **Step 8.1: Write the failing test**

Append to `tests/unit/backtest/test_llm_analyst_cache.py` (or create if it does not exist):

```python
"""Tests for CachedAnalystWrapper's article-forwarding behavior."""

from datetime import date
from unittest.mock import AsyncMock

from app.modules.backtest.llm_analyst_cache import CachedAnalystWrapper
from app.modules.equities.models import StockSignal, UniverseStock


def _stock(sym="AAPL"):
    return UniverseStock(symbol=sym, company_name=f"{sym} Inc", weight=0.05, sector="Technology")


async def test_news_wrapper_forwards_articles():
    inner = AsyncMock()
    inner.analyze.return_value = StockSignal(
        symbol="AAPL", analyst_type="news", bullish_score=7, confidence=7, summary="ok",
    )
    w = CachedAnalystWrapper(
        analyst=inner,
        analyst_type="news",
        cache={},
        llm_counter=[0],
        max_calls_per_rebalance=60,
        cache_enabled=True,
        current_date_fn=lambda: date(2026, 4, 14),
    )

    articles = [{"title": "X", "source": "R", "published_at": "2026-04-14T10:00:00Z"}]
    await w.analyze_batch([_stock()], articles_by_symbol={"AAPL": articles}, max_concurrent=2)

    inner.analyze.assert_awaited_once_with(_stock(), articles=articles)


async def test_non_news_wrapper_ignores_articles():
    inner = AsyncMock()
    inner.analyze.return_value = StockSignal(
        symbol="AAPL", analyst_type="fundamentals", bullish_score=6, confidence=6, summary="ok",
    )
    w = CachedAnalystWrapper(
        analyst=inner,
        analyst_type="fundamentals",
        cache={},
        llm_counter=[0],
        max_calls_per_rebalance=60,
        cache_enabled=True,
        current_date_fn=lambda: date(2026, 4, 14),
    )

    await w.analyze_batch([_stock()], articles_by_symbol={"AAPL": [{"title": "X"}]}, max_concurrent=2)

    # Fundamentals analyst should be called without the `articles` kwarg
    inner.analyze.assert_awaited_once_with(_stock())
```

- [ ] **Step 8.2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/backtest/test_llm_analyst_cache.py -v
```

Expected: FAIL.

- [ ] **Step 8.3: Update the wrapper**

In `app/modules/backtest/llm_analyst_cache.py`, replace the body of `CachedAnalystWrapper` (keep the existing `__init__`, `reset_rebalance_counter` untouched). Replace `analyze` and `analyze_batch` as shown:

```python
    async def analyze(
        self,
        stock: UniverseStock,
        articles: list[dict] | None = None,
    ) -> StockSignal:
        today = self._current_date_fn()
        cache_key = (today, stock.symbol, self._analyst_type)

        if self._cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]

        if self._llm_counter[0] >= self._max_calls:
            logger.warning(
                "LLM call cap reached (%d/%d) — neutral fallback for %s (%s)",
                self._llm_counter[0], self._max_calls, stock.symbol, self._analyst_type,
            )
            return StockSignal(
                symbol=stock.symbol,
                analyst_type=self._analyst_type,
                bullish_score=_NEUTRAL_FALLBACK_SCORE,
                confidence=_NEUTRAL_FALLBACK_CONFIDENCE,
                summary=f"LLM cap reached ({self._max_calls} calls/rebalance).",
            )

        self._llm_counter[0] += 1
        if self._analyst_type == "news":
            signal = await self._analyst.analyze(stock, articles=articles or [])
        else:
            signal = await self._analyst.analyze(stock)

        if self._cache_enabled:
            self._cache[cache_key] = signal
        return signal

    async def analyze_batch(
        self,
        stocks: list[UniverseStock],
        articles_by_symbol: dict[str, list[dict]] | None = None,
        max_concurrent: int = 10,
    ) -> list[StockSignal]:
        if not stocks:
            return []
        sem = asyncio.Semaphore(max_concurrent)
        mapping = articles_by_symbol or {}

        async def _limited(s: UniverseStock) -> StockSignal:
            async with sem:
                try:
                    if self._analyst_type == "news":
                        return await self.analyze(s, articles=mapping.get(s.symbol, []))
                    return await self.analyze(s)
                except Exception:
                    logger.warning(
                        "CachedAnalystWrapper: analyze failed for %s (%s)",
                        s.symbol, self._analyst_type, exc_info=True,
                    )
                    return StockSignal(
                        symbol=s.symbol,
                        analyst_type=self._analyst_type,
                        bullish_score=_NEUTRAL_FALLBACK_SCORE,
                        confidence=_NEUTRAL_FALLBACK_CONFIDENCE,
                        summary="Analysis failed — neutral fallback signal.",
                    )

        return list(await asyncio.gather(*(_limited(s) for s in stocks)))
```

- [ ] **Step 8.4: Run tests**

```bash
.venv/bin/pytest tests/unit/backtest/test_llm_analyst_cache.py -v
```

Expected: PASS.

- [ ] **Step 8.5: Lint and stage**

```bash
.venv/bin/ruff check app/modules/backtest/llm_analyst_cache.py tests/unit/backtest/test_llm_analyst_cache.py
.venv/bin/ruff format app/modules/backtest/llm_analyst_cache.py tests/unit/backtest/test_llm_analyst_cache.py
git add app/modules/backtest/llm_analyst_cache.py tests/unit/backtest/test_llm_analyst_cache.py
```

---

## Task 9: `prefetch_news` graph node + `news_context` state field

**Files:**
- Modify: `app/modules/equities/agents/graph.py`
- Test: `tests/unit/equities/test_news_prefetch.py`

- [ ] **Step 9.1: Write the failing test**

Create `tests/unit/equities/test_news_prefetch.py`:

```python
"""Unit tests for the prefetch_news graph node and news_analysis merge logic."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.equities.agents.graph import build_equities_graph
from app.modules.equities.models import StockSignal, UniverseStock


def _stock(sym="AAPL", sector="Technology"):
    return UniverseStock(symbol=sym, company_name=f"{sym} Inc", weight=0.05, sector=sector)


def _mkdeps(**overrides):
    # A minimally-wired deps dict that exercises just the prefetch path.
    data_service = AsyncMock()
    data_service.get_market_news = AsyncMock(return_value={
        "articles": [{"title": "Market up", "source": "Reuters", "published_at": "2026-04-14T10:00:00Z"}],
        "source": "mock",
    })
    data_service.get_sector_news = AsyncMock(return_value={
        "articles": [{"title": "Tech leads", "source": "Bloomberg", "published_at": "2026-04-14T10:00:00Z"}],
        "source": "mock",
    })
    data_service.get_current_price = AsyncMock(return_value=100.0)

    news_analyst = AsyncMock()
    news_analyst.analyze_batch = AsyncMock(return_value=[
        StockSignal(symbol="AAPL", analyst_type="news", bullish_score=6, confidence=6, summary="x"),
    ])
    fundamentals_analyst = AsyncMock()
    fundamentals_analyst.analyze_batch = AsyncMock(return_value=[
        StockSignal(symbol="AAPL", analyst_type="fundamentals", bullish_score=6, confidence=6, summary="x"),
    ])
    technical_analyst = AsyncMock()
    technical_analyst.analyze_batch = AsyncMock(return_value=[
        StockSignal(symbol="AAPL", analyst_type="technical", bullish_score=6, confidence=6, summary="x"),
    ])

    pm = MagicMock()
    pm.compute_composite_scores.return_value = []
    pm.select_stocks.return_value = []
    pm.size_positions.return_value = []
    pm.generate_orders.return_value = []

    deps = {
        "universe_provider": AsyncMock(),
        "screener": AsyncMock(),
        "data_service": data_service,
        "news_analyst": news_analyst,
        "fundamentals_analyst": fundamentals_analyst,
        "technical_analyst": technical_analyst,
        "portfolio_manager": pm,
        "current_positions": {},
        "nav": 1_000_000.0,
        "execute_trade_fn": None,
        "max_concurrent_analyses": 10,
        "as_of_date": date(2026, 4, 14),
        "news_window_days": 7,
        "manual_news_root": None,
    }
    deps.update(overrides)
    return deps


async def test_prefetch_calls_market_and_per_sector():
    deps = _mkdeps()
    screened = [_stock("AAPL", "Technology"), _stock("JPM", "Financial Services")]

    # Invoke the compiled graph through prefetch_news (but short-circuit after by
    # using empty analyst outputs). Because the real pipeline runs many nodes,
    # we instead construct the prefetch_news node in isolation:
    graph = build_equities_graph("growth")
    # The compiled graph doesn't expose individual nodes. Exercise the node
    # directly by importing the underlying build function and running the inner
    # function. See the implementation for the exact API used here.
    from app.modules.equities.agents.graph import _run_prefetch_news  # exposed for tests

    state = {"screened": screened, "deps": deps}
    result = await _run_prefetch_news(state)

    ctx = result["news_context"]
    deps["data_service"].get_market_news.assert_awaited_once()
    assert deps["data_service"].get_sector_news.await_count == 2
    assert set(ctx["sectors"].keys()) == {"Technology", "Financial Services"}
    assert len(ctx["market"]) == 1


async def test_prefetch_deduplicates_sectors():
    deps = _mkdeps()
    screened = [_stock("AAPL", "Technology"), _stock("MSFT", "Technology")]

    from app.modules.equities.agents.graph import _run_prefetch_news

    result = await _run_prefetch_news({"screened": screened, "deps": deps})

    assert deps["data_service"].get_sector_news.await_count == 1  # single sector


async def test_prefetch_handles_adapter_failure_gracefully():
    deps = _mkdeps()
    deps["data_service"].get_market_news.side_effect = Exception("down")
    deps["data_service"].get_sector_news.side_effect = Exception("down")
    screened = [_stock()]

    from app.modules.equities.agents.graph import _run_prefetch_news

    result = await _run_prefetch_news({"screened": screened, "deps": deps})

    ctx = result["news_context"]
    assert ctx["market"] == []
    assert ctx["sectors"]["Technology"] == []
    assert ctx["manual"] == []


async def test_prefetch_loads_manual_articles(tmp_path):
    import json
    (tmp_path / "2026-04-14.json").write_text(json.dumps([
        {"title": "Fed holds", "source": "Reuters", "published_at": "2026-04-14T10:00:00Z", "scope": "market"},
        {"title": "AI momentum", "source": "Bloomberg", "published_at": "2026-04-14T10:00:00Z", "scope": "Technology"},
    ]))
    deps = _mkdeps(manual_news_root=tmp_path)

    from app.modules.equities.agents.graph import _run_prefetch_news

    result = await _run_prefetch_news({"screened": [_stock()], "deps": deps})

    ctx = result["news_context"]
    titles = [a["title"] for a in ctx["manual"]]
    assert "Fed holds" in titles and "AI momentum" in titles


async def test_news_analysis_merges_and_passes_per_stock_articles():
    deps = _mkdeps()
    news_analyst = deps["news_analyst"]
    screened = [_stock("AAPL", "Technology")]

    from app.modules.equities.agents.graph import _run_news_analysis

    news_context = {
        "market": [{"title": "Market up", "source": "R", "published_at": "2026-04-14T10:00:00Z"}],
        "sectors": {"Technology": [{"title": "Tech leads", "source": "B", "published_at": "2026-04-14T10:00:00Z"}]},
        "manual": [
            {"title": "Fed", "source": "R", "published_at": "2026-04-14T10:00:00Z", "scope": "market"},
            {"title": "Tech AI", "source": "B", "published_at": "2026-04-14T10:00:00Z", "scope": "Technology"},
            {"title": "Banks", "source": "B", "published_at": "2026-04-14T10:00:00Z", "scope": "Financial Services"},
        ],
    }
    state = {"screened": screened, "news_context": news_context, "deps": deps}

    await _run_news_analysis(state)

    call_kwargs = news_analyst.analyze_batch.call_args.kwargs
    articles_by_symbol = call_kwargs["articles_by_symbol"]
    titles = [a["title"] for a in articles_by_symbol["AAPL"]]
    # Market + Technology sector + market-scoped manual + Technology-scoped manual
    assert "Market up" in titles
    assert "Tech leads" in titles
    assert "Fed" in titles
    assert "Tech AI" in titles
    # Banks (Financial Services scope) must NOT leak into AAPL's Technology bundle
    assert "Banks" not in titles
```

- [ ] **Step 9.2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/equities/test_news_prefetch.py -v
```

Expected: FAIL — `_run_prefetch_news` / `_run_news_analysis` do not exist.

- [ ] **Step 9.3: Update `graph.py`**

Replace the contents of `app/modules/equities/agents/graph.py` with:

```python
from __future__ import annotations

import logging
import operator
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph

from app.modules.data_platform.manual_news import ManualNewsLoader

logger = logging.getLogger(__name__)


class EquitiesWorkflowState(TypedDict, total=False):
    """State passed through the LangGraph workflow."""

    branch_name: str
    branch_id: str
    universe: list
    screened: list
    signals: Annotated[list, operator.add]
    scores: list
    orders: list
    trades: list
    deps: dict
    news_context: dict


def _window_start(as_of: date | None, window_days: int) -> date | None:
    if as_of is None:
        return None
    return as_of - timedelta(days=window_days)


async def _run_prefetch_news(state: EquitiesWorkflowState) -> dict:
    """Fetch market + per-sector news once per rebalance and load manual articles."""
    deps = state["deps"]
    data_service = deps["data_service"]
    as_of_date = deps.get("as_of_date")
    window_days = deps.get("news_window_days", 7)
    manual_root: Path | None = deps.get("manual_news_root")

    since = _window_start(as_of_date, window_days)

    # 1. Market news (one call)
    market_articles: list[dict] = []
    try:
        market_result = await data_service.get_market_news(since=since, limit=20)
        market_articles = list(market_result.get("articles", []))
    except Exception:
        logger.warning("Market news fetch failed; continuing with empty market news", exc_info=True)

    # 2. Sector news (one call per unique sector)
    unique_sectors = sorted({s.sector for s in state.get("screened", []) if s.sector})
    sector_articles: dict[str, list[dict]] = {}
    for sector in unique_sectors:
        try:
            sector_result = await data_service.get_sector_news(sector, since=since, limit=20)
            sector_articles[sector] = list(sector_result.get("articles", []))
        except Exception:
            logger.warning("Sector news fetch failed for %s", sector, exc_info=True)
            sector_articles[sector] = []

    # 3. Manual articles (file-based, additive)
    manual_articles: list[dict] = []
    if manual_root is not None and as_of_date is not None:
        try:
            loader = ManualNewsLoader(root=manual_root)
            manual_articles = loader.load(reference_date=as_of_date, window_days=window_days)
        except Exception:
            logger.warning("Manual news load failed", exc_info=True)

    logger.info(
        "News prefetch: %d market + %d sectors (%d unique) + %d manual",
        len(market_articles), sum(len(v) for v in sector_articles.values()),
        len(unique_sectors), len(manual_articles),
    )

    return {
        "news_context": {
            "market": market_articles,
            "sectors": sector_articles,
            "manual": manual_articles,
        }
    }


def _articles_for_stock(stock, news_context: dict) -> list[dict]:
    """Merge market + stock-sector articles + manual articles scoped to market or the stock's sector."""
    market = list(news_context.get("market", []))
    sector_map = news_context.get("sectors", {})
    sector_articles = list(sector_map.get(stock.sector, [])) if stock.sector else []
    manual = news_context.get("manual", [])
    manual_matching = [
        a for a in manual
        if a.get("scope") == "market" or (stock.sector and a.get("scope") == stock.sector)
    ]
    return market + sector_articles + manual_matching


async def _run_news_analysis(state: EquitiesWorkflowState) -> dict:
    deps = state["deps"]
    analyst = deps["news_analyst"]
    max_concurrent = deps.get("max_concurrent_analyses", 10)
    screened = state["screened"]
    news_context = state.get("news_context", {"market": [], "sectors": {}, "manual": []})

    articles_by_symbol = {s.symbol: _articles_for_stock(s, news_context) for s in screened}
    signals = await analyst.analyze_batch(
        screened,
        articles_by_symbol=articles_by_symbol,
        max_concurrent=max_concurrent,
    )
    logger.info("News analyst produced %d signals", len(signals))
    return {"signals": list(signals)}


def build_equities_graph(branch_name: str):
    """Builds the LangGraph workflow for one equities branch."""

    async def fetch_universe(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        provider = deps["universe_provider"]
        as_of_date = deps.get("as_of_date")
        b_name = state.get("branch_name", "")
        holdings = await provider.get_holdings(b_name, as_of_date=as_of_date)
        logger.info("Fetched %d holdings for '%s' branch", len(holdings), b_name)
        return {"universe": holdings}

    async def screen_stocks(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        screener = deps["screener"]
        data_service = deps["data_service"]
        as_of_date = deps.get("as_of_date")
        screened = await screener.screen(state["universe"], data_service, as_of_date=as_of_date)
        logger.info("Screened %d -> %d stocks", len(state["universe"]), len(screened))
        return {"screened": screened}

    async def prefetch_news(state: EquitiesWorkflowState) -> dict:
        return await _run_prefetch_news(state)

    async def news_analysis(state: EquitiesWorkflowState) -> dict:
        return await _run_news_analysis(state)

    async def fundamentals_analysis(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        analyst = deps["fundamentals_analyst"]
        max_concurrent = deps.get("max_concurrent_analyses", 10)
        signals = await analyst.analyze_batch(state["screened"], max_concurrent=max_concurrent)
        logger.info("Fundamentals analyst produced %d signals", len(signals))
        return {"signals": list(signals)}

    async def technical_analysis(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        analyst = deps["technical_analyst"]
        max_concurrent = deps.get("max_concurrent_analyses", 10)
        signals = await analyst.analyze_batch(state["screened"], max_concurrent=max_concurrent)
        logger.info("Technical analyst produced %d signals", len(signals))
        return {"signals": list(signals)}

    async def portfolio_decision(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        pm = deps["portfolio_manager"]
        data_service = deps["data_service"]
        current_positions = deps.get("current_positions", {})
        nav = deps.get("nav", 1_000_000.0)
        scores = pm.compute_composite_scores(state["signals"])
        selected = pm.select_stocks(scores)
        sized = pm.size_positions(selected)
        prices: dict[str, float] = {}
        for s in sized:
            price = await data_service.get_current_price(s.symbol)
            if price:
                prices[s.symbol] = price
        for sym in current_positions:
            if sym not in prices:
                price = await data_service.get_current_price(sym)
                if price:
                    prices[sym] = price
        orders = pm.generate_orders(sized, current_positions, nav, prices)
        logger.info("Portfolio manager generated %d orders", len(orders))
        return {"scores": scores, "orders": orders}

    async def execute_trades(state: EquitiesWorkflowState) -> dict:
        deps = state["deps"]
        trade_fn = deps.get("execute_trade_fn")
        trades = []
        if trade_fn:
            for order in state.get("orders", []):
                trade = await trade_fn(order)
                if trade:
                    trades.append(trade)
        logger.info("Executed %d trades", len(trades))
        return {"trades": trades}

    graph = StateGraph(EquitiesWorkflowState)
    graph.add_node("fetch_universe", fetch_universe)
    graph.add_node("screen_stocks", screen_stocks)
    graph.add_node("prefetch_news", prefetch_news)
    graph.add_node("news_analysis", news_analysis)
    graph.add_node("fundamentals_analysis", fundamentals_analysis)
    graph.add_node("technical_analysis", technical_analysis)
    graph.add_node("portfolio_decision", portfolio_decision)
    graph.add_node("execute_trades", execute_trades)

    graph.set_entry_point("fetch_universe")
    graph.add_edge("fetch_universe", "screen_stocks")
    graph.add_edge("screen_stocks", "prefetch_news")
    graph.add_edge("prefetch_news", "news_analysis")
    graph.add_edge("screen_stocks", "fundamentals_analysis")
    graph.add_edge("screen_stocks", "technical_analysis")
    graph.add_edge("news_analysis", "portfolio_decision")
    graph.add_edge("fundamentals_analysis", "portfolio_decision")
    graph.add_edge("technical_analysis", "portfolio_decision")
    graph.add_edge("portfolio_decision", "execute_trades")
    graph.add_edge("execute_trades", END)

    return graph.compile()
```

Note: fundamentals/technical analysts still branch off `screen_stocks` directly (they don't need news). Only news analyzers depend on `prefetch_news`.

- [ ] **Step 9.4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/equities/test_news_prefetch.py -v
```

Expected: PASS.

- [ ] **Step 9.5: Run the full equities test directory**

```bash
.venv/bin/pytest tests/unit/equities/ -q
```

Expected: all green. The existing `test_graph.py` tests don't exercise the new node and should still pass.

- [ ] **Step 9.6: Lint and stage**

```bash
.venv/bin/ruff check app/modules/equities/agents/graph.py tests/unit/equities/test_news_prefetch.py
.venv/bin/ruff format app/modules/equities/agents/graph.py tests/unit/equities/test_news_prefetch.py
git add app/modules/equities/agents/graph.py tests/unit/equities/test_news_prefetch.py
```

---

## Task 10: Wire dependencies in `EquitiesBranchService` and composition roots

**Files:**
- Modify: `app/modules/equities/service.py`
- Modify: `app/dependencies.py`
- Modify: `app/modules/backtest/context.py`

- [ ] **Step 10.1: Update `EquitiesBranchService.run_pipeline` deps dict**

In `app/modules/equities/service.py`, find the `initial_state["deps"]` dict (around line 266-280) and add two keys:

```python
# existing keys...
"max_concurrent_analyses": self.config.agents.max_concurrent_analyses,
"as_of_date": as_of_date,
"news_window_days": 7,  # live mode defaults to weekly; backtest overrides this
"manual_news_root": Path("data/news/manual"),
```

Add the import at the top of the file:

```python
from pathlib import Path
```

- [ ] **Step 10.2: Update `app/dependencies.py` — drop `data_service` kwarg from `NewsAnalyst`**

In `app/dependencies.py`, find the `NewsAnalyst(...)` construction inside `init_services()` and remove the `data_service=data_platform_service,` line:

```python
# Before
news_analyst=NewsAnalyst(
    config=equities_config.agents.news_analyst,
    data_service=data_platform_service,
    llm_client=news_llm,
),

# After
news_analyst=NewsAnalyst(
    config=equities_config.agents.news_analyst,
    llm_client=news_llm,
),
```

- [ ] **Step 10.3: Update `app/modules/backtest/context.py` — register `BacktestNewsAdapter` and wire window/manual-root**

In `app/modules/backtest/context.py`:

1. Add imports at the top (after existing imports):

```python
from app.modules.backtest.adapters.backtest_news import BacktestNewsAdapter
from app.modules.equities.news_window import window_days_for_frequency
```

2. Find the adapter_registry block (around line 168-180) and add the `"news"` key:

```python
# Before
data_service = DataPlatformService(
    adapter_registry={
        "prices": {
            "equity": [adapter],
            "all": [adapter],
        },
        "fundamentals": {
            "equity": [adapter],
        },
    },
    cache=NoOpCache(),
    rate_limiter=NoOpRateLimiter(),
)

# After
news_adapter = BacktestNewsAdapter()
data_service = DataPlatformService(
    adapter_registry={
        "prices": {
            "equity": [adapter],
            "all": [adapter],
        },
        "fundamentals": {
            "equity": [adapter],
        },
        "news": {
            "all": [news_adapter],
        },
    },
    cache=NoOpCache(),
    rate_limiter=NoOpRateLimiter(),
)
```

3. In the `NewsAnalyst(...)` construction inside the `use_llm_agents` branch (around line 250), remove `data_service=data_service`:

```python
# Before
raw_news = NewsAnalyst(
    config=llm_cfg.news_analyst,
    data_service=data_service,
    llm_client=AnthropicAnalystClient(...),
    skills_dir=skills_dir,
)

# After
raw_news = NewsAnalyst(
    config=llm_cfg.news_analyst,
    llm_client=AnthropicAnalystClient(...),
    skills_dir=skills_dir,
)
```

4. Attach the news window and manual root to the context so `EquitiesBranchService` can propagate. Since `run_pipeline` builds its deps dict, we need `EquitiesBranchService` to read these from the context. Simplest: override the deps values after building. Look for the `run_pipeline` wrapper in `BacktestEngine` — it passes `as_of_date` already. We'll follow the same pattern:

Find where `equities_service.run_pipeline()` is called by the engine (search for `run_pipeline` in `app/modules/backtest/engine.py`) and ensure `as_of_date=tp.today()` is already being passed.

Then, to push `news_window_days` + `manual_news_root` through, the easiest surface is to let `EquitiesBranchService.run_pipeline` accept them as optional kwargs. Update `run_pipeline` signature and defaults:

In `app/modules/equities/service.py`:

```python
async def run_pipeline(
    self,
    branch_name: str,
    branch_id: str,
    trade_execution_service: TradeExecutionService | None = None,
    portfolio_service: PortfolioService | None = None,
    event_log_repo: EventLogRepository | None = None,
    session: AsyncSession | None = None,
    instrument_ids: dict[str, str] | None = None,
    as_of_date=None,
    news_window_days: int = 7,
    manual_news_root: Path | None = Path("data/news/manual"),
) -> RunResult:
```

And in the deps dict, replace the hardcoded values:

```python
"news_window_days": news_window_days,
"manual_news_root": manual_news_root,
```

5. In `BacktestEngine` (wherever `equities_service.run_pipeline(...)` is called — likely in `app/modules/backtest/engine.py`), pass the window derived from `config.rebalance_frequency`. Inspect the file first:

```bash
.venv/bin/pytest --collect-only tests/unit/backtest/ -q | head -20
grep -n "run_pipeline" app/modules/backtest/engine.py
```

Then add to the call site:

```python
window_days = window_days_for_frequency(config.rebalance_frequency.value)
# existing call:
await equities_service.run_pipeline(
    branch_name=config.branch_name,
    branch_id=config.branch_name,  # as used today
    ...,
    as_of_date=tp.today(),
    news_window_days=window_days,
    manual_news_root=Path("data/news/manual"),
)
```

- [ ] **Step 10.4: Run all unit tests to verify no regressions**

```bash
.venv/bin/pytest tests/unit/ -q
```

Expected: all green.

- [ ] **Step 10.5: Lint and stage**

```bash
.venv/bin/ruff check app/modules/equities/service.py app/dependencies.py app/modules/backtest/context.py app/modules/backtest/engine.py
.venv/bin/ruff format app/modules/equities/service.py app/dependencies.py app/modules/backtest/context.py app/modules/backtest/engine.py
git add app/modules/equities/service.py app/dependencies.py app/modules/backtest/context.py app/modules/backtest/engine.py
```

---

## Task 11: Full suite + smoke backtest verification

- [ ] **Step 11.1: Full unit test run**

```bash
.venv/bin/pytest tests/unit/ -q
```

Expected: all green.

- [ ] **Step 11.2: Lint the whole touched tree**

```bash
.venv/bin/ruff check app/ tests/
```

Expected: zero issues.

- [ ] **Step 11.3: Run a quick backtest smoke test (no LLM, no DB)**

```bash
.venv/bin/python scripts/run_backtest.py 2025-01-01 2025-03-31 growth --top-n 10
```

Expected: runs to completion and prints metrics. This exercises the non-LLM path and verifies the `prefetch_news` node runs without breaking the quantitative flow (the news adapter returns empty; the quantitative news analyst doesn't receive articles but also doesn't need them since it's not wrapped).

Note: `QuantitativeNewsAnalyst.analyze(stock)` does not accept an `articles` kwarg. With the new `news_analysis` node, the graph calls `analyst.analyze_batch(screened, articles_by_symbol=..., max_concurrent=N)`. Verify that `QuantitativeNewsAnalyst.analyze_batch` accepts `**kwargs` (it does, per `quantitative_analysts.py:133`). Good — no quantitative-path changes needed.

- [ ] **Step 11.4: Run an LLM-mode backtest smoke test (only if ANTHROPIC_API_KEY is set)**

```bash
test -n "$ANTHROPIC_API_KEY" && .venv/bin/python -m scripts.run_backtest 2025-01-01 2025-03-31 growth --top-n 5 --llm --save
```

Expected: runs to completion; the news adapter serves empty articles, so news-analyst signals will be low-confidence neutrals (but from actual LLM reasoning, not the exception fallback).

- [ ] **Step 11.5: Stage any remaining untracked files and show summary**

```bash
git status --short
git diff --cached --stat
```

The user will commit on main.

---

## Self-Review Checklist

**Spec coverage:**
- Adapter interface with `get_market_news` / `get_sector_news` → Task 1 ✓
- `DataPlatformService` pass-through methods → Task 2 ✓
- `YahooFinanceAdapter` new methods + source/author bug fix → Task 3 ✓
- Manual articles file loader → Task 4 ✓
- `BacktestNewsAdapter` placeholder → Task 5 ✓
- Window sizing tied to rebalance frequency → Task 6 ✓
- `NewsAnalyst` new signature, no `data_service` → Task 7 ✓
- `CachedAnalystWrapper` forwards articles for news only → Task 8 ✓
- `prefetch_news` graph node + `news_context` state → Task 9 ✓
- Composition-root wiring (live + backtest) → Task 10 ✓
- Full pipeline smoke test → Task 11 ✓
- `SectorMapping` helper: mentioned in spec but not strictly needed for MVP since `YahooFinanceAdapter` uses a private `_SECTOR_ETF_MAP` dict directly. Deferred — add when a second adapter is introduced, per YAGNI.

**Placeholder scan:** No "TBD", "TODO", or "add appropriate handling" stubs. Each step has concrete code or a concrete command.

**Type consistency:**
- `analyze(stock, articles)` — consistent across Tasks 7, 8, 9
- `analyze_batch(stocks, articles_by_symbol, max_concurrent)` — consistent across Tasks 7, 8, 9
- `news_context` shape `{"market": [...], "sectors": {...}, "manual": [...]}` — consistent across Tasks 9, 10
- `ManualNewsLoader(root).load(reference_date, window_days)` — consistent across Tasks 4, 9
- `BacktestNewsAdapter.name = "backtest_news"` — matches `DataPlatformService` iteration pattern

**Scope check:** Single focused project. ~10 files new, ~8 files modified. Suitable for one plan.
