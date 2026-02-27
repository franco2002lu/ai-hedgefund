from abc import ABC, abstractmethod
from datetime import date

from pydantic import BaseModel


class PriceBar(BaseModel):
    timestamp: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class PriceDataAdapter(ABC):
    @abstractmethod
    async def get_prices(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> list[PriceBar]: ...

    @abstractmethod
    def supported_asset_classes(self) -> list[str]: ...
