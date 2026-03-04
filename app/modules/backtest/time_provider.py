from datetime import UTC, date, datetime, time

from app.common.interfaces.time import TimeProvider


class LiveTimeProvider(TimeProvider):
    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return date.today()


class BacktestTimeProvider(TimeProvider):
    def __init__(self, initial_date: date) -> None:
        self._current_date = initial_date

    def now(self) -> datetime:
        return datetime.combine(self._current_date, time(16, 0), tzinfo=UTC)

    def today(self) -> date:
        return self._current_date

    def advance_to(self, new_date: date) -> None:
        if new_date < self._current_date:
            raise ValueError("Cannot advance backwards")
        self._current_date = new_date
