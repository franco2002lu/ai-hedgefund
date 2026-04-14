"""ManualNewsLoader — loads date-scoped article files from data/news/manual/*.json.

File format: {YYYY-MM-DD}.json — an array of article dicts. Required fields:
title, source, published_at, scope. Optional: author, url, symbols, sentiment.

On a given reference date, files with dates in the window
[reference_date - window_days, reference_date] are loaded. Malformed files,
invalid filenames, and articles missing required fields are dropped silently
with a debug/warning log — additive inserts must never break a pipeline run.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
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
