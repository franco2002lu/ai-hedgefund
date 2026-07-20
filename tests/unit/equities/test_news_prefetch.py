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
    data_service.get_news = AsyncMock(return_value={"articles": [], "source": "mock"})

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
    assert ctx["company"] == {}
    deps["data_service"].get_sector_news.assert_not_awaited()
    deps["data_service"].get_news.assert_not_awaited()


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
    data_service.get_news = AsyncMock(return_value={"articles": [], "source": "mock"})

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
    deps["data_service"].get_news = AsyncMock(return_value=_company_feed("AAPL beats estimates", "Unrelated P&G story"))
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
    import asyncio

    deps = _mkdeps()
    call_order = []

    async def fake_get_news(symbols, since=None, limit=10):
        call_order.append(f"start:{symbols[0]}")
        await asyncio.sleep(0.01)
        call_order.append(f"end:{symbols[0]}")
        return _company_feed(f"{symbols[0]} update")

    deps["data_service"].get_news = AsyncMock(side_effect=fake_get_news)
    screened = [_stock("MSFT", "Technology"), _stock("AAPL", "Technology")]

    from app.modules.equities.agents.graph import _run_prefetch_news

    await _run_prefetch_news({"screened": screened, "deps": deps})

    assert call_order == ["start:AAPL", "end:AAPL", "start:MSFT", "end:MSFT"]


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
        {"title": f"story {i}", "url": f"c{i}", "scope": "company", "published_at": f"2026-04-{10 + i:02d}T10:00:00Z"}
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


async def test_articles_for_stock_sector_none_gets_market_and_symbol_manual_only():
    from app.modules.equities.agents.graph import _articles_for_stock

    news_context = {
        "market": [{"title": "Market up", "url": "m1", "published_at": "2026-04-14T10:00:00Z"}],
        "sectors": {"Technology": [{"title": "Tech leads", "url": "s1"}]},
        "company": {},
        "manual": [
            {"title": "No-scope note", "url": "x1"},
            {"title": "XYZ note", "scope": "XYZ", "url": "mt1"},
        ],
        "company_prompt_cap": 6,
    }
    stock = UniverseStock(symbol="XYZ", company_name="XYZ Inc", weight=0.01, sector=None)

    articles = _articles_for_stock(stock, news_context)

    assert [a["url"] for a in articles] == ["mt1", "m1"]  # symbol manual + market; no sector leak, no no-scope leak
