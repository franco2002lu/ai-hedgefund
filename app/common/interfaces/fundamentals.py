from abc import ABC, abstractmethod
from datetime import date


class FundamentalsAdapter(ABC):
    @abstractmethod
    async def get_metrics(
        self,
        symbol: str,
        end_date: date,
        period: str = "ttm",
        limit: int = 10,
    ) -> list[dict]: ...

    @abstractmethod
    async def search_line_items(
        self,
        symbol: str,
        items: list[str],
        end_date: date,
        period: str = "ttm",
        limit: int = 10,
    ) -> list[dict]: ...

    @abstractmethod
    async def get_company_facts(self, symbol: str) -> dict: ...
