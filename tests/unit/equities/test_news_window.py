"""Unit tests for news_window.window_days_for_frequency."""

import pytest

from app.modules.equities.news_window import window_days_for_frequency


@pytest.mark.parametrize(
    "freq,expected",
    [
        ("daily", 1),
        ("weekly", 7),
        ("biweekly", 14),
        ("monthly", 30),
    ],
)
def test_known_frequencies(freq, expected):
    assert window_days_for_frequency(freq) == expected


def test_unknown_frequency_defaults_to_weekly():
    assert window_days_for_frequency("quarterly") == 7
    assert window_days_for_frequency("") == 7
    assert window_days_for_frequency(None) == 7
