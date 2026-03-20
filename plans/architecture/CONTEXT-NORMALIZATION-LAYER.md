# Context Normalization Layer

## Problem Statement

The LLM analyst agents (fundamentals, technical, news) currently receive poorly formatted data, significantly reducing the quality of their reasoning. Research from production AI finance systems (e.g., Fintool) shows that LLMs reason well over markdown tables but poorly over raw dict representations, unstructured text, or CSV dumps.

**Current state of each analyst:**

| Analyst | What it sends to the LLM | What's wrong |
|---------|--------------------------|--------------|
| **Fundamentals** (`fundamentals_analyst.py:46`) | `str(metrics[0])` — raw Python dict repr of ~30 financial metrics | Unstructured, no grouping, no labels, no number formatting. LLM sees `{'pe_ratio': 25.3, 'roe': 0.18, ...}` |
| **Technical** (`technical_analyst.py:42-48`) | Last 5 bars as `Close: X, Volume: Y` bullets + single 12-month return | Discards 247 of 252 bars. No SMAs, RSI, MACD, volume trends, or support/resistance |
| **News** (`news_analyst.py:29-30`) | Raw headline titles as bullets | No dates, no source attribution, no temporal structure. LLM can't weigh recency |

## Solution: Context Normalization Layer

A new pure-function module that converts raw `DataPlatformService` output into structured markdown before it reaches the analyst agents.

### Architecture Position

```
DataPlatformService          Context Formatters          Analyst Agents
┌──────────────────┐     ┌────────────────────────┐     ┌──────────────┐
│ get_metrics()    │────>│ format_fundamentals()   │────>│ Fundamentals │
│ get_prices()     │────>│ format_technical()      │────>│ Technical    │
│ get_news()       │────>│ format_news()           │────>│ News         │
└──────────────────┘     └────────────────────────┘     └──────────────┘
     (unchanged)            (new module)                   (simplified)
```

**Key constraints:**
- `DataPlatformService` interface stays unchanged — it also serves the screener
- Formatters are pure functions (no state, no side effects, no DI wiring)
- Works identically in live and backtest modes
- `QuantitativeAnalysts` (backtest) don't use formatters — they never call LLMs

## Module Location

**New file:** `app/modules/equities/agents/context_formatters.py`

**Rationale:** The formatters exist to serve analyst agents, so they live alongside them in `agents/`. They don't belong in `data_platform/` because they perform analyst-specific presentation logic (markdown tables, indicator computation), not data access.

## Public Interface

```python
# app/modules/equities/agents/context_formatters.py

def format_fundamentals_context(
    symbol: str,
    company_name: str,
    sector: str | None,
    metrics: dict,                                    # single metrics dict from get_metrics()
    earnings: list,                                   # QuarterlyEarnings from SEC Edgar
    *,
    include_sections: frozenset[str] | None = None,   # subset: {"valuation", "profitability", ...}
    sector_medians: dict | None = None,               # optional peer comparison
) -> str:
    """Format fundamental data as structured markdown tables."""

def format_technical_context(
    symbol: str,
    company_name: str,
    bars: list[dict],                                 # bar dicts from get_prices()
    *,
    as_of_date: date | None = None,
) -> str:
    """Format price data with pre-computed indicators as markdown."""

def format_news_context(
    symbol: str,
    company_name: str,
    sector: str | None,
    articles: list[dict],                             # article dicts from get_news()
) -> str:
    """Format news with dates, sources, and time buckets as markdown."""
```

## Fundamentals Format

### Metric Organization

30+ raw metrics are organized into 5 logical sections:

| Section | Metrics | Format |
|---------|---------|--------|
| **Valuation** | P/E (TTM), Forward P/E, PEG, P/B, P/S, EV | ratios, `$` for EV |
| **Profitability** | Gross Margin, Operating Margin, Net Margin, ROE, ROA | percentages |
| **Growth** | Revenue Growth YoY, Earnings Growth YoY | signed percentages |
| **Financial Health** | Debt/Equity, Current Ratio, FCF, FCF Yield, Operating CF | mixed |
| **Dividends & Risk** | Dividend Yield, Beta, 52-Week Range | mixed |

Plus a **Recent Earnings** table when SEC Edgar data is available.

### Number Formatting

A `_fmt_number(value, fmt)` helper handles:
- `"dollar"` — `$2.85T`, `$124.3B`, `$15.2M` (smart suffixes)
- `"pct"` — `+8.1%`, `-3.4%` (signed, from raw 0.081)
- `"ratio"` — `28.50`, `1.87` (2 decimal places)
- `None` values — omitted from table entirely (saves tokens vs showing "N/A")

### Configurable Sections

`include_sections` lets branch-specific configuration control what appears:
- **Growth branch** might use: `{"growth", "profitability", "valuation"}`
- **Value branch** might use: `{"valuation", "dividends_risk", "financial_health"}`
- `None` (default) renders all sections

### Sector Medians (Future-Proofed)

When `sector_medians` is provided, an additional column appears in each table:
```
| Metric | Value | Sector Median |
|---|---|---|
| P/E (TTM) | 28.50 | 25.20 |
```
When omitted (initial implementation), the column is not rendered. The parameter exists so adding peer comparison data later requires no formatter interface change.

### Example Output

```markdown
# AAPL — Apple Inc. | Fundamentals
**Sector:** Technology | **Report Period:** 2026-03-20

## Valuation
| Metric | Value |
|---|---|
| P/E (TTM) | 28.50 |
| Forward P/E | 24.10 |
| PEG Ratio | 1.80 |
| P/B | 45.20 |
| P/S | 8.10 |
| EV | $2.85T |

## Profitability
| Metric | Value |
|---|---|
| Gross Margin | 46.2% |
| Operating Margin | 31.5% |
| Net Margin | 25.3% |
| ROE | 157.4% |
| ROA | 28.1% |

## Growth
| Metric | Value |
|---|---|
| Revenue Growth (YoY) | +8.1% |
| Earnings Growth (YoY) | +12.5% |

## Financial Health
| Metric | Value |
|---|---|
| Debt/Equity | 1.87 |
| Current Ratio | 0.99 |
| FCF | $111.4B |
| FCF Yield | 3.9% |
| Operating Cash Flow | $118.3B |

## Dividends & Risk
| Metric | Value |
|---|---|
| Dividend Yield | 0.44% |
| Beta | 1.24 |
| 52-Week Range | $164.08 — $260.10 |

## Recent Earnings
| Quarter | EPS | Revenue |
|---|---|---|
| Q4 2025 | $2.40 | $124.3B |
| Q3 2025 | $1.64 | $85.8B |
| Q2 2025 | $1.53 | $81.8B |
| Q1 2025 | $2.18 | $119.6B |
```

## Technical Format

### Pre-Computed Indicators

The formatter computes these from raw OHLCV bars so the LLM receives derived insights, not raw data:

| Indicator | Computation | Purpose |
|-----------|-------------|---------|
| **Multi-period returns** | 1w, 1m, 3m, 6m, 12m | Momentum at multiple timeframes |
| **SMA 20/50/200** | Simple moving averages | Trend identification, crossover signals |
| **RSI (14)** | Wilder smoothing | Overbought/oversold detection |
| **MACD** | 12/26/9 EMA | Momentum direction and divergence |
| **Volume trend** | 20d avg vs 50d avg ratio | Institutional activity |
| **Support/Resistance** | Pivot-based from 60-day highs/lows | Key price levels |

Each indicator includes a textual **signal** interpretation:
- RSI: "overbought" (>70), "oversold" (<30), "neutral"
- SMA: "above" / "below" relative to current price
- MACD histogram: "bullish" / "bearish"

### Graceful Degradation

When insufficient bars exist for an indicator (e.g., <200 bars for SMA 200), that row is omitted from the table. The formatter never shows "N/A" for missing indicators.

### Example Output

```markdown
# AAPL — Apple Inc. | Technical Analysis
**As of:** 2026-03-20 | **Bars:** 252

## Price Summary
| Period | Return |
|---|---|
| 1 Week | +1.2% |
| 1 Month | -3.4% |
| 3 Months | +8.7% |
| 6 Months | +15.2% |
| 12 Months | +22.1% |

**Current Price:** $245.30

## Moving Averages
| Indicator | Value | vs Price |
|---|---|---|
| SMA 20 | $242.15 | above |
| SMA 50 | $238.40 | above |
| SMA 200 | $210.75 | above |

## Momentum
| Indicator | Value | Signal |
|---|---|---|
| RSI (14) | 58.3 | neutral |
| MACD Line | 2.15 | — |
| MACD Signal | 1.80 | — |
| MACD Histogram | +0.35 | bullish |

## Volume
| Metric | Value |
|---|---|
| Avg Volume (20d) | 52.3M |
| Avg Volume (50d) | 48.1M |
| Volume Ratio | 1.09x |

## Support & Resistance
| Level | Price |
|---|---|
| Resistance 1 | $260.10 |
| Support 1 | $235.20 |

## Recent Price Action (5 Days)
| Date | Open | High | Low | Close | Volume |
|---|---|---|---|---|---|
| Mar 20 | $244.10 | $246.50 | $243.20 | $245.30 | 54.2M |
| Mar 19 | $243.80 | $245.10 | $242.00 | $244.10 | 48.7M |
| Mar 18 | $242.50 | $244.30 | $241.80 | $243.80 | 51.1M |
| Mar 17 | $241.00 | $243.20 | $240.50 | $242.50 | 45.3M |
| Mar 14 | $240.20 | $242.00 | $239.80 | $241.00 | 47.8M |
```

## News Format

### Structure

- **Time buckets**: Articles sorted chronologically and grouped into "Last 7 Days", "Last 30 Days", "Older" — helps the LLM weigh recency
- **Source attribution**: Author/publisher name in a dedicated column
- **Compact dates**: `Mon DD` format to save tokens
- **Article count in header**: Gives the LLM context on news volume (2 articles vs 20 is meaningful)
- **Maximum 20 articles**: Matches current limit, preserves token budget

### Example Output

```markdown
# AAPL — Apple Inc. | Recent News
**Sector:** Technology | **Articles:** 12

## Last 7 Days
| Date | Source | Headline |
|---|---|---|
| Mar 19 | Reuters | Apple unveils new M4 MacBook Air lineup |
| Mar 18 | Bloomberg | Apple services revenue hits record $25B quarterly run rate |
| Mar 17 | CNBC | iPhone 16 sales outpace iPhone 15 by 12% in first quarter |

## Last 30 Days
| Date | Source | Headline |
|---|---|---|
| Mar 10 | WSJ | Apple Vision Pro 2 development accelerates |
| Mar 05 | Reuters | EU antitrust probe into Apple Pay expands |
| Feb 28 | Bloomberg | Apple increases share buyback authorization by $50B |

## Older
| Date | Source | Headline |
|---|---|---|
| Feb 10 | CNBC | Warren Buffett trims Apple stake by 2% |
| Feb 05 | Reuters | Apple car project officially shelved |
```

## How Analysts Change

Each analyst's `analyze()` method is simplified — it delegates formatting to the normalizer.

### Before (fundamentals_analyst.py:46-56)
```python
metrics_str = str(metrics[0]) if metrics else "No metrics available."
earnings_str = "\n".join(...) or "No earnings data available."
prompt = (
    f"Analyze fundamentals for {stock.company_name} ({stock.symbol}).\n"
    f"Sector: {stock.sector or 'Unknown'}\n\n"
    f"Key metrics:\n{metrics_str}\n\n"
    f"Recent earnings:\n{earnings_str}\n\n"
    "Provide: bullish_score (1-10), confidence (1-10), summary (1-2 sentences)."
)
```

### After
```python
from app.modules.equities.agents.context_formatters import format_fundamentals_context

context = format_fundamentals_context(
    symbol=stock.symbol,
    company_name=stock.company_name,
    sector=stock.sector,
    metrics=metrics[0] if metrics else {},
    earnings=earnings[:4],
)
prompt = (
    f"{context}\n\n"
    "Based on the data above, provide: bullish_score (1-10), confidence (1-10), "
    "summary (1-2 sentences)."
)
```

Same pattern applies to `technical_analyst.py` and `news_analyst.py`.

## Token Budget Impact

| Analyst | Before (tokens) | After (tokens) | Delta |
|---------|-----------------|----------------|-------|
| Fundamentals | ~180 | ~400 | +220 |
| Technical | ~60 | ~400 | +340 |
| News | ~120 | ~300 | +180 |

Per pipeline run (20 stocks x 3 analysts = 60 calls): ~$0.18 additional cost at Sonnet pricing. This is negligible compared to the quality improvement from structured reasoning context.

## Backtest Compatibility

The normalization layer works with backtest mode without changes:

1. `HistoricalDataAdapter` implements the same `PriceDataAdapter` and `FundamentalsAdapter` interfaces — `DataPlatformService` returns identical dict shapes
2. Formatters accept raw dicts, never import adapter-specific types
3. `CachedAnalystWrapper` caches by `(date, symbol, analyst_type)`, independent of prompt format
4. `QuantitativeAnalysts` skip the normalization layer entirely — correct, they don't use LLMs

## Implementation Scope

### New files (2)
| File | Purpose |
|------|---------|
| `app/modules/equities/agents/context_formatters.py` | All formatters + indicator helpers |
| `tests/unit/equities/test_context_formatters.py` | Comprehensive unit tests |

### Modified files (3)
| File | Change |
|------|--------|
| `app/modules/equities/agents/fundamentals_analyst.py` | Replace `str(metrics[0])` with `format_fundamentals_context()` |
| `app/modules/equities/agents/technical_analyst.py` | Replace 5-bar summary with `format_technical_context()` |
| `app/modules/equities/agents/news_analyst.py` | Replace headline bullets with `format_news_context()` |

### Unchanged
- `DataPlatformService` — same raw dict interface
- `llm_client.py` — same system prompt and response parsing
- `QuantitativeAnalysts` — no LLM, no formatters
- `dependencies.py` — no DI changes (pure function imports)
- Screener filters — consume raw metrics directly

## Edge Cases

| Scenario | Handling |
|----------|----------|
| All metrics None (new IPO, penny stock) | Returns `"No fundamental metrics available for {symbol}."` |
| Empty price bars | Returns `"No price data available for {symbol}."` |
| <200 bars (can't compute SMA 200) | That row omitted from table |
| No news articles | Returns `"No recent news available for {symbol}."` |
| None-valued individual metrics | Row omitted from table (not shown as "N/A") |
| `published_at` missing on article | Article placed in "Older" bucket |

## Future Extensions

- **Sector medians**: When a peer comparison data source is added, pass `sector_medians` to `format_fundamentals_context()` — the interface already supports it
- **Branch-specific sections**: Wire `include_sections` to `EquitiesConfig` so growth/value branches automatically focus on relevant metrics
- **Earnings surprise highlighting**: Bold or annotate earnings that beat/miss consensus
- **Options data**: Add `format_options_context()` when options flow data becomes available
