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
