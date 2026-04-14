"""Unit tests for the prefetch_news graph node and news_analysis merge logic."""

import json
from datetime import date
from unittest.mock import AsyncMock

from app.modules.equities.models import StockSignal, UniverseStock


def _stock(sym="AAPL", sector="Technology"):
    return UniverseStock(symbol=sym, company_name=f"{sym} Inc", weight=0.05, sector=sector)


def _mkdeps(**overrides):
    data_service = AsyncMock()
    data_service.get_market_news = AsyncMock(
        return_value={
            "articles": [{"title": "Market up", "source": "Reuters", "published_at": "2026-04-14T10:00:00Z"}],
            "source": "mock",
        }
    )
    data_service.get_sector_news = AsyncMock(
        return_value={
            "articles": [{"title": "Tech leads", "source": "Bloomberg", "published_at": "2026-04-14T10:00:00Z"}],
            "source": "mock",
        }
    )

    news_analyst = AsyncMock()
    news_analyst.analyze_batch = AsyncMock(
        return_value=[
            StockSignal(symbol="AAPL", analyst_type="news", bullish_score=6, confidence=6, summary="x"),
        ]
    )

    deps = {
        "data_service": data_service,
        "news_analyst": news_analyst,
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

    from app.modules.equities.agents.graph import _run_prefetch_news

    result = await _run_prefetch_news({"screened": screened, "deps": deps})

    ctx = result["news_context"]
    deps["data_service"].get_market_news.assert_awaited_once()
    assert deps["data_service"].get_sector_news.await_count == 2
    assert set(ctx["sectors"].keys()) == {"Technology", "Financial Services"}
    assert len(ctx["market"]) == 1


async def test_prefetch_deduplicates_sectors():
    deps = _mkdeps()
    screened = [_stock("AAPL", "Technology"), _stock("MSFT", "Technology")]

    from app.modules.equities.agents.graph import _run_prefetch_news

    await _run_prefetch_news({"screened": screened, "deps": deps})

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
    (tmp_path / "2026-04-14.json").write_text(
        json.dumps(
            [
                {"title": "Fed holds", "source": "Reuters", "published_at": "2026-04-14T10:00:00Z", "scope": "market"},
                {
                    "title": "AI momentum",
                    "source": "Bloomberg",
                    "published_at": "2026-04-14T10:00:00Z",
                    "scope": "Technology",
                },
            ]
        )
    )
    deps = _mkdeps(manual_news_root=tmp_path)

    from app.modules.equities.agents.graph import _run_prefetch_news

    result = await _run_prefetch_news({"screened": [_stock()], "deps": deps})

    ctx = result["news_context"]
    titles = [a["title"] for a in ctx["manual"]]
    assert "Fed holds" in titles and "AI momentum" in titles


async def test_prefetch_no_screened_stocks():
    deps = _mkdeps()
    from app.modules.equities.agents.graph import _run_prefetch_news

    result = await _run_prefetch_news({"screened": [], "deps": deps})

    ctx = result["news_context"]
    assert ctx["market"]  # still fetched
    assert ctx["sectors"] == {}
    deps["data_service"].get_sector_news.assert_not_awaited()


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
    assert "Market up" in titles
    assert "Tech leads" in titles
    assert "Fed" in titles
    assert "Tech AI" in titles
    # Banks (Financial Services scope) must NOT leak into AAPL's Technology bundle
    assert "Banks" not in titles


async def test_news_analysis_handles_missing_news_context():
    """If news_context is absent from state, news_analysis must still work (empty fallback)."""
    deps = _mkdeps()
    screened = [_stock()]
    state = {"screened": screened, "deps": deps}

    from app.modules.equities.agents.graph import _run_news_analysis

    result = await _run_news_analysis(state)

    assert "signals" in result
    # Should have been called with empty articles_by_symbol mapping
    call_kwargs = deps["news_analyst"].analyze_batch.call_args.kwargs
    assert call_kwargs["articles_by_symbol"]["AAPL"] == []


async def test_prefetch_fetches_sectors_concurrently():
    """Multiple sectors should be fetched in parallel via asyncio.gather."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock

    from app.modules.equities.agents.graph import _run_prefetch_news

    call_order: list[str] = []

    async def slow_sector(sector, since=None, limit=20):
        call_order.append(f"start:{sector}")
        await _asyncio.sleep(0.05)
        call_order.append(f"end:{sector}")
        return {"articles": [], "source": "mock", "sector": sector}

    data_service = AsyncMock()
    data_service.get_market_news = AsyncMock(return_value={"articles": [], "source": "mock"})
    data_service.get_sector_news = AsyncMock(side_effect=slow_sector)

    deps = {
        "data_service": data_service,
        "as_of_date": None,
        "news_window_days": 7,
        "manual_news_root": None,
    }

    screened = [
        _stock("AAPL", "Technology"),
        _stock("JPM", "Financial Services"),
        _stock("XOM", "Energy"),
    ]

    await _run_prefetch_news({"screened": screened, "deps": deps})

    # If concurrent: all three "start:" entries come before any "end:" entry.
    # If serial: start:X end:X start:Y end:Y start:Z end:Z.
    starts = [i for i, x in enumerate(call_order) if x.startswith("start:")]
    ends = [i for i, x in enumerate(call_order) if x.startswith("end:")]
    assert max(starts) < min(ends), f"Sectors fetched serially, not concurrently: {call_order}"
