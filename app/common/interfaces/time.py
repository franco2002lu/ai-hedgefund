from abc import ABC, abstractmethod
from datetime import date, datetime


class TimeProvider(ABC):
    @abstractmethod
    def now(self) -> datetime: ...

    @abstractmethod
    def today(self) -> date: ...
