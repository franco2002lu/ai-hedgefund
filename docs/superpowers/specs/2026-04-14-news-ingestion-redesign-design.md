# News Ingestion Redesign

**Date:** 2026-04-14
**Status:** Design

## Problem

The news analyst is broken in LLM-mode backtests and expensive in live mode.

In backtest mode, `NewsAnalyst.analyze()` calls `data_service.get_news(symbols=[stock.symbol])`, which raises `DataUnavailableError` because no news adapter is registered. The exception is caught by `analyze_batch`, which returns a neutral `StockSignal(bullish_score=5, confidence=1)`. At 35% weight in the composite score, every stock on every rebalance gets the same uninformative news signal.

In live mode, the per-stock call pattern causes the news analyst to make one API call per screened stock — 15-20 calls per rebalance, even though the most important context (macro environment, sector trends) is identical across stocks. This is both expensive and conceptually wrong: a real analyst reads market and sector news once, then applies that lens to individual stocks.

A secondary bug in `YahooFinanceAdapter.get_news()` assigns the provider display name to the `author` field and hardcodes `source="yahoo_finance"`. The news skill prompt asks the LLM to weight articles by source quality, but the `source` field carries no useful information.

## Goals

1. Replace per-stock news fetching with market + sector news fetching in both live and backtest modes.
2. Keep news-fetching cheap: one market call + one call per sector per rebalance, not per stock.
3. Make the system extensible so a new data source can be plugged in without touching the analyst, graph, skill prompts, or context formatter.
4. Support manually-inserted articles (additive, file-based) for both live and backtest.
5. Fix the `YahooFinanceAdapter` source/author bug.

## Non-Goals

- Selecting a specific news data provider. The design accommodates any provider that implements the adapter interface; the choice of provider is deferred.
- Synthesizing news from price or fundamentals data. If no real news source is available, the news analyst receives no articles and says so.
- Changing composite score weights. Fundamentals 0.40 / news 0.35 / technical 0.25 stay the same across modes.
- Changing the news skill prompts or branch overlays.

## Architecture

### Adapter Interface

`NewsAdapter` (`app/common/interfaces/news.py`) replaces the per-symbol method with two scoped methods:

```python
class NewsAdapter(ABC):
    @abstractmethod
    async def get_market_news(
        self,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]: ...

    @abstractmethod
    async def get_sector_news(
        self,
        sector: str,
        since: date | None = None,
        limit: int = 20,
    ) -> list[NewsArticle]: ...
```

`YahooFinanceAdapter.get_news(symbols=...)` is retained for backward compatibility but is no longer called by the equities pipeline.

`DataPlatformService` (`app/modules/data_platform/service.py`) gets matching pass-through methods that route to the `"news"` key of the adapter registry. Behavior mirrors `get_prices` / `get_metrics`: iterate adapters in order, return on first success, raise `DataUnavailableError` if none succeed.

### Provider Abstraction

Each adapter translates the canonical `UniverseStock.sector` value (e.g., `"Technology"`) into whatever its provider expects. A lightweight helper in `app/modules/data_platform/adapters/news_base.py` provides a `SectorMapping` pattern so providers don't leak sector-name details to callers.

To swap providers, create a class implementing `NewsAdapter` and register it in `adapter_registry["news"]["all"]`. No other code changes are required.

### Graph Change

`graph.py` gains a `prefetch_news` node between `screen_stocks` and the analyst fan-out:

```
screen_stocks → prefetch_news → [news_analysis, fundamentals_analysis, technical_analysis]
```

`prefetch_news` does:

1. Compute unique sectors from `state["screened"]`.
2. Call `data_service.get_market_news(since=rebalance_date - window)` once.
3. Call `data_service.get_sector_news(sector, since=...)` once per unique sector.
4. Load manual articles from `data/news/manual/` within the rebalance window.
5. Populate `state["news_context"]`:

```python
{
    "market": [NewsArticle, ...],
    "sectors": {"Technology": [...], "Healthcare": [...]},
    "manual": [NewsArticle, ...],
}
```

`EquitiesWorkflowState` gets a `news_context: dict` field.

### Analyst Signature Change

`NewsAnalyst.analyze()` accepts pre-fetched articles and stops calling `data_service`:

```python
async def analyze(self, stock: UniverseStock, articles: list[dict]) -> StockSignal: ...
```

The `news_analysis` graph node assembles `articles` before calling the analyst by merging:
- `news_context["market"]`
- `news_context["sectors"].get(stock.sector, [])`
- Manual articles scoped to `"market"` or to the stock's sector

Articles flow through the existing `format_news_context()` unchanged. The skill prompts (base + branch overlays) work without modification.

### Manual Articles

**Directory:** `data/news/manual/`

**File format:** `{YYYY-MM-DD}.json`, one file per date:

```json
[
  {
    "title": "Fed holds rates steady, signals cuts later this year",
    "source": "Reuters",
    "published_at": "2026-03-15T14:00:00Z",
    "scope": "market"
  },
  {
    "title": "Semiconductor stocks rally on AI chip demand forecasts",
    "source": "Bloomberg",
    "published_at": "2026-03-14T09:30:00Z",
    "scope": "Technology"
  }
]
```

**Required fields:** `title`, `source`, `published_at`, `scope`. `scope` is either `"market"` or a sector name matching `UniverseStock.sector` values.

**Optional fields:** `author`, `url`, `symbols`, `sentiment` — same as `NewsArticle`.

**Windowing:** On a rebalance date, the loader globs `data/news/manual/*.json`, parses dates from filenames, and includes any file whose date falls within the rebalance window. For weekly rebalancing on 2026-03-15, files from `2026-03-09.json` through `2026-03-15.json` are loaded. The window size matches the configured rebalance frequency.

Manual articles are **additive** — they merge with whatever the provider returns. They don't override.

### Backtest Adapter

`BacktestNewsAdapter` implements the `NewsAdapter` interface and returns empty lists for both methods. It's a placeholder that gets swapped out when a real historical news provider is plugged in.

Backtest behavior:
- **Without manual articles:** No articles are returned. `format_news_context()` renders "No recent news available". The LLM applies the skill prompt heuristic ("absence of news is mildly positive for established companies") and produces a low-confidence neutral score with actual reasoning — different from the current broken path that catches an exception and returns a hardcoded fallback.
- **With manual articles:** Articles from `data/news/manual/` within the window flow through normally. The system works identically to live mode.

### Source Quality Fix

In `YahooFinanceAdapter.get_news()` (lines 273-281):

```python
# Before
author=content.get("provider", {}).get("displayName"),
source="yahoo_finance",

# After
author=None,
source=content.get("provider", {}).get("displayName") or "Yahoo Finance",
```

Provider display name moves to `source`; `author` is `None` since Yahoo Finance doesn't return individual author names. `format_news_context()` at line 578 is left as-is — it handles both cases gracefully.

## File Changes

### New files

| File | Purpose |
|------|---------|
| `app/modules/backtest/adapters/backtest_news.py` | `BacktestNewsAdapter` — empty placeholder |
| `app/modules/data_platform/adapters/manual_news.py` | Manual article loader (glob, parse, window filter) |
| `app/modules/data_platform/adapters/news_base.py` | `SectorMapping` helper for provider adapters |
| `data/news/manual/.gitkeep` | Empty directory marker |
| `tests/unit/data_platform/test_manual_news.py` | Tests for loader |
| `tests/unit/backtest/test_backtest_news_adapter.py` | Tests for backtest adapter |
| `tests/unit/equities/test_news_prefetch.py` | Tests for the graph prefetch node |

### Modified files

| File | Change |
|------|--------|
| `app/common/interfaces/news.py` | `NewsAdapter` interface — add `get_market_news`, `get_sector_news`; old `get_news` removed from abstract base |
| `app/modules/data_platform/service.py` | Add `get_market_news`, `get_sector_news` methods |
| `app/modules/data_platform/adapters/yahoo_finance.py` | Implement new methods; fix source/author bug |
| `app/modules/equities/agents/graph.py` | Add `prefetch_news` node and edge; add `news_context` to state |
| `app/modules/equities/agents/news_analyst.py` | `analyze()` takes articles param; `analyze_batch()` receives and distributes articles; `data_service` dependency removed (no longer fetches) |
| `app/modules/backtest/context.py` | Register `BacktestNewsAdapter` under `"news"` key |

### Unchanged

- `format_news_context()` in `context_formatters.py`
- News skill prompts (`base/news.md`, `branches/growth/news.md`, `branches/value/news.md`)
- `portfolio_manager.py`, weights in `config.py`
- Fundamentals and technical analyst paths

## Testing Strategy

Following TDD discipline:

1. **Interface tests** — `NewsAdapter` contract tests verify both adapters satisfy the same behavior (return empty lists when no data, respect `since` and `limit`, return `NewsArticle` objects).
2. **Manual loader tests** — date parsing, window filtering, malformed file handling, missing directory, empty directory.
3. **Prefetch node tests** — correct sector extraction from screened stocks; correct number of calls (1 market + N sectors); correct merge into state; handles empty universe.
4. **Analyst signature tests** — verify `analyze()` uses passed-in articles and does not call `data_service`.
5. **YahooFinanceAdapter bug fix test** — verify `source` carries provider name, `author` is `None`.
6. **Backtest adapter test** — verify it returns empty lists (until a real provider is plugged in).

No integration test changes needed — the existing e2e equities test exercises the full pipeline and will validate the wiring.

## Extensibility

Adding a new news provider:

1. Create `app/modules/data_platform/adapters/{provider}_news.py` with a class implementing `NewsAdapter`.
2. Register it in the composition root (`app/dependencies.py` for live mode, `app/modules/backtest/context.py` for backtest).
3. If the provider uses non-standard sector names, use `SectorMapping` to translate.

No changes required to the graph, the analyst, the skill prompts, the context formatter, the portfolio manager, or the config. The new provider is picked up automatically.

A config-driven selector (`news_provider` field in `EquitiesConfig`) can be added when there's more than one provider to choose between. For now, the composition root wires one adapter directly.

## Out of Scope

- Historical news API integration — deferred until a provider is selected.
- Weight adjustments or per-mode configuration — not needed; the same weights work in both modes because both modes receive real (or explicitly absent) news.
- Aggregating multiple providers in parallel — the registry supports it via ordered fallback, but a multi-source aggregation strategy (deduplication across sources, source-quality weighting at the adapter level) is deferred.
