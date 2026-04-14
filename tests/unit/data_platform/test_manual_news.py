"""Unit tests for ManualNewsLoader."""

import json
from datetime import date
from pathlib import Path

from app.modules.data_platform.manual_news import ManualNewsLoader


def _write_day(root: Path, day: str, articles: list[dict]) -> None:
    (root / f"{day}.json").write_text(json.dumps(articles))


def test_returns_empty_when_directory_missing(tmp_path):
    loader = ManualNewsLoader(root=tmp_path / "nonexistent")
    assert loader.load(reference_date=date(2026, 4, 14), window_days=7) == []


def test_returns_empty_when_no_files(tmp_path):
    loader = ManualNewsLoader(root=tmp_path)
    assert loader.load(reference_date=date(2026, 4, 14), window_days=7) == []


def test_loads_articles_within_window(tmp_path):
    _write_day(
        tmp_path,
        "2026-04-10",
        [
            {"title": "A", "source": "Reuters", "published_at": "2026-04-10T10:00:00Z", "scope": "market"},
        ],
    )
    _write_day(
        tmp_path,
        "2026-04-14",
        [
            {"title": "B", "source": "Bloomberg", "published_at": "2026-04-14T10:00:00Z", "scope": "Technology"},
        ],
    )
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    titles = sorted(a["title"] for a in articles)
    assert titles == ["A", "B"]


def test_excludes_articles_outside_window(tmp_path):
    _write_day(
        tmp_path,
        "2026-04-01",
        [
            {"title": "Old", "source": "Reuters", "published_at": "2026-04-01T10:00:00Z", "scope": "market"},
        ],
    )
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []


def test_excludes_future_dates(tmp_path):
    _write_day(
        tmp_path,
        "2026-04-20",
        [
            {"title": "Future", "source": "Reuters", "published_at": "2026-04-20T10:00:00Z", "scope": "market"},
        ],
    )
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []


def test_skips_malformed_files(tmp_path):
    (tmp_path / "2026-04-14.json").write_text("{not-valid-json")
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []


def test_skips_invalid_filenames(tmp_path):
    _write_day(
        tmp_path,
        "not-a-date",
        [
            {"title": "X", "source": "R", "published_at": "2026-04-14T10:00:00Z", "scope": "market"},
        ],
    )
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []


def test_required_fields_enforced(tmp_path):
    # Missing "scope" → article dropped
    (tmp_path / "2026-04-14.json").write_text(
        json.dumps(
            [
                {"title": "No scope", "source": "R", "published_at": "2026-04-14T10:00:00Z"},
            ]
        )
    )
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []


def test_inclusive_boundaries(tmp_path):
    # Day == reference_date → included
    _write_day(
        tmp_path,
        "2026-04-14",
        [
            {"title": "Today", "source": "R", "published_at": "2026-04-14T10:00:00Z", "scope": "market"},
        ],
    )
    # Day == reference_date - window_days → included
    _write_day(
        tmp_path,
        "2026-04-07",
        [
            {"title": "Edge", "source": "R", "published_at": "2026-04-07T10:00:00Z", "scope": "market"},
        ],
    )
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    titles = sorted(a["title"] for a in articles)
    assert titles == ["Edge", "Today"]


def test_non_array_json_is_skipped(tmp_path):
    (tmp_path / "2026-04-14.json").write_text(json.dumps({"not": "a list"}))
    loader = ManualNewsLoader(root=tmp_path)

    articles = loader.load(reference_date=date(2026, 4, 14), window_days=7)

    assert articles == []
