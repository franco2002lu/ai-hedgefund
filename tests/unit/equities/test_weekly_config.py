"""Tests for weekly-pipeline env var parsing in app.config.Settings."""

import pytest

from app.config import Settings


def test_top_n_defaults_to_50(monkeypatch):
    monkeypatch.delenv("HEDGE_EQUITIES_TOP_N", raising=False)
    s = Settings()
    assert s.equities_top_n == 50


def test_top_n_parses_env(monkeypatch):
    monkeypatch.setenv("HEDGE_EQUITIES_TOP_N", "20")
    s = Settings()
    assert s.equities_top_n == 20


def test_enabled_branches_default(monkeypatch):
    monkeypatch.delenv("HEDGE_EQUITIES_ENABLED_BRANCHES", raising=False)
    s = Settings()
    assert s.equities_enabled_branches == ["growth", "value"]


def test_enabled_branches_parses_comma_split(monkeypatch):
    monkeypatch.setenv("HEDGE_EQUITIES_ENABLED_BRANCHES", "growth")
    s = Settings()
    assert s.equities_enabled_branches == ["growth"]


def test_enabled_branches_trims_whitespace(monkeypatch):
    monkeypatch.setenv("HEDGE_EQUITIES_ENABLED_BRANCHES", "growth, value")
    s = Settings()
    assert s.equities_enabled_branches == ["growth", "value"]


def test_enabled_branches_rejects_unknown_name(monkeypatch):
    monkeypatch.setenv("HEDGE_EQUITIES_ENABLED_BRANCHES", "growth,typo")
    with pytest.raises(ValueError, match="Unknown branches"):
        Settings()
