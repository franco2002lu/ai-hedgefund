from abc import ABC, abstractmethod
from datetime import date

from pydantic import BaseModel


class MacroDataPoint(BaseModel):
    date: str
    value: float


class MacroAdapter(ABC):
    @abstractmethod
    async def get_indicator(
        self,
        indicator: str,
        start_date: date,
        end_date: date,
    ) -> list[MacroDataPoint]: ...

    @abstractmethod
    def available_indicators(self) -> list[str]: ...
