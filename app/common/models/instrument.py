from pydantic import BaseModel

from app.common.enums import AssetClass


class Instrument(BaseModel):
    id: str
    symbol: str
    name: str
    asset_class: AssetClass
    exchange: str | None = None
    currency: str = "USD"
    is_active: bool = True
    metadata: dict | None = None
