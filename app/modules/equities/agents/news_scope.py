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

    Ticker rule: case-sensitive match of the symbol as a standalone token, only
    for tickers >= 2 chars (single-letter tickers like T/F would false-positive
    on ordinary words).
    Name rule: the normalized company name (>= 3 chars) appears
    case-insensitively as a substring of the title.
    """
    if len(symbol) >= 2 and re.search(rf"\b{re.escape(symbol)}\b", title):
        return True
    normalized = normalize_company_name(company_name)
    return len(normalized) >= 3 and normalized.lower() in title.lower()


def filter_company_articles(articles: list[dict], symbol: str, company_name: str) -> list[dict]:
    """Keep only articles whose title passes the company-relevance rules."""
    return [a for a in articles if is_company_relevant(a.get("title", ""), symbol, company_name)]


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
    articles are sorted newest-first and capped at company_cap after dedupe; a
    company article dropped by the cap does not claim its dedupe key, so the
    same story may still render under a lower-priority scope.
    """
    company_sorted = sorted(company, key=_published, reverse=True)
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    company_kept = 0
    for scope_list, is_company in (
        (manual, False),
        (company_sorted, True),
        (sector, False),
        (market, False),
    ):
        for article in scope_list:
            key = _dedupe_key(article)
            if key in seen:
                continue
            if is_company:
                if company_kept >= company_cap:
                    continue
                company_kept += 1
            seen.add(key)
            merged.append(article)
    return merged
