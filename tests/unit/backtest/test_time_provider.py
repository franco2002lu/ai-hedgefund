"""Tests for TimeProvider ABC and implementations."""

from datetime import UTC, date, datetime

import pytest

from app.common.interfaces.time import TimeProvider
from app.modules.backtest.time_provider import BacktestTimeProvider, LiveTimeProvider


class TestTimeProviderABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            TimeProvider()


class TestLiveTimeProvider:
    def test_today_returns_current_date(self):
        provider = LiveTimeProvider()
        assert provider.today() == date.today()

    def test_now_returns_utc_datetime(self):
        provider = LiveTimeProvider()
        now = provider.now()
        assert now.tzinfo is not None
        # Should be within 2 seconds of actual UTC now
        delta = abs((datetime.now(UTC) - now).total_seconds())
        assert delta < 2.0

    def test_implements_time_provider_abc(self):
        assert isinstance(LiveTimeProvider(), TimeProvider)


class TestBacktestTimeProvider:
    def test_today_returns_initial_date(self):
        provider = BacktestTimeProvider(date(2024, 3, 15))
        assert provider.today() == date(2024, 3, 15)

    def test_now_returns_datetime_at_market_close(self):
        provider = BacktestTimeProvider(date(2024, 3, 15))
        now = provider.now()
        assert now.hour == 16
        assert now.minute == 0

    def test_now_returns_utc(self):
        provider = BacktestTimeProvider(date(2024, 3, 15))
        assert provider.now().tzinfo is not None

    def test_advance_to_updates_date(self):
        provider = BacktestTimeProvider(date(2024, 3, 15))
        provider.advance_to(date(2024, 6, 1))
        assert provider.today() == date(2024, 6, 1)

    def test_advance_to_updates_now(self):
        provider = BacktestTimeProvider(date(2024, 3, 15))
        provider.advance_to(date(2024, 6, 1))
        assert provider.now().date() == date(2024, 6, 1)

    def test_advance_backwards_raises(self):
        provider = BacktestTimeProvider(date(2024, 6, 1))
        with pytest.raises(ValueError, match="[Cc]annot advance backwards"):
            provider.advance_to(date(2024, 3, 15))

    def test_implements_time_provider_abc(self):
        assert isinstance(BacktestTimeProvider(date(2024, 1, 1)), TimeProvider)
