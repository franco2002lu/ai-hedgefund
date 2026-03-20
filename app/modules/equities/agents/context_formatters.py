"""Context normalization layer for LLM analyst agents.

Converts raw DataPlatformService output into structured markdown tables
that LLMs can reason over effectively. Pure functions — no state, no DI.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

# ---------------------------------------------------------------------------
# Fundamentals metric definitions: (metric_key, display_label, format_type)
# ---------------------------------------------------------------------------

_FUNDAMENTALS_SECTIONS: dict[str, list[tuple[str, str, str]]] = {
    "valuation": [
        ("pe_ratio", "P/E (TTM)", "ratio"),
        ("forward_pe", "Forward P/E", "ratio"),
        ("peg_ratio", "PEG Ratio", "ratio"),
        ("price_to_book", "P/B", "ratio"),
        ("price_to_sales", "P/S", "ratio"),
        ("enterprise_value", "EV", "dollar"),
    ],
    "profitability": [
        ("gross_margins", "Gross Margin", "pct"),
        ("operating_margins", "Operating Margin", "pct"),
        ("profit_margins", "Net Margin", "pct"),
        ("return_on_equity", "ROE", "pct"),
        ("return_on_assets", "ROA", "pct"),
    ],
    "growth": [
        ("revenue_growth", "Revenue Growth (YoY)", "pct"),
        ("earnings_growth", "Earnings Growth (YoY)", "pct"),
    ],
    "financial_health": [
        ("debt_to_equity", "Debt/Equity", "ratio"),
        ("current_ratio", "Current Ratio", "ratio"),
        ("free_cash_flow", "FCF", "dollar"),
        ("fcfYield", "FCF Yield", "pct"),
        ("operating_cash_flow", "Operating Cash Flow", "dollar"),
    ],
    "dividends_risk": [
        ("dividend_yield", "Dividend Yield", "pct"),
        ("beta", "Beta", "ratio"),
    ],
}

_SECTION_TITLES: dict[str, str] = {
    "valuation": "Valuation",
    "profitability": "Profitability",
    "growth": "Growth",
    "financial_health": "Financial Health",
    "dividends_risk": "Dividends & Risk",
}

# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------


def _fmt_number(value, fmt: str = "auto") -> str:
    """Format a number for display in markdown tables.

    Args:
        value: Number to format, or None.
        fmt: One of "auto", "pct", "ratio", "dollar".

    Returns:
        Formatted string. Returns "--" for None.
    """
    if value is None:
        return "--"

    if fmt == "pct":
        if abs(value) < 0.001:
            return "0.0%"
        return f"{value * 100:+.1f}%"

    if fmt == "ratio":
        return f"{value:.2f}"

    # "auto" and "dollar"
    prefix = "$" if fmt == "dollar" else ""
    abs_val = abs(value)

    if abs_val == 0:
        return "0"

    if abs_val >= 1e12:
        formatted = f"{prefix}{value / 1e12:.2f}T"
    elif abs_val >= 1e9:
        formatted = f"{prefix}{value / 1e9:.1f}B"
    elif abs_val >= 1e6:
        formatted = f"{prefix}{value / 1e6:.1f}M"
    else:
        formatted = f"{prefix}{value:,.0f}"

    return formatted


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _render_table(headers: list[str], rows: list[tuple]) -> str:
    """Render a markdown pipe table from headers and row tuples."""
    header_line = "| " + " | ".join(headers) + " |"
    separator = "|" + "|".join("---" for _ in headers) + "|"
    lines = [header_line, separator]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Technical indicator helpers
# ---------------------------------------------------------------------------


def _compute_sma(closes: list[float], period: int) -> float | None:
    """Simple Moving Average over the last `period` closes."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Relative Strength Index (Wilder smoothing)."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(len(closes) - period, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(values: list[float], period: int) -> list[float]:
    """Compute exponential moving average series."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    ema_vals = [sum(values[:period]) / period]
    for i in range(period, len(values)):
        ema_vals.append(values[i] * k + ema_vals[-1] * (1 - k))
    return ema_vals


def _compute_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float, float, float] | None:
    """MACD line, signal line, and histogram."""
    if len(closes) < slow + signal:
        return None
    fast_ema = _ema(closes, fast)
    slow_ema = _ema(closes, slow)
    # Align: fast_ema starts at index (fast-1), slow_ema at (slow-1)
    offset = slow - fast
    if offset >= len(fast_ema):
        return None
    macd_line_series = [
        fast_ema[i + offset] - slow_ema[i]
        for i in range(len(slow_ema))
    ]
    if len(macd_line_series) < signal:
        return None
    signal_ema = _ema(macd_line_series, signal)
    if not signal_ema:
        return None
    macd_val = macd_line_series[-1]
    signal_val = signal_ema[-1]
    histogram = macd_val - signal_val
    return (macd_val, signal_val, histogram)


def _compute_volume_trend(volumes: list[int], window: int = 20) -> dict:
    """Compare recent average volume to longer-term average."""
    if len(volumes) < window:
        return {
            "avg_volume_20d": None,
            "avg_volume_50d": None,
            "volume_ratio": None,
            "trend": "insufficient data",
        }
    avg_20 = sum(volumes[-window:]) / window
    long_window = min(50, len(volumes))
    avg_50 = sum(volumes[-long_window:]) / long_window
    ratio = avg_20 / avg_50 if avg_50 > 0 else 1.0
    if ratio > 1.1:
        trend = "above average"
    elif ratio < 0.9:
        trend = "below average"
    else:
        trend = "normal"
    return {
        "avg_volume_20d": avg_20,
        "avg_volume_50d": avg_50,
        "volume_ratio": ratio,
        "trend": trend,
    }


def _find_support_resistance(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    lookback: int = 60,
) -> dict:
    """Identify support/resistance from recent highs and lows."""
    if len(highs) < 5 or len(lows) < 5:
        return {"resistance_1": None, "support_1": None}
    n = min(lookback, len(highs))
    recent_highs = highs[-n:]
    recent_lows = lows[-n:]
    resistance_1 = max(recent_highs)
    support_1 = min(recent_lows)
    return {
        "resistance_1": resistance_1,
        "support_1": support_1,
    }


def _compute_returns(closes: list[float]) -> dict:
    """Compute returns over standard periods (1w, 1m, 3m, 6m, 12m)."""
    periods = {
        "return_1w": 5,
        "return_1m": 21,
        "return_3m": 63,
        "return_6m": 126,
        "return_12m": 252,
    }
    if len(closes) < 2:
        return {k: None for k in periods}
    current = closes[-1]
    result = {}
    for key, lookback in periods.items():
        if len(closes) > lookback:
            prev = closes[-1 - lookback]
            result[key] = (current - prev) / prev if prev != 0 else None
        else:
            result[key] = None
    return result


# ---------------------------------------------------------------------------
# format_fundamentals_context
# ---------------------------------------------------------------------------


def format_fundamentals_context(
    symbol: str,
    company_name: str,
    sector: str | None,
    metrics: dict,
    earnings: list,
    *,
    include_sections: frozenset[str] | None = None,
    sector_medians: dict | None = None,
) -> str:
    """Format fundamental data as structured markdown tables."""
    sector_display = sector or "Unknown"
    report_period = metrics.get("report_period", date.today().isoformat())
    lines = [
        f"# {symbol} — {company_name} | Fundamentals",
        f"**Sector:** {sector_display} | **Report Period:** {report_period}",
        "",
    ]

    if not metrics:
        lines.append(f"No fundamental metrics available for {symbol}.")
        return "\n".join(lines)

    has_medians = sector_medians is not None

    for section_key, metric_defs in _FUNDAMENTALS_SECTIONS.items():
        if include_sections is not None and section_key not in include_sections:
            continue

        # Build rows, skipping None-valued metrics
        rows: list[tuple] = []
        for metric_key, label, fmt in metric_defs:
            value = metrics.get(metric_key)
            if value is None:
                continue
            formatted = _fmt_number(value, fmt)
            if has_medians:
                median_val = sector_medians.get(metric_key)
                median_formatted = _fmt_number(median_val, fmt)
                rows.append((label, formatted, median_formatted))
            else:
                rows.append((label, formatted))

        if not rows:
            continue

        title = _SECTION_TITLES[section_key]
        lines.append(f"## {title}")
        if has_medians:
            lines.append(_render_table(["Metric", "Value", "Sector Median"], rows))
        else:
            lines.append(_render_table(["Metric", "Value"], rows))
        lines.append("")

    # 52-week range (special: combined into one row)
    if include_sections is None or "dividends_risk" in include_sections:
        high_52 = metrics.get("52_week_high")
        low_52 = metrics.get("52_week_low")
        if high_52 is not None and low_52 is not None:
            range_str = f"${low_52:,.2f} — ${high_52:,.2f}"
            # Append to last Dividends & Risk section if it exists
            # Find the section and add the row
            for i in range(len(lines) - 1, -1, -1):
                if "## Dividends" in lines[i]:
                    # Find the end of the table (next empty line)
                    for j in range(i + 1, len(lines)):
                        if lines[j] == "":
                            lines.insert(j, f"| 52-Week Range | {range_str} |")
                            break
                    break

    # Earnings table
    if earnings:
        lines.append("## Recent Earnings")
        rows = []
        for e in earnings:
            eps_str = f"${e.eps:.2f}" if e.eps is not None else "--"
            rev_str = _fmt_number(e.revenue, "dollar") if e.revenue is not None else "--"
            rows.append((e.fiscal_quarter, eps_str, rev_str))
        lines.append(_render_table(["Quarter", "EPS", "Revenue"], rows))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# format_technical_context
# ---------------------------------------------------------------------------


def format_technical_context(
    symbol: str,
    company_name: str,
    bars: list[dict],
    *,
    as_of_date: date | None = None,
) -> str:
    """Format price data with pre-computed indicators as markdown."""
    if not bars:
        return f"# {symbol} — {company_name} | Technical Analysis\n\nNo price data available for {symbol}."

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars]
    current_price = closes[-1]

    as_of = as_of_date or date.today()
    lines = [
        f"# {symbol} — {company_name} | Technical Analysis",
        f"**As of:** {as_of.isoformat()} | **Bars:** {len(bars)}",
        "",
    ]

    # Price Summary — multi-period returns
    returns = _compute_returns(closes)
    return_rows = []
    for label, key in [
        ("1 Week", "return_1w"),
        ("1 Month", "return_1m"),
        ("3 Months", "return_3m"),
        ("6 Months", "return_6m"),
        ("12 Months", "return_12m"),
    ]:
        val = returns.get(key)
        if val is not None:
            return_rows.append((label, _fmt_number(val, "pct")))
    if return_rows:
        lines.append("## Price Summary")
        lines.append(_render_table(["Period", "Return"], return_rows))
        lines.append("")
        lines.append(f"**Current Price:** ${current_price:,.2f}")
        lines.append("")

    # Moving Averages
    sma_rows = []
    for period_val, label in [(20, "SMA 20"), (50, "SMA 50"), (200, "SMA 200")]:
        sma = _compute_sma(closes, period_val)
        if sma is not None:
            vs_price = "above" if current_price > sma else "below"
            sma_rows.append((label, f"${sma:,.2f}", vs_price))
    if sma_rows:
        lines.append("## Moving Averages")
        lines.append(_render_table(["Indicator", "Value", "vs Price"], sma_rows))
        lines.append("")

    # Momentum
    momentum_rows = []
    rsi = _compute_rsi(closes)
    if rsi is not None:
        if rsi > 70:
            rsi_signal = "overbought"
        elif rsi < 30:
            rsi_signal = "oversold"
        else:
            rsi_signal = "neutral"
        momentum_rows.append(("RSI (14)", f"{rsi:.1f}", rsi_signal))
    macd = _compute_macd(closes)
    if macd is not None:
        macd_line, signal_line, histogram = macd
        momentum_rows.append(("MACD Line", f"{macd_line:.2f}", "—"))
        momentum_rows.append(("MACD Signal", f"{signal_line:.2f}", "—"))
        hist_signal = "bullish" if histogram > 0 else "bearish"
        momentum_rows.append(("MACD Histogram", f"{histogram:+.2f}", hist_signal))
    if momentum_rows:
        lines.append("## Momentum")
        lines.append(_render_table(["Indicator", "Value", "Signal"], momentum_rows))
        lines.append("")

    # Volume
    vol_trend = _compute_volume_trend(volumes)
    if vol_trend["avg_volume_20d"] is not None:
        vol_rows = [
            ("Avg Volume (20d)", _fmt_number(vol_trend["avg_volume_20d"], "auto")),
            ("Avg Volume (50d)", _fmt_number(vol_trend["avg_volume_50d"], "auto")),
            ("Volume Ratio", f"{vol_trend['volume_ratio']:.2f}x"),
        ]
        lines.append("## Volume")
        lines.append(_render_table(["Metric", "Value"], vol_rows))
        lines.append("")

    # Support & Resistance
    sr = _find_support_resistance(highs, lows, closes)
    if sr["resistance_1"] is not None:
        sr_rows = []
        if sr["resistance_1"] is not None:
            sr_rows.append(("Resistance 1", f"${sr['resistance_1']:,.2f}"))
        if sr["support_1"] is not None:
            sr_rows.append(("Support 1", f"${sr['support_1']:,.2f}"))
        if sr_rows:
            lines.append("## Support & Resistance")
            lines.append(_render_table(["Level", "Price"], sr_rows))
            lines.append("")

    # Recent Price Action (last 5 bars)
    recent = bars[-5:]
    price_rows = []
    for b in recent:
        ts = b.get("timestamp", "")
        # Try to format as date
        try:
            if isinstance(ts, str) and len(ts) == 10 and "-" in ts:
                d = date.fromisoformat(ts)
                ts_display = d.strftime("%b %d")
            elif isinstance(ts, date):
                ts_display = ts.strftime("%b %d")
            else:
                ts_display = str(ts)
        except (ValueError, TypeError):
            ts_display = str(ts)

        price_rows.append((
            ts_display,
            f"${b['open']:,.2f}",
            f"${b['high']:,.2f}",
            f"${b['low']:,.2f}",
            f"${b['close']:,.2f}",
            _fmt_number(b["volume"], "auto"),
        ))

    lines.append(f"## Recent Price Action ({len(recent)} Days)")
    lines.append(_render_table(
        ["Date", "Open", "High", "Low", "Close", "Volume"],
        price_rows,
    ))
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# format_news_context
# ---------------------------------------------------------------------------


def _parse_datetime(value) -> datetime | None:
    """Parse a datetime from various formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return None


def _bucket_articles(
    articles: list[dict],
    as_of: datetime | None = None,
) -> dict[str, list[dict]]:
    """Sort articles into time buckets: Last 7 Days, Last 30 Days, Older."""
    now = as_of or datetime.now(UTC)
    cutoff_7d = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)

    buckets: dict[str, list[dict]] = {
        "Last 7 Days": [],
        "Last 30 Days": [],
        "Older": [],
    }

    # Parse and sort articles by date descending
    parsed: list[tuple[datetime, dict]] = []
    for article in articles:
        pub = _parse_datetime(article.get("published_at"))
        parsed.append((pub or datetime.min.replace(tzinfo=UTC), article))
    parsed.sort(key=lambda x: x[0], reverse=True)

    for pub, article in parsed:
        if pub >= cutoff_7d:
            buckets["Last 7 Days"].append(article)
        elif pub >= cutoff_30d:
            buckets["Last 30 Days"].append(article)
        else:
            buckets["Older"].append(article)

    return buckets


def format_news_context(
    symbol: str,
    company_name: str,
    sector: str | None,
    articles: list[dict],
) -> str:
    """Format news articles as structured markdown with dates and sources."""
    sector_display = sector or "Unknown"
    lines = [
        f"# {symbol} — {company_name} | Recent News",
        f"**Sector:** {sector_display} | **Articles:** {len(articles)}",
        "",
    ]

    if not articles:
        lines.append(f"No recent news available for {symbol}.")
        return "\n".join(lines)

    # Determine reference time from the most recent article
    most_recent = None
    for a in articles:
        pub = _parse_datetime(a.get("published_at"))
        if pub and (most_recent is None or pub > most_recent):
            most_recent = pub
    buckets = _bucket_articles(articles, as_of=most_recent)

    for bucket_name, bucket_articles in buckets.items():
        if not bucket_articles:
            continue
        lines.append(f"## {bucket_name}")
        rows = []
        for a in bucket_articles:
            pub = _parse_datetime(a.get("published_at"))
            date_str = pub.strftime("%b %d") if pub else "--"
            source = a.get("author") or a.get("source", "--")
            title = a.get("title", "No title")
            rows.append((date_str, source, title))
        lines.append(_render_table(["Date", "Source", "Headline"], rows))
        lines.append("")

    return "\n".join(lines)
