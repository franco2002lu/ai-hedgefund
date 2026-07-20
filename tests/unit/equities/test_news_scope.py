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
        assert not is_company_relevant("P&G raises dividend for 70th year", "NVDA", "NVIDIA Corp")

    def test_two_char_ticker_token_matches(self):
        assert is_company_relevant("GM posts record EV sales", "GM", "General Motors Co")

    def test_single_letter_ticker_matches_via_name(self):
        assert is_company_relevant("Ford Motor recalls trucks", "F", "Ford Motor Co")

    def test_dotted_ticker_token_matches(self):
        assert is_company_relevant("BRK.B climbs after earnings", "BRK.B", "Berkshire Hathaway Inc")
        assert not is_company_relevant("XBRK.B fund note", "BRK.B", "ZZZ Unrelated")

    def test_slash_ticker_falls_back_to_name(self):
        assert is_company_relevant("Berkshire Hathaway boosts buyback", "BRK/B", "Berkshire Hathaway Inc")


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


class TestTagScope:
    def test_tags_and_copies(self):
        original = [{"title": "x"}]
        tagged = tag_scope(original, "company")
        assert tagged == [{"title": "x", "scope": "company"}]
        assert "scope" not in original[0]  # copies, not mutation

    def test_overwrites_existing_scope(self):
        assert tag_scope([{"title": "x", "scope": "Technology"}], "manual") == [{"title": "x", "scope": "manual"}]


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

    def test_cap_dropped_company_story_still_renders_under_lower_scope(self):
        company = _art("Chip rally", "same-url", "company")
        sector = _art("Chip rally", "same-url", "sector")
        merged = merge_and_dedupe([], [company], [sector], [], company_cap=0)
        assert merged == [sector]

    def test_published_at_accepts_datetime_objects(self):
        from datetime import UTC, datetime

        old = {"title": "old", "url": "u1", "scope": "company", "published_at": datetime(2026, 7, 1, tzinfo=UTC)}
        new = {"title": "new", "url": "u2", "scope": "company", "published_at": datetime(2026, 7, 9, tzinfo=UTC)}
        merged = merge_and_dedupe([], [old, new], [], [], company_cap=1)
        assert [a["title"] for a in merged] == ["new"]

    def test_published_at_naive_string_treated_as_utc(self):
        old = _art("old", "u1", "company", "2026-07-01T00:00:00")
        new = _art("new", "u2", "company", "2026-07-09T00:00:00Z")
        merged = merge_and_dedupe([], [old, new], [], [], company_cap=1)
        assert [a["title"] for a in merged] == ["new"]

    def test_deduped_company_article_does_not_consume_cap(self):
        manual = _art("Story", "same-url", "manual")
        dup_company = _art("Story", "same-url", "company")
        fresh_company = _art("Fresh", "u9", "company")
        merged = merge_and_dedupe([manual], [dup_company, fresh_company], [], [], company_cap=1)
        assert [a["title"] for a in merged] == ["Story", "Fresh"]
